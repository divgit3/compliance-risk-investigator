"""
embed_policy_docs.py
--------------------
Downloads policy PDFs from S3, extracts text via PyMuPDF, chunks into
overlapping windows, embeds via OpenAI text-embedding-3-small, and upserts
into the Qdrant `policy_docs` collection for RAG-based rule extraction.

Run once before Task 2.0b (business_rules_registry.py).

Usage:
  python pipelines/embed_policy_docs.py

Prerequisites:
  - Qdrant running at localhost:6333 (docker compose up -d)
  - `policy_docs` collection already created (1536-dim, Cosine)
  - OPENAI_API_KEY set in .env
  - AWS credentials set in .env
"""

import hashlib
import io
import sys
import time
from collections import defaultdict

import boto3
import fitz  # PyMuPDF
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

load_dotenv()

import os

# ── Configuration ──────────────────────────────────────────────────────────────

S3_BUCKET         = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_POLICY_PREFIX  = "raw/policy_docs/pdfs/"
QDRANT_HOST       = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "policy_docs"
EMBEDDING_MODEL   = "text-embedding-3-small"
EMBEDDING_DIM     = 1536
CHUNK_SIZE        = 512   # words per chunk
CHUNK_OVERLAP     = 64    # words overlap between consecutive chunks
EMBED_BATCH_SIZE  = 100   # max texts per OpenAI embeddings call
QDRANT_BATCH_SIZE = 50    # points per Qdrant upsert call
MAX_RETRIES       = 3
BASE_BACKOFF      = 2.0   # seconds; doubled on each retry

POLICY_DOC_METADATA = {
    "cms_open_payments_data_dictionary.pdf": {
        "doc_id":        "DOC_001",
        "doc_type":      "cms_reference",
        "authority":     "CMS",
        "relevant_rules": ["reporting", "payment_classification"],
    },
    "nova_pharma_internal_policy_SYNTHETIC.pdf": {
        "doc_id":        "DOC_002",
        "doc_type":      "company_policy",
        "authority":     "Nova Pharma",
        "relevant_rules": ["meal_limits", "fmv", "annual_cap",
                           "speaker_fees", "attestation"],
    },
    "oig_cpg_pharmaceutical.pdf": {
        "doc_id":        "DOC_003",
        "doc_type":      "regulatory_guidance",
        "authority":     "OIG",
        "relevant_rules": ["compliance_program", "risk_areas",
                           "hcp_interactions"],
    },
    "oig_speaker_fraud_alert_2020.pdf": {
        "doc_id":        "DOC_004",
        "doc_type":      "fraud_alert",
        "authority":     "OIG",
        "relevant_rules": ["speaker_programs", "low_attendance",
                           "venue_cost", "repeat_speakers"],
    },
    "phrma_code_2022.pdf": {
        "doc_id":        "DOC_005",
        "doc_type":      "industry_code",
        "authority":     "PhRMA",
        "relevant_rules": ["meal_limits", "fmv", "consulting_fees",
                           "speaker_fees", "educational_items"],
    },
}

# ── Clients (initialised at module level; fail fast on bad credentials) ────────

s3     = boto3.client("s3")
openai = OpenAI()
qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


# ── Functions ──────────────────────────────────────────────────────────────────

def _merge_word_bboxes_to_lines(
    word_bboxes: list[tuple[float, float, float, float]],
    y_tolerance: float = 3.0,
) -> list[tuple[float, float, float, float]]:
    """
    Merge word-level bboxes that share the same text line into single line bboxes.
    Words are considered on the same line if their y0 values are within y_tolerance pts.
    """
    if not word_bboxes:
        return []
    sorted_bboxes = sorted(word_bboxes, key=lambda b: (b[1], b[0]))
    lines = []
    cx0, cy0, cx1, cy1 = sorted_bboxes[0]
    for x0, y0, x1, y1 in sorted_bboxes[1:]:
        if abs(y0 - cy0) <= y_tolerance:
            cx0 = min(cx0, x0)
            cx1 = max(cx1, x1)
            cy1 = max(cy1, y1)
        else:
            lines.append((cx0, cy0, cx1, cy1))
            cx0, cy0, cx1, cy1 = x0, y0, x1, y1
    lines.append((cx0, cy0, cx1, cy1))
    return lines


def download_pdf_from_s3(bucket: str, key: str) -> bytes:
    """Download PDF bytes from S3. Returns raw bytes."""
    filename = key.split("/")[-1]
    logger.info(f"Downloading s3://{bucket}/{key}")
    obj = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()
    logger.info(f"  {filename}: {len(data):,} bytes")
    return data


def extract_text_from_pdf(pdf_bytes: bytes, filename: str) -> list[dict]:
    """
    Extract text page-by-page using PyMuPDF.
    Skips pages with fewer than 50 characters (blank or header-only pages).
    Returns list of {page_num, text, filename}.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    total_chars = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if len(text) < 50:
            continue
        # Word-level extraction: (x0, y0, x1, y1, word, block, line, word_no)
        # Used for bbox-aware chunking; skip empty tokens.
        raw_words = page.get_text("words")
        words_data = [
            (float(x0), float(y0), float(x1), float(y1), w)
            for x0, y0, x1, y1, w, *_ in raw_words
            if w.strip()
        ]
        pages.append({
            "page_num":   page_num + 1,  # 1-indexed for human readability
            "text":       text,
            "filename":   filename,
            "words_data": words_data,    # [(x0,y0,x1,y1,word), ...]
        })
        total_chars += len(text)

    doc.close()
    logger.info(
        f"  {filename}: {len(pages)} pages with text, {total_chars:,} chars total"
    )
    return pages


def chunk_text(
    pages: list[dict],
    filename: str,
    doc_metadata: dict,
) -> list[dict]:
    """
    Split extracted page text into overlapping word-window chunks.

    Strategy: flatten all page text into a single word list (preserving
    page-of-origin per word), then slide a window of CHUNK_SIZE words
    with CHUNK_OVERLAP words of carry-over. Each chunk records the page
    number where it started.

    Returns list of chunk dicts with full metadata.
    """
    doc_id   = doc_metadata["doc_id"]
    doc_type = doc_metadata["doc_type"]
    authority = doc_metadata["authority"]
    relevant_rules = doc_metadata["relevant_rules"]

    # Build section heading lookup: page_num → first heading-like line on that page.
    # Heuristic: first non-empty line < 80 chars that doesn't end with punctuation.
    page_heading: dict[int, str] = {}
    for page in pages:
        heading = ""
        for line in page["text"].split("\n"):
            stripped = line.strip()
            if stripped and len(stripped) < 80 and not stripped.endswith((".", ",", ";")):
                heading = stripped
                break
        page_heading[page["page_num"]] = heading

    # Build (word, page_num, bbox) triples from word-level data.
    # words_data is [(x0,y0,x1,y1,word), ...] extracted by PyMuPDF at page level,
    # giving us exact coordinates without any runtime text search.
    word_page_pairs: list[tuple[str, int, tuple]] = []
    for page in pages:
        for x0, y0, x1, y1, word in page["words_data"]:
            word_page_pairs.append((word, page["page_num"], (x0, y0, x1, y1)))

    chunks: list[dict] = []
    total_words = len(word_page_pairs)
    chunk_index = 0
    pos = 0

    while pos < total_words:
        end = min(pos + CHUNK_SIZE, total_words)
        window = word_page_pairs[pos:end]
        text       = " ".join(w for w, _, _ in window)
        start_page = window[0][1]

        # Collect word bboxes grouped by page, then merge into line-level bboxes.
        page_word_bboxes: dict[int, list] = defaultdict(list)
        for _, pnum, bbox in window:
            page_word_bboxes[pnum].append(bbox)

        bboxes: list[dict] = []
        for pnum in sorted(page_word_bboxes):
            for x0, y0, x1, y1 in _merge_word_bboxes_to_lines(page_word_bboxes[pnum]):
                bboxes.append({
                    "x0": round(x0, 2), "y0": round(y0, 2),
                    "x1": round(x1, 2), "y1": round(y1, 2),
                    "page_num": pnum,
                })

        chunk_id = f"{doc_id}_chunk_{chunk_index:04d}"
        chunks.append({
            "chunk_id":           chunk_id,
            "doc_id":             doc_id,
            "doc_type":           doc_type,
            "authority":          authority,
            "filename":           filename,
            "page_num":           start_page,
            "section_heading":    page_heading.get(start_page, ""),
            "chunk_index":        chunk_index,
            "chunk_start_offset": pos,
            "chunk_end_offset":   end,
            "text":               text,
            "char_count":         len(text),
            "relevant_rules":     relevant_rules,
            "bboxes":             bboxes,
        })
        chunk_index += 1

        # Advance by CHUNK_SIZE - CHUNK_OVERLAP (= step size)
        pos += CHUNK_SIZE - CHUNK_OVERLAP
        # Safety: stop if we've consumed all words (avoid infinite loop when
        # the final window is smaller than the overlap)
        if end == total_words:
            break

    logger.info(f"  {filename}: {len(chunks)} chunks produced")
    return chunks


def embed_chunks(chunks: list[dict]) -> list[dict]:
    """
    Embed each chunk via OpenAI text-embedding-ada-002.
    Batches in groups of EMBED_BATCH_SIZE.
    Retries with exponential backoff on rate-limit errors (up to MAX_RETRIES).
    Adds 'embedding' key to each chunk dict in-place.
    Returns the same list with embeddings added.
    """
    total = len(chunks)
    embedded = 0

    for batch_start in range(0, total, EMBED_BATCH_SIZE):
        batch = chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
        texts = [c["text"] for c in batch]

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = openai.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                    dimensions=EMBEDDING_DIM,
                )
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    logger.error(
                        f"Embedding failed after {MAX_RETRIES} attempts "
                        f"(batch starting at chunk {batch_start}): {e}"
                    )
                    # Skip this batch — chunks remain without embeddings
                    response = None
                    break
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    f"Embedding attempt {attempt} failed: {e}. "
                    f"Retrying in {wait:.0f}s..."
                )
                time.sleep(wait)

        if response is None:
            continue

        for chunk, embedding_obj in zip(batch, response.data):
            chunk["embedding"] = embedding_obj.embedding

        embedded += len(batch)
        if embedded % 50 == 0 or embedded == total:
            logger.info(f"  Embedded {embedded}/{total} chunks")

    return chunks


def _chunk_id_to_qdrant_id(chunk_id: str) -> int:
    """Convert a string chunk_id to a stable integer Qdrant point ID via MD5."""
    md5 = hashlib.md5(chunk_id.encode()).hexdigest()
    return int(md5[:8], 16)


def recreate_qdrant_collection() -> None:
    """Drop and recreate the policy_docs collection for a clean re-embed."""
    existing = [c.name for c in qdrant.get_collections().collections]
    if QDRANT_COLLECTION in existing:
        logger.info(f"Dropping collection '{QDRANT_COLLECTION}'...")
        qdrant.delete_collection(QDRANT_COLLECTION)
    logger.info(
        f"Creating collection '{QDRANT_COLLECTION}' "
        f"({EMBEDDING_DIM}-dim, Cosine)..."
    )
    qdrant.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    logger.info("Collection created.")


def upsert_to_qdrant(chunks_with_embeddings: list[dict]) -> int:
    """
    Upsert embedded chunks into the Qdrant policy_docs collection.
    Only upserts chunks that have an 'embedding' key (skips failed embeds).
    Batches in groups of QDRANT_BATCH_SIZE.
    Returns total points upserted.
    """

    embeddable = [c for c in chunks_with_embeddings if "embedding" in c]
    total = len(embeddable)
    upserted = 0

    for batch_start in range(0, total, QDRANT_BATCH_SIZE):
        batch = embeddable[batch_start : batch_start + QDRANT_BATCH_SIZE]
        points = []
        for chunk in batch:
            payload = {k: v for k, v in chunk.items() if k != "embedding"}
            points.append(
                PointStruct(
                    id=_chunk_id_to_qdrant_id(chunk["chunk_id"]),
                    vector=chunk["embedding"],
                    payload=payload,
                )
            )

        try:
            qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
            upserted += len(points)
            logger.info(
                f"  Upserted batch {batch_start // QDRANT_BATCH_SIZE + 1}: "
                f"{upserted}/{total} points"
            )
        except Exception as e:
            logger.error(f"Qdrant upsert failed at batch {batch_start}: {e}")
            raise

    return upserted


def verify_qdrant_collection() -> dict:
    """Query Qdrant for current collection status and return a summary dict."""
    info = qdrant.get_collection(QDRANT_COLLECTION)
    summary = {
        "collection":   QDRANT_COLLECTION,
        "total_points": info.points_count,
        "vector_dim":   info.config.params.vectors.size,
        "status":       info.status.value,
    }
    logger.info(
        f"Qdrant collection '{QDRANT_COLLECTION}': "
        f"{summary['total_points']} points | "
        f"dim={summary['vector_dim']} | "
        f"status={summary['status']}"
    )
    return summary


def main() -> None:
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("embed_policy_docs.py — Policy Document Embedding Pipeline")
    logger.info("=" * 60)
    logger.info(f"S3 bucket:        {S3_BUCKET}")
    logger.info(f"S3 prefix:        {S3_POLICY_PREFIX}")
    logger.info(f"Qdrant:           {QDRANT_HOST}:{QDRANT_PORT}/{QDRANT_COLLECTION}")
    logger.info(f"Embedding model:  {EMBEDDING_MODEL}")
    logger.info(f"Chunk size:       {CHUNK_SIZE} words  (overlap: {CHUNK_OVERLAP})")
    logger.info(f"Documents:        {len(POLICY_DOC_METADATA)}")
    logger.info("")

    # Drop and recreate collection for clean re-embed with bbox payload.
    recreate_qdrant_collection()

    docs_processed  = 0
    total_chunks    = 0
    total_upserted  = 0

    for filename, doc_metadata in POLICY_DOC_METADATA.items():
        logger.info(f"── Processing: {filename} ({doc_metadata['doc_id']}) ──")
        s3_key = S3_POLICY_PREFIX + filename

        # 1. Download
        try:
            pdf_bytes = download_pdf_from_s3(S3_BUCKET, s3_key)
        except Exception as e:
            logger.error(f"  Failed to download {filename}: {e} — skipping.")
            continue

        # 2. Extract text
        try:
            pages = extract_text_from_pdf(pdf_bytes, filename)
        except Exception as e:
            logger.error(f"  Failed to extract text from {filename}: {e} — skipping.")
            continue

        if not pages:
            logger.warning(f"  No text extracted from {filename} — skipping.")
            continue

        # 3. Chunk
        chunks = chunk_text(pages, filename, doc_metadata)
        if not chunks:
            logger.warning(f"  No chunks produced for {filename} — skipping.")
            continue

        # 4. Embed
        chunks = embed_chunks(chunks)

        # 5. Upsert
        upserted = upsert_to_qdrant(chunks)

        docs_processed += 1
        total_chunks   += len(chunks)
        total_upserted += upserted
        logger.info("")

    # Final verification
    summary = verify_qdrant_collection()
    elapsed = time.time() - start_time

    logger.info("")
    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Docs processed:       {docs_processed}/{len(POLICY_DOC_METADATA)}")
    logger.info(f"  Total chunks embedded:{total_chunks}")
    logger.info(f"  Total points upserted:{total_upserted}")
    logger.info(f"  Qdrant total points:  {summary['total_points']}")
    logger.info(f"  Collection status:    {summary['status']}")
    logger.info(f"  Time taken:           {elapsed:.1f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
