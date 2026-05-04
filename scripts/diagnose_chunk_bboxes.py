"""
scripts/diagnose_chunk_bboxes.py

Diagnostic script for the bbox highlighting investigation.

Fetches 3 chunks from Qdrant and reports:
  - Raw bbox list (page, coordinates, width, height)
  - Union bbox area as % of page area
  - PDF page dimensions (from local PDF files)

Run from project root:
  python scripts/diagnose_chunk_bboxes.py

Prerequisites:
  - Qdrant running (docker compose up -d)
  - docker/.env present (or QDRANT_HOST/QDRANT_PORT in environment)
  - data/raw/policy_docs/ contains the 5 source PDFs
  - pip install qdrant-client pymupdf python-dotenv
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Load env from docker/.env so QDRANT_PORT etc. pick up host-side values ──
_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _ROOT / "docker" / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE)
    except ImportError:
        pass  # dotenv optional — env vars may already be set

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

# ── Config ────────────────────────────────────────────────────────────────────

QDRANT_HOST  = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT  = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION   = "policy_docs"
PDF_DIR      = _ROOT / "data" / "raw" / "policy_docs"

# Chunks to diagnose: [chunk_id, ...]
CHUNK_IDS = [
    "DOC_002_chunk_0001",   # nova_pharma_internal_policy_SYNTHETIC.pdf
    "DOC_004_chunk_0000",   # oig_speaker_fraud_alert_2020.pdf
    "DOC_003_chunk_0007",   # oig_cpg_pharmaceutical.pdf
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_chunk(qdrant: QdrantClient, chunk_id: str) -> dict | None:
    """Fetch a single chunk payload from Qdrant by chunk_id."""
    results, _ = qdrant.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    return results[0].payload if results else None


def get_page_dims(filename: str, page_num: int) -> tuple[float, float] | None:
    """
    Return (width_pts, height_pts) for a 1-indexed page in the given PDF.
    Returns None if the file is missing or the page number is out of range.
    """
    try:
        import fitz  # PyMuPDF
        path = PDF_DIR / filename
        if not path.is_file():
            return None
        doc = fitz.open(str(path))
        idx = page_num - 1
        if idx < 0 or idx >= doc.page_count:
            doc.close()
            return None
        rect = doc[idx].rect
        doc.close()
        return rect.width, rect.height
    except Exception as e:
        print(f"  [warn] Could not open {filename} p{page_num}: {e}")
        return None


def union_bbox(bboxes: list[dict]) -> tuple[float, float, float, float] | None:
    """Return (min_x0, min_y0, max_x1, max_y1) over the bbox list, or None."""
    if not bboxes:
        return None
    return (
        min(b["x0"] for b in bboxes),
        min(b["y0"] for b in bboxes),
        max(b["x1"] for b in bboxes),
        max(b["y1"] for b in bboxes),
    )


def bbox_area(x0: float, y0: float, x1: float, y1: float) -> float:
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def diagnose_chunk(payload: dict) -> dict:
    """
    Print a full diagnostic report for one chunk and return summary metrics.
    """
    chunk_id  = payload["chunk_id"]
    filename  = payload.get("filename", "unknown")
    page_num  = payload.get("page_num")
    text      = payload.get("text", "")
    bboxes    = payload.get("bboxes") or []

    print("=" * 72)
    print(f"chunk_id  : {chunk_id}")
    print(f"source    : {filename}")
    print(f"page_num  : {page_num}  (chunk start page, 1-indexed)")
    print(f"text_chars: {len(text)}")
    print(f"text_head : {text[:80]!r}")
    print(f"text_tail : {text[-80:]!r}")
    print(f"n_bboxes  : {len(bboxes)}")
    print()

    # ── Per-bbox dump ─────────────────────────────────────────────────────────
    pages_seen: set[int] = set()
    for i, b in enumerate(bboxes):
        x0, y0, x1, y1, bpn = b["x0"], b["y0"], b["x1"], b["y1"], b["page_num"]
        w = x1 - x0
        h = y1 - y0
        pages_seen.add(bpn)
        print(f"  [{i:03d}] page={bpn}  bbox=({x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f})"
              f"  width={w:.2f}  height={h:.2f}")

    print()

    # ── Union bbox ────────────────────────────────────────────────────────────
    ub = union_bbox(bboxes)
    if ub:
        ux0, uy0, ux1, uy1 = ub
        print(f"union_bbox: ({ux0:.2f}, {uy0:.2f}, {ux1:.2f}, {uy1:.2f})")
        print(f"  union width : {ux1 - ux0:.2f} pts")
        print(f"  union height: {uy1 - uy0:.2f} pts")
    else:
        print("union_bbox: (no bboxes)")

    # ── Page dimensions & coverage ────────────────────────────────────────────
    print()
    print("Per-page coverage:")
    summary_area_pcts: list[float] = []

    for pn in sorted(pages_seen):
        dims = get_page_dims(filename, pn)
        bboxes_on_page = [b for b in bboxes if b["page_num"] == pn]
        page_ub = union_bbox(bboxes_on_page)

        if dims:
            pw, ph = dims
            page_area = pw * ph
            print(f"  page {pn}: dims={pw:.0f}×{ph:.0f} pts  ({pw/72:.1f}×{ph/72:.1f} in)")
            if page_ub:
                ux0, uy0, ux1, uy1 = page_ub
                ub_area = bbox_area(ux0, uy0, ux1, uy1)
                pct = 100.0 * ub_area / page_area
                summary_area_pcts.append(pct)
                print(f"         bboxes_on_page={len(bboxes_on_page)}")
                print(f"         union_bbox on page=({ux0:.2f}, {uy0:.2f}, {ux1:.2f}, {uy1:.2f})")
                print(f"         union_width={ux1-ux0:.2f}  union_height={uy1-uy0:.2f}")
                print(f"         union_area={ub_area:.1f} pts²  page_area={page_area:.1f} pts²")
                print(f"         coverage={pct:.1f}% of page")
            else:
                print(f"         (no bboxes on this page)")
        else:
            print(f"  page {pn}: dims unavailable (PDF not found or page out of range)")
            if page_ub:
                ux0, uy0, ux1, uy1 = page_ub
                ub_area = bbox_area(ux0, uy0, ux1, uy1)
                pct_fallback = None
                print(f"         union_bbox=({ux0:.2f}, {uy0:.2f}, {ux1:.2f}, {uy1:.2f})")

    print()
    avg_pct = sum(summary_area_pcts) / len(summary_area_pcts) if summary_area_pcts else 0.0
    return {
        "chunk_id":    chunk_id,
        "n_bboxes":    len(bboxes),
        "chunk_chars": len(text),
        "avg_coverage_pct": avg_pct,
    }


# ── Bbox structure inspector ──────────────────────────────────────────────────

def inspect_bbox_structure(qdrant: QdrantClient, chunk_id: str) -> None:
    """
    Print the raw Qdrant payload structure for one chunk, then drill into
    the bboxes field to expose every stored field name and type.
    Also checks for intermediate pipeline files that may have been written
    before the Qdrant upsert (none expected for this project — pipeline writes
    directly to Qdrant with no intermediate serialization step).
    """
    import json

    print("=" * 72)
    print(f"inspect_bbox_structure: {chunk_id}")
    print("=" * 72)

    # ── 1. Fetch from Qdrant ──────────────────────────────────────────────────
    results, _ = qdrant.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not results:
        print(f"ERROR: chunk_id '{chunk_id}' not found in {COLLECTION}.\n")
        return

    point   = results[0]
    payload = point.payload

    # ── 2. Full payload dump (text truncated for readability) ─────────────────
    printable = {}
    for k, v in payload.items():
        if k == "text" and isinstance(v, str) and len(v) > 200:
            printable[k] = v[:200] + f"... [{len(v)} chars total]"
        elif k == "bboxes" and isinstance(v, list):
            # Summarise the list in the top-level dump; full detail below
            printable[k] = f"<list of {len(v)} items — see bbox detail below>"
        else:
            printable[k] = v

    print("\n── Full payload (text/bboxes summarised) ──")
    print(json.dumps(printable, indent=2, default=str))

    # ── 3. Bbox field deep-dive ───────────────────────────────────────────────
    bboxes = payload.get("bboxes")
    print("\n── bboxes field structure ──")
    print(f"type(bboxes)        : {type(bboxes).__name__}")

    if bboxes is None:
        print("bboxes is None — field missing or null in payload.")

    elif isinstance(bboxes, list):
        print(f"len(bboxes)         : {len(bboxes)}")

        if bboxes:
            print(f"\nFirst 3 elements (raw):")
            for i, entry in enumerate(bboxes[:3]):
                print(f"\n  bboxes[{i}]:")
                print(f"    type: {type(entry).__name__}")
                if isinstance(entry, dict):
                    for field_name, field_val in entry.items():
                        print(f"    .{field_name:<12} = {field_val!r:<20}  (type: {type(field_val).__name__})")
                else:
                    # Not a dict — print as-is
                    print(f"    value: {entry!r}")

            # Field-name inventory across ALL bbox entries (in case some have
            # extra/missing fields relative to the first 3)
            all_field_sets = [
                frozenset(b.keys()) if isinstance(b, dict) else frozenset()
                for b in bboxes
            ]
            unique_schemas = {s for s in all_field_sets}
            print(f"\nDistinct bbox schemas across all {len(bboxes)} entries:")
            for schema in sorted(unique_schemas, key=lambda s: sorted(s)):
                count = sum(1 for s in all_field_sets if s == schema)
                print(f"  {sorted(schema)}  — {count} of {len(bboxes)} entries")

            # Coordinate range summary
            if all(isinstance(b, dict) for b in bboxes):
                x0s = [b.get("x0") for b in bboxes if "x0" in b]
                y0s = [b.get("y0") for b in bboxes if "y0" in b]
                x1s = [b.get("x1") for b in bboxes if "x1" in b]
                y1s = [b.get("y1") for b in bboxes if "y1" in b]
                pns = [b.get("page_num") for b in bboxes if "page_num" in b]
                print(f"\nCoordinate ranges across all bboxes:")
                if x0s: print(f"  x0 : {min(x0s):.2f} – {max(x0s):.2f}")
                if y0s: print(f"  y0 : {min(y0s):.2f} – {max(y0s):.2f}")
                if x1s: print(f"  x1 : {min(x1s):.2f} – {max(x1s):.2f}")
                if y1s: print(f"  y1 : {min(y1s):.2f} – {max(y1s):.2f}")
                if pns: print(f"  page_num : {sorted(set(pns))}")

                widths  = [b.get("x1",0) - b.get("x0",0) for b in bboxes if "x0" in b and "x1" in b]
                heights = [b.get("y1",0) - b.get("y0",0) for b in bboxes if "y0" in b and "y1" in b]
                if widths:
                    print(f"  bbox widths  : min={min(widths):.2f}  max={max(widths):.2f}  "
                          f"mean={sum(widths)/len(widths):.2f}")
                if heights:
                    print(f"  bbox heights : min={min(heights):.2f}  max={max(heights):.2f}  "
                          f"mean={sum(heights)/len(heights):.2f}")

    elif isinstance(bboxes, dict):
        print("bboxes is a dict (unexpected — expected list).")
        print(f"keys: {list(bboxes.keys())}")
        first_val = next(iter(bboxes.values()), None)
        print(f"type of first value: {type(first_val).__name__}")
        print(f"first value: {first_val!r}")

    else:
        print(f"bboxes is unexpected type: {type(bboxes).__name__}")
        print(f"value: {bboxes!r}")

    # ── 4. Intermediate pipeline files ────────────────────────────────────────
    print("\n── Intermediate pipeline files ──")
    # The embed_policy_docs.py pipeline writes directly to Qdrant — there is no
    # intermediate serialization step. Checking data/ anyway in case a future
    # pipeline version added one.
    candidates = [
        _ROOT / "data" / "processed" / "chunks.parquet",
        _ROOT / "data" / "processed" / "chunks.json",
        _ROOT / "data" / "processed" / "policy_chunks.parquet",
        _ROOT / "data" / "processed" / "policy_chunks.json",
        _ROOT / "data" / "chunks.parquet",
        _ROOT / "data" / "chunks.json",
        _ROOT / "pipelines" / "outputs" / "chunks.parquet",
        _ROOT / "pipelines" / "outputs" / "chunks.json",
    ]
    found_any = False
    for p in candidates:
        if p.exists():
            found_any = True
            print(f"  FOUND: {p}  ({p.stat().st_size:,} bytes)")
    if not found_any:
        print("  None found. The chunking pipeline (pipelines/embed_policy_docs.py)")
        print("  writes directly to Qdrant — no intermediate chunk file is produced.")
        print("  The Qdrant payload IS the canonical chunk record.")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Confirm collection is reachable
    try:
        info = qdrant.get_collection(COLLECTION)
    except Exception as e:
        print(f"ERROR: cannot reach Qdrant at {QDRANT_HOST}:{QDRANT_PORT} — {e}")
        sys.exit(1)

    print(f"Qdrant: {QDRANT_HOST}:{QDRANT_PORT}/{COLLECTION}")
    print(f"Total points: {info.points_count}")
    print(f"PDF dir: {PDF_DIR}")
    print()

    # ── Bbox structure inspection ──────────────────────────────────────────────
    INSPECT_IDS = [
        "DOC_002_chunk_0001",   # nova_pharma — single-column A4 layout
        "DOC_003_chunk_0007",   # oig_cpg — two-column Federal Register layout
    ]
    for cid in INSPECT_IDS:
        inspect_bbox_structure(qdrant, cid)

    # ── Per-chunk diagnostic (original) ───────────────────────────────────────
    print("\n" + "=" * 72)
    print("ORIGINAL DIAGNOSTIC (union coverage per chunk)")
    print("=" * 72 + "\n")

    summaries: list[dict] = []
    for cid in CHUNK_IDS:
        payload = fetch_chunk(qdrant, cid)
        if payload is None:
            print(f"ERROR: chunk_id '{cid}' not found in collection.\n")
            continue
        summary = diagnose_chunk(payload)
        summaries.append(summary)

    # ── Summary table ─────────────────────────────────────────────────────────
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"{'chunk_id':<30} {'n_bboxes':>8}  {'chunk_chars':>11}  {'avg_bbox_union_pct':>18}")
    print("-" * 72)
    for s in summaries:
        print(f"{s['chunk_id']:<30} {s['n_bboxes']:>8}  {s['chunk_chars']:>11}  {s['avg_coverage_pct']:>17.1f}%")
    print()
    print("avg_bbox_union_pct = (union of all bboxes on page) / page_area × 100")
    print("Expected for a well-bounded chunk: 10–40% (a few paragraphs on a page).")
    print("If values are 80–100%, bboxes cover nearly the entire page — likely a data bug.")


if __name__ == "__main__":
    main()
