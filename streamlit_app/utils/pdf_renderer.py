"""
streamlit_app/utils/pdf_renderer.py

Server-side PDF page rasterization using PyMuPDF with optional chunk highlighting.

Policy PDFs are mounted at /app/data/raw/policy_docs/ in the Streamlit container
(read-only). Pages are rendered at 2× resolution (~144 DPI).

Caching strategy (Option Y): st.cache_data hashes all parameters including chunk_text.
With ≤128 total chunks in the corpus the cache is bounded — no PIL overlay layer needed.
Highlights are applied as yellow PyMuPDF annotations before rasterization.
"""

from __future__ import annotations

import os

import streamlit as st

_PDF_DIR = "/app/data/raw/policy_docs"

# Fallback substring lengths tried in order when full-text search_for() fails.
# PyMuPDF normalizes whitespace (space/newline/tab treated equivalently), so the
# space-collapsed chunk text generally matches the newline-separated PDF text.
_SEARCH_FALLBACK_LENGTHS = (200, 100, 60)


def _pdf_path(source_doc: str) -> str:
    return os.path.join(_PDF_DIR, source_doc)


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
        chunk_text: Full chunk text for highlight search (newlines collapsed to spaces).
                    If None, page renders without highlighting.

    Returns:
        (png_bytes, metadata) or None on file-read error.

    metadata keys:
        highlight_status  'full' | 'fallback_Nchars' | 'none'
        rect_count        int — number of highlight rectangles drawn
        chunk_continues   bool — heuristic: True if less than 50% of the chunk text
                          was locatable on this page, suggesting continuation on next page

    Highlighting: cascading fallback — full chunk → first 200 → 100 → 60 chars.
    Yellow annotations are added to the PyMuPDF page object before get_pixmap().
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
            rects = []
            matched_len = 0

            # Cascading fallback: full text first, then progressively shorter substrings.
            candidates = [(chunk_text, total_len)] + [
                (chunk_text[:n], n)
                for n in _SEARCH_FALLBACK_LENGTHS
                if n < total_len
            ]
            for candidate_text, candidate_len in candidates:
                found = page.search_for(candidate_text.strip())
                if found:
                    # Full-text match: keep all rects (one rect per text row of the
                    # matched block). Fallback match: keep only the first occurrence —
                    # short prefixes can appear multiple times on the page (headers,
                    # repeated phrases), producing scattered highlights unrelated to
                    # the chunk. The first occurrence is where the chunk actually starts.
                    rects = found if candidate_len == total_len else found[:1]
                    matched_len = candidate_len
                    break

            if rects:
                for rect in rects:
                    annot = page.add_highlight_annot(rect)
                    annot.update()

                meta["highlight_status"] = (
                    "full" if matched_len == total_len else f"fallback_{matched_len}chars"
                )
                meta["rect_count"] = len(rects)
                # Heuristic: if less than 50% of the chunk text was locatable on this
                # page, the remaining content likely continues to the next page.
                meta["chunk_continues"] = (
                    total_len > 300 and matched_len < total_len * 0.5
                )
            else:
                meta["highlight_status"] = "none"
                # No match at all — if chunk is longer than a typical page (~2500 chars),
                # assume content spans beyond this page.
                meta["chunk_continues"] = total_len > 2500

        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes, meta

    except Exception:
        return None
