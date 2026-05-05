"""
streamlit_app/utils/pdf_renderer.py

Server-side PDF page rasterization using PyMuPDF with bbox-based highlighting.

1.2g: When answer_text and chunk_text are provided, delegates to
sentence_highlighter.sentence_highlight() to score and rank sentences by cosine
similarity, highlighting only the top-2 most relevant sentences.

Falls back to whole-chunk highlighting (1.2f path) on segmentation or word-match
failure. Falls back to no-highlight when max sentence score < SCORE_THRESHOLD
(topic absent from corpus).

Policy PDFs are mounted at /app/data/raw/policy_docs/ in the Streamlit container
(read-only). Pages are rendered at 2× resolution (~144 DPI).
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
    answer_text: str = "",
    chunk_text: str = "",
) -> tuple[bytes, dict] | None:
    """
    Rasterize a PDF page as PNG bytes with optional sentence-level highlighting.

    Args:
        source_doc:          PDF filename (e.g. "nova_pharma_internal_policy_SYNTHETIC.pdf")
        page_num:            1-indexed page number (as stored in Qdrant chunk metadata)
        bboxes_for_page:     Tuple of (x0, y0, x1, y1) tuples for the chunk on this page.
                             Pre-filtered to page_num by the caller. Used as fallback
                             highlight region when sentence scoring fails.
        answer_text:         Agent answer text. When non-empty, enables sentence scoring.
        chunk_text:          Full chunk text (excerpt from citation). When non-empty
                             alongside answer_text, enables sentence scoring.

    Returns:
        (png_bytes, metadata) or None on file-read error.

    metadata keys:
        highlight_status  "sentence" | "full" | "none"
        rect_count        int

    highlight_status values:
        "sentence"  Top-2 sentences highlighted (1.2g path)
        "full"      Whole-chunk highlighted — sentence scoring unavailable or
                    fell back due to segmentation / word-match failure (1.2f path)
        "none"      No highlights — topic absent (max sentence score < threshold)
                    or no bboxes available
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

        if answer_text and chunk_text:
            from utils.sentence_highlighter import sentence_highlight
            bboxes_to_draw, hs = sentence_highlight(
                chunk_text,
                answer_text,
                page,
                chunk_bboxes_for_page=bboxes_for_page,
            )
        else:
            bboxes_to_draw = bboxes_for_page
            hs = "full" if bboxes_for_page else "none"

        for x0, y0, x1, y1 in bboxes_to_draw:
            annot = page.add_highlight_annot(fitz.Rect(x0, y0, x1, y1))
            annot.update()

        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()

        return png_bytes, {
            "highlight_status": hs,
            "rect_count": len(bboxes_to_draw),
        }

    except Exception:
        return None
