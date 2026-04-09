from __future__ import annotations
"""
business_rules_registry.py
---------------------------
RAGs against the Qdrant `policy_docs` collection (128 embedded chunks from
5 policy documents) to extract compliance rule thresholds, reconciles
values across authorities, and writes a versioned compliance/rules.json.

rules.json is the single source of truth for all business rule constants
used by downstream anomaly detection components (mart_event_features,
rule_based_flags.py, scorer.py, etc.).

Usage:
  python pipelines/business_rules_registry.py

Prerequisites:
  - embed_policy_docs.py must have run (128 points in Qdrant)
  - Qdrant running at localhost:6333
  - OPENAI_API_KEY set in .env
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

QDRANT_HOST       = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "policy_docs"
EMBEDDING_MODEL   = "text-embedding-ada-002"
EXTRACTION_MODEL  = "gpt-4o"
OUTPUT_PATH       = Path(__file__).parent.parent / "compliance" / "rules.json"

# Authority hierarchy — lower index = higher authority
AUTHORITY_HIERARCHY = ["OIG", "Nova Pharma", "PhRMA", "CMS"]

# ── Fallback rules — used when RAG extraction returns null ─────────────────────
# These are the canonical values applied by mart_hcp_spend_features and all
# downstream models. RAG extraction overrides these if explicit thresholds
# are found in the embedded policy documents.

FALLBACK_RULES = {
    "MEAL_001":     30.00,
    "MEAL_002":     75.00,
    "MEAL_003":    125.00,
    "MEAL_004":     75.00,
    "SPEAKER_001": 4000.00,
    "SPEAKER_002":    6,
    "SPEAKER_003":    3,
    "SPEAKER_004":    3,
    "SPEAKER_005":   30,
    "VENUE_001":   3000.00,
    "VENUE_002":   8000.00,
    "VENUE_003":    125.00,
    "COMP_001":   75000.00,
    "COMP_002":     500.00,
    "COMP_003":       0.80,
    "FREQ_001":      24,
    "FREQ_002":       3,
    "FREQ_003":      10,
    "ATTEST_001":     0.80,
    "ATTEST_002":  True,
    "ATTEST_003":  True,
    "PROHIBIT_001": True,
    "PROHIBIT_002": True,
    "PROHIBIT_003": True,
}

# ── Rule definitions — query, rule IDs, expected structure ────────────────────

RULE_CATEGORIES = [
    {
        "category":    "meal_limits",
        "query":       "meal limit breakfast lunch dinner food beverage cost per person",
        "rule_ids": [
            {
                "id":          "MEAL_001",
                "name":        "Meal Limit — Breakfast",
                "description": "Maximum per-person cost for breakfast meals",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "meal_limit_exceeded",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
            {
                "id":          "MEAL_002",
                "name":        "Meal Limit — Lunch",
                "description": "Maximum per-person cost for lunch meals",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "meal_limit_exceeded",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
            {
                "id":          "MEAL_003",
                "name":        "Meal Limit — Dinner",
                "description": "Maximum per-person cost for dinner meals",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "meal_limit_exceeded",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
            {
                "id":          "MEAL_004",
                "name":        "Meal Limit — General",
                "description": "Default meal limit when meal type is unspecified",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "meal_limit_exceeded",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
        ],
        "extraction_keys": ["MEAL_001", "MEAL_002", "MEAL_003", "MEAL_004"],
    },
    {
        "category":    "speaker_programs",
        "query":       "speaker program fee honoraria fair market value speaker events",
        "rule_ids": [
            {
                "id":          "SPEAKER_001",
                "name":        "Speaker Fee FMV Ceiling",
                "description": "Maximum speaker honorarium per event at fair market value",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "fmv_exceeded",
                "severity":    "high",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "SPEAKER_002",
                "name":        "Max Speaker Events Per Year",
                "description": "Maximum speaker program events per HCP per year before high-repeat flag",
                "unit":        "count",
                "threshold_type": "maximum",
                "violation_type": "repeat_speaker",
                "severity":    "medium",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "SPEAKER_003",
                "name":        "Repeat Speaker Threshold",
                "description": "Events per year above which repeat-speaker flag is triggered",
                "unit":        "count",
                "threshold_type": "maximum",
                "violation_type": "repeat_speaker",
                "severity":    "low",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "SPEAKER_004",
                "name":        "Minimum Attendees Per Event",
                "description": "Minimum number of attendees for a valid speaker program",
                "unit":        "count",
                "threshold_type": "minimum",
                "violation_type": "low_attendance",
                "severity":    "medium",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "SPEAKER_005",
                "name":        "Rapid Repeat Event Window",
                "description": "Days within which a second event by the same speaker is flagged as rapid repeat",
                "unit":        "days",
                "threshold_type": "maximum",
                "violation_type": "rapid_repeat_speaker",
                "severity":    "low",
                "applies_to":  ["speaker_events"],
            },
        ],
        "extraction_keys": [
            "SPEAKER_001", "SPEAKER_002", "SPEAKER_003",
            "SPEAKER_004", "SPEAKER_005",
        ],
    },
    {
        "category":    "venue_event_costs",
        "query":       "venue cost event total program cost limit speaker program expenses",
        "rule_ids": [
            {
                "id":          "VENUE_001",
                "name":        "Max Venue Cost Per Event",
                "description": "Maximum allowable venue cost per speaker program event",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "high_venue_cost",
                "severity":    "medium",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "VENUE_002",
                "name":        "Max Total Program Cost Per Event",
                "description": "Maximum total cost (speaker fee + venue + meals + travel) per speaker event",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "excess_program_cost",
                "severity":    "high",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "VENUE_003",
                "name":        "Meal Cost Per Attendee Ceiling",
                "description": "Maximum meal cost per attendee at speaker program events",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "meal_limit_exceeded",
                "severity":    "medium",
                "applies_to":  ["speaker_events"],
            },
        ],
        "extraction_keys": ["VENUE_001", "VENUE_002", "VENUE_003"],
    },
    {
        "category":    "hcp_compensation",
        "query":       "annual compensation cap HCP total payments fair market value consulting",
        "rule_ids": [
            {
                "id":          "COMP_001",
                "name":        "Annual HCP Compensation Cap",
                "description": "Maximum total annual compensation paid to a single HCP across all programs",
                "unit":        "USD",
                "threshold_type": "maximum",
                "violation_type": "annual_cap_exceeded",
                "severity":    "high",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
            {
                "id":          "COMP_002",
                "name":        "FMV Consulting Hourly Rate Ceiling",
                "description": "Maximum hourly consulting fee consistent with fair market value",
                "unit":        "USD/hour",
                "threshold_type": "maximum",
                "violation_type": "fmv_exceeded",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions"],
            },
            {
                "id":          "COMP_003",
                "name":        "Near-Cap Warning Threshold",
                "description": "Fraction of annual cap at which near-cap warning flag is triggered",
                "unit":        "fraction",
                "threshold_type": "maximum",
                "violation_type": "near_annual_cap",
                "severity":    "low",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
        ],
        "extraction_keys": ["COMP_001", "COMP_002", "COMP_003"],
    },
    {
        "category":    "interaction_frequency",
        "query":       "interaction frequency meals per year HCP rep visits frequency limit",
        "rule_ids": [
            {
                "id":          "FREQ_001",
                "name":        "Max Meals Per HCP Per Year",
                "description": "Maximum number of meal interactions per HCP per calendar year",
                "unit":        "count",
                "threshold_type": "maximum",
                "violation_type": "excess_meal_frequency",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions"],
            },
            {
                "id":          "FREQ_002",
                "name":        "Max Interactions Same HCP/Rep Per Week",
                "description": "Maximum same-rep visits to same HCP within a 7-day window",
                "unit":        "count",
                "threshold_type": "maximum",
                "violation_type": "excess_interaction_frequency",
                "severity":    "low",
                "applies_to":  ["hcp_interactions"],
            },
            {
                "id":          "FREQ_003",
                "name":        "Max Interactions Same HCP/Rep Per Month",
                "description": "Maximum same-rep visits to same HCP within a 30-day window",
                "unit":        "count",
                "threshold_type": "maximum",
                "violation_type": "excess_interaction_frequency",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions"],
            },
        ],
        "extraction_keys": ["FREQ_001", "FREQ_002", "FREQ_003"],
    },
    {
        "category":    "attestation_documentation",
        "query":       "attestation signature documentation compliance sign required",
        "rule_ids": [
            {
                "id":          "ATTEST_001",
                "name":        "Minimum Attestation Rate Per Event",
                "description": "Minimum fraction of attendees who must sign attestation forms",
                "unit":        "fraction",
                "threshold_type": "minimum",
                "violation_type": "missing_attestation",
                "severity":    "medium",
                "applies_to":  ["speaker_events"],
            },
            {
                "id":          "ATTEST_002",
                "name":        "Business Rationale Required",
                "description": "Whether a written business rationale is required for each interaction",
                "unit":        "boolean",
                "threshold_type": "required",
                "violation_type": "missing_documentation",
                "severity":    "low",
                "applies_to":  ["hcp_interactions"],
            },
            {
                "id":          "ATTEST_003",
                "name":        "FMV Documentation Required",
                "description": "Whether FMV documentation is required for compensated HCP services",
                "unit":        "boolean",
                "threshold_type": "required",
                "violation_type": "missing_fmv_documentation",
                "severity":    "medium",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
        ],
        "extraction_keys": ["ATTEST_001", "ATTEST_002", "ATTEST_003"],
    },
    {
        "category":    "prohibited_practices",
        "query":       "prohibited entertainment recreational activity gift banned",
        "rule_ids": [
            {
                "id":          "PROHIBIT_001",
                "name":        "Entertainment/Recreational Activities Prohibited",
                "description": "Whether entertainment or recreational activities associated with HCP interactions are prohibited",
                "unit":        "boolean",
                "threshold_type": "prohibited",
                "violation_type": "prohibited_entertainment",
                "severity":    "high",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
            {
                "id":          "PROHIBIT_002",
                "name":        "Gifts to HCPs Prohibited",
                "description": "Whether gifts of value to HCPs are prohibited",
                "unit":        "boolean",
                "threshold_type": "prohibited",
                "violation_type": "prohibited_gift",
                "severity":    "high",
                "applies_to":  ["hcp_interactions"],
            },
            {
                "id":          "PROHIBIT_003",
                "name":        "Cash or Cash Equivalent Payments Prohibited",
                "description": "Whether cash or cash-equivalent payments to HCPs are prohibited",
                "unit":        "boolean",
                "threshold_type": "prohibited",
                "violation_type": "prohibited_cash_payment",
                "severity":    "high",
                "applies_to":  ["hcp_interactions", "speaker_events"],
            },
        ],
        "extraction_keys": ["PROHIBIT_001", "PROHIBIT_002", "PROHIBIT_003"],
    },
]

# ── Clients ────────────────────────────────────────────────────────────────────

openai_client = OpenAI()
qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


# ── Functions ──────────────────────────────────────────────────────────────────

def query_qdrant(
    query_text: str,
    top_k: int = 5,
    filter_authority: str | None = None,
) -> list[dict]:
    """
    Embed query_text via ada-002 and search Qdrant policy_docs.
    Optionally filter by authority field in the chunk payload.
    Returns a list of chunk dicts with score.
    """
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[query_text],
    )
    query_vector = response.data[0].embedding

    qdrant_filter = None
    if filter_authority:
        qdrant_filter = Filter(
            must=[FieldCondition(
                key="authority",
                match=MatchValue(value=filter_authority),
            )]
        )

    results = qdrant_client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=top_k,
        query_filter=qdrant_filter,
        with_payload=True,
    )

    chunks = []
    for hit in results:
        p = hit.payload or {}
        chunks.append({
            "chunk_id":  p.get("chunk_id", ""),
            "doc_id":    p.get("doc_id", ""),
            "authority": p.get("authority", ""),
            "doc_type":  p.get("doc_type", ""),
            "text":      p.get("text", ""),
            "score":     hit.score,
            "page_num":  p.get("page_num", 0),
        })
    return chunks


def extract_rules_from_chunks(
    chunks: list[dict],
    rule_category: str,
    rule_definitions: list[dict],
) -> dict:
    """
    Pass retrieved chunk texts to GPT-4o to extract numeric/boolean thresholds.
    Returns dict of {rule_id: extracted_value | None}.
    Falls back to {} on any JSON parse error.
    """
    if not chunks:
        logger.warning(f"  [{rule_category}] No chunks retrieved — skipping extraction.")
        return {}

    # Build rule list for prompt
    rule_lines = []
    for rd in rule_definitions:
        rule_lines.append(
            f"  - {rd['id']}: {rd['description']} (unit: {rd['unit']})"
        )
    rules_text = "\n".join(rule_lines)

    # Build chunk context with authority labels
    excerpts = []
    for i, chunk in enumerate(chunks, 1):
        excerpts.append(
            f"[Excerpt {i} — Authority: {chunk['authority']}, "
            f"Doc: {chunk['doc_id']}, Page: {chunk['page_num']}]\n"
            f"{chunk['text']}"
        )
    context_text = "\n\n---\n\n".join(excerpts)

    system_prompt = (
        "You are a pharmaceutical compliance expert. "
        "Extract specific numeric thresholds and boolean rules "
        "from the provided policy document excerpts. "
        "Return ONLY a JSON object with rule values. "
        "If a threshold is not explicitly stated in the text, return null for that rule. "
        "Do not infer or estimate values not present in the text."
    )

    user_prompt = (
        f"Extract the following rules from these policy excerpts:\n\n"
        f"{rules_text}\n\n"
        f"Document excerpts:\n\n"
        f"{context_text}\n\n"
        f"Return a JSON object with one key per rule ID and its extracted numeric "
        f"or boolean value. Use null when the value is not explicitly stated. "
        f"Return JSON only. No explanation."
    )

    try:
        response = openai_client.chat.completions.create(
            model=EXTRACTION_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        )
        raw = response.choices[0].message.content
        extracted = json.loads(raw)
        logger.debug(f"  [{rule_category}] Extracted: {extracted}")
        return extracted
    except json.JSONDecodeError as e:
        logger.error(f"  [{rule_category}] GPT-4o returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"  [{rule_category}] Extraction failed: {e}")
        return {}


def reconcile_rules(
    extractions_by_authority: dict[str, dict],
    rule_id: str,
    rule_def: dict,
    chunks_by_authority: dict[str, list[dict]],
) -> dict:
    """
    Apply authority hierarchy and stricter-wins logic to produce a single
    effective threshold for rule_id.

    extractions_by_authority: {authority: {rule_id: value, ...}}
    chunks_by_authority:       {authority: [chunk, ...]}  — for source metadata
    Returns a complete rule dict ready for rules.json.
    """
    fallback_value = FALLBACK_RULES.get(rule_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Collect all non-null extracted values across authorities
    sources = []
    values_by_authority: dict[str, float | bool] = {}

    for authority in AUTHORITY_HIERARCHY:
        extracted = extractions_by_authority.get(authority, {})
        value = extracted.get(rule_id)
        if value is None:
            continue

        # Find best matching chunk for this authority as source reference
        auth_chunks = chunks_by_authority.get(authority, [])
        chunk_id = auth_chunks[0]["chunk_id"] if auth_chunks else ""

        sources.append({
            "doc_id":          auth_chunks[0]["doc_id"] if auth_chunks else "",
            "authority":       authority,
            "doc_type":        auth_chunks[0]["doc_type"] if auth_chunks else "",
            "chunk_id":        chunk_id,
            "extracted_value": value,
        })
        values_by_authority[authority] = value

    # Determine effective threshold
    nova_pharma_value = values_by_authority.get("Nova Pharma")
    oig_value         = values_by_authority.get("OIG")
    phrma_value       = values_by_authority.get("PhRMA")
    cms_value         = values_by_authority.get("CMS")

    # Pick industry/regulatory value following hierarchy
    # For numeric maximums: stricter = lower. For minimums: stricter = higher.
    # For booleans: True (prohibited/required) is always the stricter value.
    threshold_type = rule_def.get("threshold_type", "maximum")

    def is_stricter(a, b):
        """Return True if a is stricter than b for this threshold type."""
        if a is None:
            return False
        if b is None:
            return True
        if isinstance(a, bool):
            return a  # True = more restrictive for prohibited/required
        if threshold_type == "maximum":
            return a < b
        if threshold_type == "minimum":
            return a > b
        return False  # required/prohibited — handled via boolean logic

    # Authority hierarchy: OIG > Nova Pharma > PhRMA > CMS
    candidate_pairs = [
        ("OIG",         oig_value),
        ("Nova Pharma", nova_pharma_value),
        ("PhRMA",       phrma_value),
        ("CMS",         cms_value),
    ]
    effective_value  = None
    effective_source = None

    for authority, val in candidate_pairs:
        if val is None:
            continue
        if effective_value is None:
            effective_value  = val
            effective_source = authority
        elif is_stricter(val, effective_value):
            effective_value  = val
            effective_source = authority

    # Fall back to hard-coded default if nothing extracted
    used_fallback = False
    if effective_value is None:
        effective_value  = fallback_value
        effective_source = "fallback"
        used_fallback    = True
        if effective_value is not None:
            logger.warning(
                f"  [{rule_id}] No extracted value — using fallback: {effective_value}"
            )

    # Build reconciliation note
    if used_fallback:
        recon_note = "No explicit threshold found in policy documents; using fallback default."
    elif len(sources) == 1:
        recon_note = f"Single source ({sources[0]['authority']}); no reconciliation needed."
    else:
        auth_list = ", ".join(s["authority"] for s in sources)
        recon_note = (
            f"Multiple sources ({auth_list}). "
            f"{'Stricter-wins applied. ' if len(sources) > 1 else ''}"
            f"Effective source: {effective_source}."
        )

    return {
        "rule_id":              rule_id,
        "rule_name":            rule_def["name"],
        "category":             rule_def.get("category", ""),
        "threshold":            effective_value,
        "unit":                 rule_def["unit"],
        "threshold_type":       threshold_type,
        "sources":              sources,
        "nova_pharma_value":    nova_pharma_value,
        "industry_value":       phrma_value,
        "effective_threshold":  effective_value,
        "effective_source":     effective_source,
        "single_source":        len(sources) == 1,
        "reconciliation_note":  recon_note,
        "violation_type":       rule_def.get("violation_type", ""),
        "severity":             rule_def.get("severity", "medium"),
        "applies_to":           rule_def.get("applies_to", []),
        "used_fallback":        used_fallback,
        "extracted_at":         now_iso,
    }


def build_rules_registry() -> dict:
    """
    For each rule category: query Qdrant, extract thresholds per authority,
    reconcile, and assemble the full rules.json structure.
    """
    all_rules:          list[dict]  = []
    all_doc_ids:        set[str]    = set()
    total_chunks_queried: int       = 0

    stats = {
        "extracted":       0,
        "oig_precedence":  0,
        "nova_override":   0,
        "fallback_used":   0,
        "null_extraction": 0,
    }

    for cat in RULE_CATEGORIES:
        category     = cat["category"]
        query_text   = cat["query"]
        rule_defs    = cat["rule_ids"]
        rule_def_map = {rd["id"]: {**rd, "category": category} for rd in rule_defs}

        logger.info(f"── Category: {category} ──")
        logger.info(f"  Query: \"{query_text}\"")

        # ── Step 1: Query Qdrant per authority ──────────────────────────────
        chunks_by_authority: dict[str, list[dict]] = {}
        all_chunks: list[dict] = []

        for authority in AUTHORITY_HIERARCHY:
            auth_chunks = query_qdrant(query_text, top_k=5, filter_authority=authority)
            if auth_chunks:
                chunks_by_authority[authority] = auth_chunks
                all_chunks.extend(auth_chunks)
                for c in auth_chunks:
                    all_doc_ids.add(c["doc_id"])
                logger.info(f"  {authority}: {len(auth_chunks)} chunks retrieved")
            else:
                logger.info(f"  {authority}: 0 chunks")

        total_chunks_queried += len(all_chunks)

        if not all_chunks:
            logger.warning(f"  [{category}] No chunks retrieved — all rules use fallbacks.")
            for rule_def in rule_defs:
                rule = reconcile_rules({}, rule_def["id"], rule_def_map[rule_def["id"]], {})
                all_rules.append(rule)
                stats["fallback_used"] += 1
            continue

        # ── Step 2: Extract per authority ───────────────────────────────────
        extractions_by_authority: dict[str, dict] = {}

        for authority, auth_chunks in chunks_by_authority.items():
            logger.info(f"  Extracting [{authority}] ({len(auth_chunks)} chunks)...")
            extracted = extract_rules_from_chunks(
                auth_chunks, category, rule_defs
            )
            extractions_by_authority[authority] = extracted

            for rule_id in cat["extraction_keys"]:
                val = extracted.get(rule_id)
                if val is None:
                    stats["null_extraction"] += 1

        # ── Step 3: Reconcile per rule ──────────────────────────────────────
        for rule_def in rule_defs:
            rule_id = rule_def["id"]
            rule = reconcile_rules(
                extractions_by_authority,
                rule_id,
                rule_def_map[rule_id],
                chunks_by_authority,
            )
            all_rules.append(rule)
            stats["extracted"] += 1

            if rule["used_fallback"]:
                stats["fallback_used"] += 1
            elif rule["effective_source"] == "OIG":
                stats["oig_precedence"] += 1
            elif rule["effective_source"] == "Nova Pharma":
                stats["nova_override"] += 1

            logger.info(
                f"  {rule_id}: {rule['effective_threshold']} "
                f"({rule['effective_source']})"
            )

        logger.info("")

    now_iso = datetime.now(timezone.utc).isoformat()
    registry = {
        "metadata": {
            "version":               "1.0",
            "generated_at":          now_iso,
            "generated_by":          "business_rules_registry.py",
            "qdrant_collection":     QDRANT_COLLECTION,
            "total_chunks_queried":  total_chunks_queried,
            "total_rules_extracted": len(all_rules),
            "documents_used":        sorted(all_doc_ids),
        },
        "rules":          all_rules,
        "fallback_rules": FALLBACK_RULES,
    }

    logger.info("── Registry stats ──")
    logger.info(f"  Total rules:           {stats['extracted']}")
    logger.info(f"  OIG precedence:        {stats['oig_precedence']}")
    logger.info(f"  Nova Pharma override:  {stats['nova_override']}")
    logger.info(f"  Fallback used:         {stats['fallback_used']}")
    logger.info(f"  Null extractions:      {stats['null_extraction']}")

    return registry, stats


def save_rules_registry(registry: dict) -> str:
    """
    Write registry to compliance/rules.json.
    Creates compliance/ directory if it doesn't exist.
    Returns the file path as a string.
    """
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, default=str)
    logger.info(f"Rules registry saved to: {OUTPUT_PATH}")
    return str(OUTPUT_PATH)


def get_rule(
    rule_id: str,
    rules_json_path: str | None = None,
) -> dict:
    """
    Utility function imported by downstream tasks to retrieve a single rule.

    Loads rules.json, returns the rule dict for rule_id.
    Falls back to FALLBACK_RULES[rule_id] if:
      - rules.json doesn't exist
      - rule_id not found in registry
      - effective_threshold is None

    Usage:
      from pipelines.business_rules_registry import get_rule
      meal_limit = get_rule("MEAL_003")["effective_threshold"]  # 125.0
    """
    path = Path(rules_json_path) if rules_json_path else OUTPUT_PATH

    if not path.exists():
        logger.warning(
            f"rules.json not found at {path}. "
            "Run business_rules_registry.py first. Using fallback."
        )
        fallback_val = FALLBACK_RULES.get(rule_id)
        return {
            "rule_id":             rule_id,
            "effective_threshold": fallback_val,
            "effective_source":    "fallback",
            "used_fallback":       True,
        }

    with open(path, encoding="utf-8") as f:
        registry = json.load(f)

    for rule in registry.get("rules", []):
        if rule["rule_id"] == rule_id:
            # If extraction left a null threshold, fill from fallbacks section
            if rule.get("effective_threshold") is None:
                fallback_val = registry.get("fallback_rules", {}).get(rule_id)
                rule = {**rule, "effective_threshold": fallback_val, "used_fallback": True}
            return rule

    # rule_id not in registry — use fallback
    fallback_val = registry.get("fallback_rules", {}).get(rule_id, FALLBACK_RULES.get(rule_id))
    return {
        "rule_id":             rule_id,
        "effective_threshold": fallback_val,
        "effective_source":    "fallback",
        "used_fallback":       True,
    }


def main() -> None:
    logger.info("=" * 60)
    logger.info("business_rules_registry.py — Compliance Rules Registry")
    logger.info("=" * 60)
    logger.info(f"Qdrant:   {QDRANT_HOST}:{QDRANT_PORT}/{QDRANT_COLLECTION}")
    logger.info(f"Extractor: {EXTRACTION_MODEL}  Embedder: {EMBEDDING_MODEL}")
    logger.info(f"Output:    {OUTPUT_PATH}")
    logger.info("")

    # Verify Qdrant connection and collection
    try:
        info = qdrant_client.get_collection(QDRANT_COLLECTION)
        logger.info(
            f"Qdrant collection '{QDRANT_COLLECTION}': "
            f"{info.points_count} points, status={info.status.value}"
        )
        if info.points_count == 0:
            logger.error(
                "Collection is empty. Run embed_policy_docs.py first."
            )
            sys.exit(1)
    except Exception as e:
        logger.error(f"Cannot connect to Qdrant: {e}")
        sys.exit(1)

    logger.info("")

    registry, stats = build_rules_registry()
    output_path = save_rules_registry(registry)

    logger.info("")
    logger.info("=" * 60)
    logger.info("REGISTRY COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Output:               {output_path}")
    logger.info(f"  Total rules:          {registry['metadata']['total_rules_extracted']}")
    logger.info(f"  Chunks queried:       {registry['metadata']['total_chunks_queried']}")
    logger.info(f"  Documents used:       {', '.join(registry['metadata']['documents_used'])}")
    logger.info(f"  OIG precedence:       {stats['oig_precedence']} rules")
    logger.info(f"  Nova Pharma override: {stats['nova_override']} rules")
    logger.info(f"  Fallback used:        {stats['fallback_used']} rules")
    logger.info(f"  Null extractions:     {stats['null_extraction']}")
    logger.info("")
    logger.info("Verify: cat compliance/rules.json | python3 -m json.tool")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
