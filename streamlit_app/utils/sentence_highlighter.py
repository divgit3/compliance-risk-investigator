"""
streamlit_app/utils/sentence_highlighter.py

Sentence-level highlighting logic for 1.2g.

All functions are pure Python with no Streamlit dependency — importable directly
in pytest without a Streamlit runtime.

Pipeline:
  normalize(text) → remove PDF bullet chars and footnote markers
  split_sentences(text) → NLTK punkt tokenization with validation
  score_sentences(chunk_text, answer_text) → cosine similarity ranking
  detect_columns(words) → two-column layout detection from word x0 coords
  sort_words_by_reading_order(words, ...) → visual left-to-right, top-to-bottom
  find_sentence_bboxes_on_page(sentence, sorted_words) → word-level bbox match
  sentence_highlight(chunk_text, answer_text, page, ...) → top-K bboxes + status
"""

from __future__ import annotations

import logging
import os
import re

import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

BULLET_CHARS = ["", "•", "▪", "■", "●"]

SCORE_THRESHOLD: float = 0.3
TOP_K_SENTENCES: int   = 2

REFUSAL_PHRASES: tuple[str, ...] = (
    "do not address",
    "does not address",
    "no specific information",
    "no information",
    "policy documents do not",
    "not covered in",
    "not specified in",
    "not mentioned in",
    "outside the scope",
)

_SENTENCE_MAX_WORDS: int   = 200
_MIN_SENTENCES: int        = 2
_TWO_COL_X_SPREAD: float   = 200.0   # pt — minimum spread for two-col candidacy
_TWO_COL_GAP_THRESHOLD: float = 15.0 # pt — minimum gap size to declare two-col
_Y_TOLERANCE: float        = 3.0     # pt — line merge tolerance


# ── normalize ─────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """
    Pre-normalize PDF-extracted chunk text before sentence splitting.

    1. Replaces each character in BULLET_CHARS followed by a space with a
       newline, so NLTK punkt treats each bullet item as a sentence boundary.
    2. Strips footnote markers of the form .<1–2 digits><space><Capital>
       e.g. "products.7 This" → "products. This". A negative lookbehind
       prevents corrupting decimal numbers like "0.5. The result".
    """
    for bullet in BULLET_CHARS:
        text = text.replace(bullet + " ", "\n")
    # Bullet items in OIG PDFs end with semicolons, not periods.
    # After the bullet→newline replacement, convert "; \n" or ";\n" to ".\n"
    # so punkt treats each bullet item as a sentence boundary.
    # PDF extraction may leave a trailing space before the newline (";\s*\n").
    text = re.sub(r';\s*\n', '.\n', text)
    text = re.sub(r'(?<!\d)\.(\d{1,2})\s+([A-Z])', r'. \2', text)
    return text


# ── refusal detection ─────────────────────────────────────────────────────────

def is_refusal(answer_text: str) -> bool:
    """Return True if answer_text contains a phrase indicating topic absence."""
    lower = answer_text.lower()
    return any(phrase in lower for phrase in REFUSAL_PHRASES)


# ── split_sentences ───────────────────────────────────────────────────────────

def split_sentences(text: str, chunk_id: str = "") -> list[str] | None:
    """
    Split normalized chunk text into sentences using NLTK punkt.

    Returns None on validation failure, which triggers whole-chunk fallback:
      - Any sentence exceeds _SENTENCE_MAX_WORDS words
      - Fewer than _MIN_SENTENCES sentences produced
    """
    try:
        from nltk.tokenize import sent_tokenize
        sentences = sent_tokenize(normalize(text), language="english")
    except LookupError:
        import nltk
        nltk.download("punkt_tab", quiet=True)
        from nltk.tokenize import sent_tokenize
        sentences = sent_tokenize(normalize(text), language="english")
    except Exception as e:
        logger.warning(
            "split_sentences: tokenization failed chunk=%s: %s — fallback", chunk_id, e
        )
        return None

    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) < _MIN_SENTENCES:
        logger.warning(
            "split_sentences: chunk=%s produced %d sentence(s) < min %d — fallback",
            chunk_id, len(sentences), _MIN_SENTENCES,
        )
        return None

    for i, s in enumerate(sentences):
        wc = len(s.split())
        if wc > _SENTENCE_MAX_WORDS:
            logger.warning(
                "split_sentences: chunk=%s sentence[%d] has %d words > %d — fallback",
                chunk_id, i, wc, _SENTENCE_MAX_WORDS,
            )
            return None

    return sentences


# ── embedding + scoring ───────────────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with text-embedding-3-small (dim=1536)."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
        dimensions=1536,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def _cosine(a: list[float], b: list[float]) -> float:
    av = np.array(a, dtype=float)
    bv = np.array(b, dtype=float)
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    return float(np.dot(av, bv) / denom) if denom > 0 else 0.0


def score_sentences(
    chunk_text: str,
    answer_text: str,
    chunk_id: str = "",
) -> tuple[list[str], list[float]] | None:
    """
    Score each sentence in chunk_text by cosine similarity to answer_text.

    Returns (sentences, scores) both ordered by score descending, or None on
    any failure (triggers whole-chunk fallback in the caller).
    """
    sentences = split_sentences(chunk_text, chunk_id=chunk_id)
    if sentences is None:
        return None

    try:
        embeddings = _embed(sentences + [answer_text])
    except Exception as e:
        logger.warning(
            "score_sentences: embedding failed chunk=%s: %s — fallback", chunk_id, e
        )
        return None

    answer_emb = embeddings[-1]
    scores = [_cosine(e, answer_emb) for e in embeddings[:-1]]

    pairs = sorted(zip(sentences, scores), key=lambda x: -x[1])
    sents, scrs = zip(*pairs)
    return list(sents), list(scrs)


# ── column detection ──────────────────────────────────────────────────────────

def detect_columns(words: list) -> tuple[bool, float]:
    """
    Detect whether a PDF page uses a two-column layout by analysing word x0
    coordinates.

    Strategy: if the overall x0 spread exceeds _TWO_COL_X_SPREAD, scan for the
    largest gap in the x0 histogram that falls within the middle 15–85% of the
    x0 range. A gap > _TWO_COL_GAP_THRESHOLD declares two-column; split_x is
    the midpoint of that gap.

    Federal Register (OIG CPG) note: these pages are trimodal — left body text
    (~45–200 pt), centre section labels (~222–380 pt), right body text
    (~399–570 pt). The largest gap in the middle band falls at ~376–399 pt,
    giving split_x ≈ 388 pt, which correctly separates right body text from
    everything else.

    words: list of (x0, y0, x1, y1, word, block_no, line_no, word_no) tuples
    from page.get_text("words").
    """
    if not words:
        return False, 0.0

    x0_vals = [w[0] for w in words]
    x_min, x_max = min(x0_vals), max(x0_vals)

    if x_max - x_min <= _TWO_COL_X_SPREAD:
        return False, 0.0

    # Build sorted list of unique rounded x0 positions (2pt resolution)
    resolution = 2.0
    sorted_unique = sorted(set(round(x / resolution) * resolution for x in x0_vals))

    mid_lo = x_min + (x_max - x_min) * 0.15
    mid_hi = x_min + (x_max - x_min) * 0.85

    best_gap_size = 0.0
    best_split = 0.0
    for i in range(len(sorted_unique) - 1):
        g_start = sorted_unique[i]
        g_end   = sorted_unique[i + 1]
        g_size  = g_end - g_start
        g_mid   = (g_start + g_end) / 2.0
        if g_size > best_gap_size and mid_lo < g_mid < mid_hi:
            best_gap_size = g_size
            best_split = g_mid

    if best_gap_size > _TWO_COL_GAP_THRESHOLD:
        return True, best_split

    return False, 0.0


def sort_words_by_reading_order(
    words: list,
    is_two_column: bool,
    split_x: float,
) -> list:
    """
    Sort words into visual reading order.

    Two-column: (column_index=0 for left, 1 for right, then y0, x0).
    Single-column: (y0, x0).
    """
    if is_two_column:
        return sorted(words, key=lambda w: (0 if w[0] < split_x else 1, w[1], w[0]))
    return sorted(words, key=lambda w: (w[1], w[0]))


# ── word-level bbox reconstruction ────────────────────────────────────────────

def _merge_to_lines(
    word_bboxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Merge word-level bboxes into line-level bboxes (same y_tolerance as 1.2f)."""
    if not word_bboxes:
        return []
    sorted_bboxes = sorted(word_bboxes, key=lambda b: (b[1], b[0]))
    cx0, cy0, cx1, cy1 = sorted_bboxes[0]
    lines: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in sorted_bboxes[1:]:
        if abs(y0 - cy0) <= _Y_TOLERANCE:
            cx0 = min(cx0, x0)
            cx1 = max(cx1, x1)
            cy1 = max(cy1, y1)
        else:
            lines.append((cx0, cy0, cx1, cy1))
            cx0, cy0, cx1, cy1 = x0, y0, x1, y1
    lines.append((cx0, cy0, cx1, cy1))
    return lines


def _clean_word(w: str) -> str:
    """Normalise a word for fuzzy matching: strip outer punctuation, lowercase, straighten quotes."""
    w = w.strip(".,;:\"'()[]{}!?")
    w = w.lower()
    w = w.replace("’", "'").replace("‘", "'")
    w = w.replace("“", '"').replace("”", '"')
    return w


def find_sentence_bboxes_on_page(
    sentence: str,
    sorted_words: list,
) -> list[tuple[float, float, float, float]]:
    """
    Find bboxes for a sentence on the page via sliding-window word matching.

    sorted_words: output of sort_words_by_reading_order() — (x0,y0,x1,y1,word,...).
    Returns list of line-level (x0,y0,x1,y1) bboxes, or [] on no match.
    """
    sent_words = sentence.split()
    if not sent_words:
        return []

    n = len(sent_words)
    page_words  = [w[4] for w in sorted_words]
    page_bboxes = [(w[0], w[1], w[2], w[3]) for w in sorted_words]
    m = len(page_words)

    sent_clean = [_clean_word(w) for w in sent_words]

    for i in range(m - n + 1):
        if all(
            _clean_word(page_words[i + j]) == sent_clean[j]
            for j in range(n)
        ):
            matched = [page_bboxes[i + j] for j in range(n)]
            return _merge_to_lines(matched)

    return []


# ── orchestrator ──────────────────────────────────────────────────────────────

def sentence_highlight(
    chunk_text: str,
    answer_text: str,
    page,                          # fitz.Page — passed in, not opened here
    chunk_id: str = "",
    chunk_bboxes_for_page: tuple = (),
) -> tuple[tuple, str]:
    """
    Core 1.2g pipeline: score sentences, reconstruct bboxes for top-K.

    Returns (bboxes_to_draw, highlight_status) where:
      highlight_status = "sentence" — top-K sentence bboxes found
                       = "full"     — segmentation / word-match failure, fall back
                       = "none"     — topic absent (refusal phrase detected OR
                                      max score < SCORE_THRESHOLD)
    """
    # Short-circuit: refusal phrase in answer → skip embedding, return "none".
    if is_refusal(answer_text):
        logger.debug("sentence_highlight: refusal phrase detected chunk=%s — none", chunk_id)
        return (), "none"

    result = score_sentences(chunk_text, answer_text, chunk_id=chunk_id)

    if result is None:
        return chunk_bboxes_for_page, "full"

    sentences, scores = result

    if scores[0] < SCORE_THRESHOLD:
        return (), "none"

    top_sentences = sentences[:TOP_K_SENTENCES]

    # Reconstruct word bboxes from this page
    words = page.get_text("words")
    is_two_col, split_x = detect_columns(words)
    sorted_words = sort_words_by_reading_order(words, is_two_col, split_x)

    matched: list[tuple[float, float, float, float]] = []
    for sent in top_sentences:
        matched.extend(find_sentence_bboxes_on_page(sent, sorted_words))

    if not matched:
        logger.warning(
            "sentence_highlight: no word matches found chunk=%s — fallback", chunk_id
        )
        return chunk_bboxes_for_page, "full"

    return tuple(matched), "sentence"
