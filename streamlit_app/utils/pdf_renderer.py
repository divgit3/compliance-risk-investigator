"""
streamlit_app/utils/pdf_renderer.py

Server-side PDF page rasterization using PyMuPDF with optional chunk highlighting.

Policy PDFs are mounted at /app/data/raw/policy_docs/ in the Streamlit container
(read-only). Pages are rendered at 2× resolution (~144 DPI).

Caching: st.cache_data keyed on (source_doc, page_num, chunk_text). With ≤128 total
chunks the cache is bounded — no PIL overlay needed.

Highlighting uses a two-stage approach:
  Stage 1 — whitespace normalization + full-text search. Fixes the most common
             mismatch between chunk text (spaces-for-newlines, \xa0) and PDF text.
  Stage 2 — multi-substring vertical clustering. Sample 4 positions across the chunk,
             search each, find the vertical region where ≥2 match positions cluster.
             That region is where the chunk actually lives; scattered spurious matches
             at other y-positions are discarded.
If both stages fail, no highlights — wrong highlight is worse than no highlight.
"""

from __future__ import annotations

import os
import re

import streamlit as st

_PDF_DIR = "/app/data/raw/policy_docs"

# Stage 2 clustering parameters (in PDF coordinate points, 72pt = 1 inch)
_CLUSTER_MAX_GAP_PTS    = 100.0   # max vertical gap between consecutive rects in a cluster
_CLUSTER_MIN_SUBSTRINGS = 2       # unique chunk positions required to form a valid cluster
_CLUSTER_SUBSTR_LEN     = 80      # characters sampled per position
_CLUSTER_SHORT_THRESHOLD = 200    # chunks below this use 2 positions instead of 4


def _pdf_path(source_doc: str) -> str:
    return os.path.join(_PDF_DIR, source_doc)


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs (incl. \\xa0, tabs, newlines) to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _find_vertical_cluster(
    all_rects: list[tuple[int, object]],
) -> tuple[list, int]:
    """
    Find the densest vertical cluster of rectangles from multiple chunk positions.

    Sorts rects by y0 (top edge, PDF points). Sliding window expands downward while
    consecutive rects stay within _CLUSTER_MAX_GAP_PTS. Score = unique chunk-position
    indices in the window. Scattered spurious matches (headers, common phrases) produce
    low-score isolated windows and are ignored.

    Returns (cluster_rects, best_score). cluster_rects is empty if no window reaches
    _CLUSTER_MIN_SUBSTRINGS unique positions.
    """
    if not all_rects:
        return [], 0

    sorted_rects = sorted(all_rects, key=lambda x: x[1].y0)
    best_cluster: list = []
    best_score = 0

    for i, (idx_i, rect_i) in enumerate(sorted_rects):
        cluster = [(idx_i, rect_i)]
        seen = {idx_i}
        for j in range(i + 1, len(sorted_rects)):
            idx_j, rect_j = sorted_rects[j]
            gap = rect_j.y0 - cluster[-1][1].y1
            if gap <= _CLUSTER_MAX_GAP_PTS:
                cluster.append((idx_j, rect_j))
                seen.add(idx_j)
            else:
                break
        score = len(seen)
        if score >= _CLUSTER_MIN_SUBSTRINGS and score > best_score:
            best_score = score
            best_cluster = cluster

    return [r for _, r in best_cluster], best_score


def _search_two_stage(page: object, chunk_text: str) -> tuple[list, str, int]:
    """
    Two-stage highlight search. Returns (rects, status, matched_positions) where:
      status            'full' | 'clustered' | 'none'
      matched_positions  number of unique chunk positions found (4 max for stage 2)
    """
    total_len = len(chunk_text)

    # Stage 1: normalize whitespace and try full-text match.
    # Handles the most common mismatch: chunk text has spaces where PDF has \xa0,
    # tabs, or multi-space runs.
    rects = page.search_for(_normalize_ws(chunk_text))
    if rects:
        return list(rects), "full", 1  # "1 position" = full chunk

    # Stage 2: sample positions across the chunk, cluster matches vertically.
    if total_len < _CLUSTER_SHORT_THRESHOLD:
        positions = [0, total_len // 2]
        sub_len   = max(20, total_len // 2)
    else:
        positions = [
            0,
            total_len * 30 // 100,
            total_len * 60 // 100,
            total_len * 90 // 100,
        ]
        sub_len = _CLUSTER_SUBSTR_LEN

    all_rects: list[tuple[int, object]] = []
    for idx, pos in enumerate(positions):
        sub = _normalize_ws(chunk_text[pos : pos + sub_len])
        if not sub:
            continue
        for r in page.search_for(sub):
            all_rects.append((idx, r))

    cluster, best_score = _find_vertical_cluster(all_rects)
    if cluster:
        return cluster, "clustered", best_score

    return [], "none", 0


@st.cache_data(show_spinner=False)
def render_pdf_page(
    source_doc: str,
    page_num: int,
    chunk_text: str | None = None,
) -> tuple[bytes, dict] | None:
    """
    Rasterize a PDF page as PNG bytes with optional chunk highlighting.

    Args:
        source_doc: PDF filename (e.g. "nova_pharma_internal_policy_SYNTHETIC.pdf")
        page_num:   1-indexed page number (as stored in Qdrant chunk metadata)
        chunk_text: Full chunk text (newlines collapsed). When passed for a
                    continuation page, the caller should pass the tail half
                    (excerpt[len//2:]) so stage 1 searches for the continuation.

    Returns:
        (png_bytes, metadata) or None on file-read error.

    metadata keys:
        highlight_status  'full' | 'clustered' | 'none'
        rect_count        int
        chunk_continues   bool — True when the chunk likely extends to the next page
    """
    try:
        import fitz  # PyMuPDF

        path = _pdf_path(source_doc)
        if not os.path.isfile(path):
            return None

        doc = fitz.open(path)
        zero_indexed = page_num - 1
        if zero_indexed < 0 or zero_indexed >= doc.page_count:
            doc.close()
            return None

        page = doc.load_page(zero_indexed)

        meta: dict = {
            "highlight_status": "none",
            "rect_count": 0,
            "chunk_continues": False,
        }

        if chunk_text:
            total_len = len(chunk_text)
            rects, status, matched_positions = _search_two_stage(page, chunk_text)

            if rects:
                for rect in rects:
                    annot = page.add_highlight_annot(rect)
                    annot.update()

                meta["highlight_status"] = status
                meta["rect_count"] = len(rects)
                # chunk_continues heuristic:
                #   full match  → chunk is here, no continuation
                #   clustered   → found a region but may not cover the whole chunk;
                #                 assume continues if chunk is long and only a minority
                #                 of positions were clustered (< half of 4 positions)
                #   none        → no match; long chunks probably span pages
                if status == "full":
                    meta["chunk_continues"] = False
                else:
                    # matched_positions is the cluster score (unique positions found)
                    # 4 positions sampled; ≥3 in cluster ≈ chunk is mostly here
                    meta["chunk_continues"] = (
                        total_len > 300 and matched_positions < 3
                    )
            else:
                meta["chunk_continues"] = total_len > 2500

        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes, meta

    except Exception:
        return None
