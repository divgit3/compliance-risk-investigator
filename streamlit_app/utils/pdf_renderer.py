"""
streamlit_app/utils/pdf_renderer.py

Server-side PDF page rasterization using PyMuPDF with bbox-based highlighting.

Policy PDFs are mounted at /app/data/raw/policy_docs/ in the Streamlit container
(read-only). Pages are rendered at 2× resolution (~144 DPI).

Bboxes are stored per-chunk in Qdrant at index time (Phase 1 of the bbox fix).
The caller passes only the bboxes for the current page; no runtime text search
is needed.
"""

from __future__ import annotations

import os

import streamlit as st

_PDF_DIR = "/app/data/raw/policy_docs"


def _pdf_path(source_doc: str) -> str:
    return os.path.join(_PDF_DIR, source_doc)


@st.cache_data(show_spinner=False)
def render_pdf_page(
    source_doc: str,
    page_num: int,
    bboxes_for_page: tuple = (),
) -> tuple[bytes, dict] | None:
    """
    Rasterize a PDF page as PNG bytes with optional bbox highlighting.

    Args:
        source_doc:       PDF filename (e.g. "nova_pharma_internal_policy_SYNTHETIC.pdf")
        page_num:         1-indexed page number (as stored in Qdrant chunk metadata)
        bboxes_for_page:  Tuple of (x0, y0, x1, y1) tuples for rectangles on this
                          page. Must be pre-filtered to this page_num by the caller.
                          Tuple (not list) for st.cache_data hashability.

    Returns:
        (png_bytes, metadata) or None on file-read error.

    metadata keys:
        highlight_status  'full' | 'none'
        rect_count        int
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
