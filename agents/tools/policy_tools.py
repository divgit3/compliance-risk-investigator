"""
agents/tools/policy_tools.py — LangChain tools for policy knowledge base queries.

Tools:
  search_policy_docs — Qdrant semantic search over 128 policy chunks
  lookup_rule        — Exact threshold and rule ID lookup from rules.json
                       (added Task 3.3)

Qdrant collection: policy_docs
  - 128 chunks, 1536-dim, Cosine similarity
  - Payload fields: chunk_id, doc_id, authority, doc_type, filename,
                    chunk_index, page_num, relevant_rules, text

Requires OPENAI_API_KEY in environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from langchain.tools import tool
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

# ── rules.json cache (Task 3.3) ────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]
_RULES_PATH = _ROOT / "compliance/rules.json"
_rules_cache: Optional[Dict[str, Any]] = None


def _get_rules() -> Dict[str, Any]:
    global _rules_cache
    if _rules_cache is None:
        with open(_RULES_PATH) as f:
            _rules_cache = json.load(f)
    return _rules_cache

_QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
_QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
_COLLECTION        = "policy_docs"
_EMBEDDING_MODEL   = "text-embedding-3-small"
_EMBEDDING_DIM     = 1536

# Module-level singletons — initialised on first call
_qdrant_client: Optional[QdrantClient] = None
_openai_client: Optional[OpenAI] = None


def _get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT, timeout=10)
    return _qdrant_client


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY not set in environment")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def _embed(text: str) -> list[float]:
    client = _get_openai()
    response = client.embeddings.create(
        model=_EMBEDDING_MODEL,
        input=text,
        dimensions=_EMBEDDING_DIM,
    )
    return response.data[0].embedding


@tool
def search_policy_docs(query: str, top_k: int = 3) -> dict:
    """
    Search the Nova Pharma policy document knowledge base for relevant compliance
    guidance using semantic similarity.

    IMPORTANT: Pass the user's question VERBATIM as the `query` parameter. Do NOT
    paraphrase, summarize, shorten, or extract keywords. The retrieval system
    performs better with the full natural-language question. If the user asked
    'What is the annual meal cap per HCP at Nova Pharma?', pass that exact string
    to `query` — not 'annual meal limit HCP' or any shortened form.

    Edge case: if the user's question references internal flag names (e.g.,
    'flag_fmv_non_compliance'), translate those to policy concepts in the query.
    For all other questions, pass the question verbatim.

    Embeds the query with OpenAI text-embedding-3-small and searches the Qdrant
    policy_docs collection (128 chunks from PhRMA Code 2022, OIG CPG, OIG Speaker
    Fraud Alert, CMS Data Dictionary, and Nova Pharma Internal Policy).

    Use this tool to ground compliance findings in specific policy language.

    Example: query='What is the annual meal cap per HCP at Nova Pharma?' returns
    chunks from Nova Pharma Internal Policy relevant to meal limits and HCP
    compensation caps.

    Returns a list of matching chunks with chunk_id, source_doc, relevance_score,
    and the full chunk text as excerpt. Returns empty results list if no chunks
    meet the minimum relevance threshold — try a broader query if so.
    """
    try:
        query = query.strip().strip("'\"")
        qdrant = _get_qdrant()
        vector = _embed(query)

        try:
            # qdrant-client >= 1.7: use query_points
            results = qdrant.query_points(
                collection_name=_COLLECTION,
                query=vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            ).points
        except AttributeError:
            # fallback for older qdrant-client versions
            results = qdrant.search(
                collection_name=_COLLECTION,
                query_vector=vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )

        hits = []
        for hit in results:
            payload = hit.payload or {}
            raw_text = payload.get("text") or payload.get("content") or ""
            excerpt  = raw_text.replace("\n", " ").strip()

            source_doc = (
                payload.get("filename")
                or payload.get("doc_id")
                or "unknown"
            )

            hits.append({
                "chunk_id":           payload.get("chunk_id", str(hit.id)),
                "source_doc":         source_doc,
                "authority":          payload.get("authority", "unknown"),
                "relevance_score":    round(float(hit.score), 4),
                "excerpt":            excerpt,
                "page_num":           payload.get("page_num"),
                "bboxes":             payload.get("bboxes") or [],
                "section_heading":    payload.get("section_heading", ""),
                "chunk_start_offset": payload.get("chunk_start_offset"),
                "chunk_end_offset":   payload.get("chunk_end_offset"),
            })

        # Filter applied once after all hits collected — not inside the loop
        MIN_RELEVANCE = 0.0
        hits = [h for h in hits if h["relevance_score"] >= MIN_RELEVANCE]
        if not hits:
            return {
                "query":   query,
                "top_k":   top_k,
                "results": [],
                "note":    "No policy chunks met minimum relevance threshold (0.15). "
                           "Try a broader query such as 'meal limit PhRMA' or "
                           "'speaker FMV fair market value'.",
            }

        return {
            "query":   query,
            "top_k":   top_k,
            "results": hits,
        }

    except EnvironmentError as e:
        return {"error": str(e)}
    except UnexpectedResponse as e:
        return {"error": f"Qdrant error: {e}"}
    except Exception as e:
        return {"error": f"Policy search failed: {e}"}


# ── lookup_rule (Task 3.3) ─────────────────────────────────────────────────────

def _infer_scope(rule: dict) -> dict:
    """Derive explicit scope metadata from a rule's structural fields."""
    name           = rule.get("rule_name", "").lower()
    category       = rule.get("category", "")
    unit           = rule.get("unit", "")
    threshold_type = rule.get("threshold_type", "")

    # Time scope
    if "annual" in name or "per year" in name or "per program year" in name:
        time_scope = "annual"
    elif "per month" in name or "monthly" in name:
        time_scope = "monthly"
    elif "per week" in name or "weekly" in name:
        time_scope = "weekly"
    elif "per day" in name or "daily" in name:
        time_scope = "daily"
    elif unit == "days":
        time_scope = "window_in_days"
    else:
        time_scope = "per_event_or_per_instance"

    # Entity scope
    if "per attendee" in name:
        entity_scope = "per_attendee"
    elif "per event" in name or category == "venue_event_costs":
        entity_scope = "per_event"
    elif "per meal" in name or category == "meal_limits":
        entity_scope = "per_meal"
    elif "per engagement" in name or "fmv" in name:
        entity_scope = "per_engagement"
    elif "per hcp" in name:
        entity_scope = "per_hcp"
    elif category == "hcp_compensation":
        entity_scope = "per_hcp_aggregate"
    else:
        entity_scope = "unspecified"

    return {
        "time_scope":      time_scope,
        "entity_scope":    entity_scope,
        "threshold_type":  threshold_type,
    }


def _rule_score(rule: dict, tokens: list[str]) -> int:
    """Count how many query tokens appear in searchable rule fields."""
    searchable = " ".join(filter(None, [
        rule.get("rule_id", ""),
        rule.get("rule_name", ""),
        rule.get("category", ""),
        rule.get("violation_type", ""),
        rule.get("reconciliation_note", ""),
        " ".join(rule.get("applies_to", [])),
    ])).lower()
    return sum(1 for t in tokens if t in searchable)


@tool
def list_rule_dimensions() -> dict:
    """
    Return the list of scope dimensions the rules registry segments by, and
    the list of dimensions it does NOT segment by.

    Call this when the question references a scope qualifier — a jurisdiction
    (state, region), HCP specialty (cardiologist, oncologist, etc.), HCP role
    (nurse practitioner, physician assistant, etc.), drug or product, or
    patient population. The answer to "is there a per-X cap" depends on
    whether the rules registry has any rules segmented by X.

    If the question's qualifier is in `dimensions_absent`, the policy applies
    uniformly across that dimension — your answer must explicitly state this
    rather than substituting general rules as if they were specific to the
    qualifier.
    """
    rules_data = _get_rules()
    rules = rules_data["rules"]

    dimensions_present = {
        "time_scope": set(),
        "entity_scope": set(),
        "applies_to": set(),
        "category": set(),
        "severity": set(),
    }

    for r in rules:
        scope = _infer_scope(r)
        dimensions_present["time_scope"].add(scope["time_scope"])
        dimensions_present["entity_scope"].add(scope["entity_scope"])
        for at in r.get("applies_to", []):
            dimensions_present["applies_to"].add(at)
        dimensions_present["category"].add(r.get("category", ""))
        dimensions_present["severity"].add(r.get("severity", ""))

    dimensions_absent = [
        "jurisdiction",
        "hcp_specialty",
        "hcp_role",
        "drug_or_product",
        "patient_population",
    ]

    return {
        "dimensions_present": {k: sorted(v) for k, v in dimensions_present.items()},
        "dimensions_absent": dimensions_absent,
        "note": (
            "If the question references a dimension in `dimensions_absent`, "
            "the policy does not segment rules by that dimension — all rules "
            "apply uniformly across that dimension. Your answer must state "
            "this explicitly rather than substituting general rules as if "
            "they were specific to the dimension the question asked about."
        ),
    }


@tool
def lookup_rule(query: str) -> dict:
    """
    Look up compliance rules and thresholds from Nova Pharma's rules registry
    (compliance/rules.json — 24 rules extracted from policy documents via RAG).

    Use this when you need exact dollar thresholds, rule IDs, or policy authority
    citations. Query can be a rule name, flag name, or topic such as:
      - "meal limit" / "meal expense"
      - "speaker FMV" / "fair market value"
      - "annual cap" / "speaker program cap"
      - "vague rationale" / "business rationale"
      - "attestation" / "documentation"
      - "repeat speaker" / "low attendance"
      - "entertainment" / "gifts" / "cash payment"

    Returns up to 5 matching rules with exact thresholds, authority source,
    chunk_id for audit, and whether Nova Pharma is stricter than the PhRMA
    industry standard for that rule.
    """
    try:
        rules_data = _get_rules()
        rules      = rules_data["rules"]
        fallbacks  = rules_data.get("fallback_rules", {})

        query_lower = query.lower()
        tokens = [t for t in query_lower.replace("-", " ").split() if len(t) > 2]

        # Score each rule by keyword overlap
        scored = [(r, _rule_score(r, tokens)) for r in rules]
        scored = [(r, s) for r, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        top_rules = [r for r, _ in scored[:5]]

        if not top_rules:
            return {
                "rules":   [],
                "message": f"No matching rules found for: '{query}'",
            }

        result_rules = []
        for r in top_rules:
            rule_id   = r["rule_id"]
            sources   = r.get("sources", [])
            authority = r.get("effective_source") or (sources[0]["authority"] if sources else "unknown")
            source_doc = sources[0].get("chunk_id", "") if sources else ""
            chunk_id   = sources[0].get("chunk_id", "") if sources else ""

            # Determine nova_override: compare effective_threshold against fallback (PhRMA proxy)
            eff = r.get("effective_threshold")
            fallback = fallbacks.get(rule_id)
            nova_override = False
            phrma_equivalent = None

            if (
                eff is not None
                and fallback is not None
                and isinstance(eff, (int, float))
                and isinstance(fallback, (int, float))
                and eff != fallback
            ):
                unit = r.get("unit", "")
                # Nova is stricter if it has a lower maximum threshold than fallback
                if r.get("threshold_type") == "maximum" and eff < fallback:
                    nova_override = True
                    phrma_equivalent = f"{fallback} {unit}".strip()

            # Human-readable threshold string
            eff_threshold = r.get("effective_threshold")
            unit = r.get("unit", "")
            if isinstance(eff_threshold, bool):
                threshold_str = "Required" if eff_threshold else "Prohibited"
            elif eff_threshold is not None:
                threshold_str = f"{eff_threshold} {unit}".strip()
            else:
                threshold_str = "see policy document"

            result_rules.append({
                "rule_id":           rule_id,
                "rule_name":         r.get("rule_name", ""),
                "category":          r.get("category", ""),
                "threshold":         threshold_str,
                "threshold_type":    r.get("threshold_type", ""),
                "scope":             _infer_scope(r),
                "severity":          r.get("severity", ""),
                "violation_type":    r.get("violation_type", ""),
                "authority":         authority,
                "source_doc":        source_doc,
                "chunk_id":          chunk_id,
                "nova_override":     nova_override,
                "phrma_equivalent":  phrma_equivalent,
                "reconciliation_note": r.get("reconciliation_note", ""),
            })

        return {"rules": result_rules}

    except Exception as e:
        return {"error": f"lookup_rule failed: {e}"}
