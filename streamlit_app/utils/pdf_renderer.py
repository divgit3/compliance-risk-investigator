"""
streamlit_app/utils/pdf_renderer.py

Server-side PDF page rasterization using PyMuPDF.

Policy PDFs are mounted at /app/data/raw/policy_docs/ in the Streamlit container
(read-only). Pages are rendered at 2× resolution (144 DPI) and returned as PNG bytes.
Results are cached by (pdf_path, page_num) so repeated views don't re-render.
"""

from __future__ import annotations

import os

import streamlit as st

_PDF_DIR = "/app/data/raw/policy_docs"

# Map source_doc filename → absolute path in container
def _pdf_path(source_doc: str) -> str:
    return os.path.join(_PDF_DIR, source_doc)


@st.cache_data(show_spinner=False)
def render_pdf_page(source_doc: str, page_num: int) -> bytes | None:
    """
    Rasterize a single PDF page and return PNG bytes, or None on error.

    Args:
        source_doc: PDF filename (e.g. "nova_pharma_internal_policy_SYNTHETIC.pdf")
        page_num:   1-indexed page number (as stored in Qdrant chunk metadata)
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
        # 2× zoom → ~144 DPI — legible without excessive memory
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes

    except Exception:
        return None
