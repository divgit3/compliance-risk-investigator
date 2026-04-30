"""
agents/post_processors/over_narration.py

Strips over-narration from TOPIC ABSENT answers.

The policy agent sometimes correctly identifies that a topic is absent from
the knowledge base (Step A: refusal first sentence) but then appends multiple
paragraphs of tangentially-retrieved content despite the TOOL OUTPUT SUPPRESSION
instruction in the system prompt failing to bind.

strip_over_narration() detects this pattern and truncates the answer back to the
refusal first sentence plus any clean closing sentence found at the end of the body,
discarding the over-narration middle.
"""

from __future__ import annotations

import re

# --- Detection regexes ---

_REFUSAL_RE = re.compile(
    r"""(?xi)
    \b(
        policy\s+(does\s+not\s+address|does\s+not\s+contain|does\s+not\s+cover|
                  does\s+not\s+include|does\s+not\s+specify|does\s+not\s+discuss|
                  does\s+not\s+define)
        |
        not\s+(explicitly|specifically)\s+addressed
        |
        there\s+(is|are)\s+no\s+specific\s+rule
        |
        no\s+(policy|specific\s+policy|explicit\s+policy|rule)\s+(exists|found|available)
        |
        (is|are)\s+not\s+(explicitly|specifically)\s+(covered|mentioned|addressed|defined)
        |
        does\s+not\s+explicitly\s+(address|cover|mention|define|discuss|specify)
    )\b
    """,
    re.IGNORECASE,
)

_SOFT_TRANSITION_RE = re.compile(
    r"""(?xi)
    \b(
        however[,\s]+i\s+can\s+(provide|share|offer)
        |
        based\s+on\s+the\s+retrieved\s+content
        |
        some\s+relevant\s+information
        |
        let\s+me\s+provide
        |
        i\s+can\s+(provide|share)\s+some
        |
        the\s+relevant\s+chunks?\s+(retrieved|from\s+the)
        |
        the\s+following\s+information\s+(?:was|is|has\s+been)\s+retrieved
    )\b
    """,
    re.IGNORECASE,
)

_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)

_CHUNK_REF_RE = re.compile(r"DOC_\d+_chunk_\d+|chunk_id:\s*DOC", re.IGNORECASE)

_CLEAN_CLOSING_MAX_WORDS = 35


_CLEAN_CLOSING_MIN_WORDS = 5


def _is_clean_closing(sentence: str) -> bool:
    """True if a sentence is suitable as a closing: short, no chunk refs, no soft transitions."""
    words = sentence.split()
    if len(words) < _CLEAN_CLOSING_MIN_WORDS:
        return False
    if len(words) > _CLEAN_CLOSING_MAX_WORDS:
        return False
    if _CHUNK_REF_RE.search(sentence):
        return False
    if _SOFT_TRANSITION_RE.search(sentence):
        return False
    if _NUMBERED_RE.search(sentence):
        return False
    if _REFUSAL_RE.search(sentence):
        return False
    # Reject markdown bullet list items (content items, not genuine closings)
    if re.match(r"^\s*[-*•]\s+", sentence):
        return False
    return True


def _split_sentences(text: str) -> list[str]:
    """Rough sentence split: split on '. ' or '.\n' while preserving the delimiter."""
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def strip_over_narration(answer: str) -> tuple[str, str | None]:
    """
    Detect and strip over-narration from TOPIC ABSENT answers.

    Returns:
        (answer, note) where:
        - answer is the (possibly truncated) answer text
        - note is a data_limitations string if truncation occurred, else None

    Detection fires when ALL of:
        Step A: First sentence matches _REFUSAL_RE
        Step B: Body contains soft transition OR (numbered list AND chunk references)
    """
    sentences = _split_sentences(answer)

    if len(sentences) < 2:
        return answer, None

    first = sentences[0]

    # Step A: refusal in first sentence
    if not _REFUSAL_RE.search(first):
        return answer, None

    body_sentences = sentences[1:]

    # Preserve original body text (with newlines) so _NUMBERED_RE multiline works.
    # _split_sentences + join(" ") destroys line structure needed for "^\s*\d+\.\s+" match.
    body_original = answer.strip()[len(first):].lstrip()

    # Step B: over-narration signal in the body
    has_soft = bool(_SOFT_TRANSITION_RE.search(body_original))
    has_numbered = bool(_NUMBERED_RE.search(body_original))

    has_chunk_refs = bool(_CHUNK_REF_RE.search(body_original))

    # Fire when soft transition + numbered list (original Path B assumption)
    # OR when numbered list + chunk references (un_01 pattern: agent skips
    # the soft transition but narrates retrieved chunks with citations).
    # The chunk_refs guard prevents false positives on entries with legitimate
    # numbered content (e.g., fp_01's general meal limits without retrieval
    # narration).
    if not (has_soft or (has_numbered and has_chunk_refs)):
        return answer, None

    # Over-narration confirmed. Count discarded items for the note.
    discarded_count = len(body_sentences)

    # Step C: scan backward for a clean closing sentence
    closing: str | None = None
    for sent in reversed(body_sentences):
        if _is_clean_closing(sent):
            closing = sent
            break

    # Build truncated answer
    kept = [first]
    if closing and closing != first:
        kept.append(closing)
    truncated = " ".join(kept)

    # Step D: build note
    note = (
        f"Answer trimmed: {discarded_count} over-narration sentence(s) suppressed "
        f"(TOPIC ABSENT — tool output should not appear in final answer)."
    )

    return truncated, note
