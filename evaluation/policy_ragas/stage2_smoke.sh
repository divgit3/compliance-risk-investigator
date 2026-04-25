#!/usr/bin/env bash
# Stage 2 smoke test — 12 queries across 4 categories.
# Run from repo root after Docker stack is up.
# Outputs one JSON per query to evaluation/policy_ragas/stage2_smoke_results/.

set -e

OUTDIR="evaluation/policy_ragas/stage2_smoke_results"
mkdir -p "$OUTDIR"

ask () {
  local id="$1"
  local question="$2"
  local outfile="$OUTDIR/${id}.json"
  echo "→ $id: $question"
  curl -s -X POST http://localhost:8000/policy/query \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$question" '{question: $q}')" \
    | python -m json.tool > "$outfile"
  echo "   confidence: $(python -c "import json; print(json.load(open('$outfile'))['confidence'])")"
  echo "   limitations: $(python -c "import json; print(len(json.load(open('$outfile'))['data_limitations']))" ) entries"
  echo
}

# ── Rule-backed (well-formed, scope matches a real rule) ───────────────────────
ask "rb_01_lunch_meal_limit"      "What is the meal limit for lunch?"
ask "rb_02_annual_compensation"   "What is the annual cap on HCP compensation?"
ask "rb_03_speaker_fmv"           "What is the speaker FMV ceiling?"
ask "rb_04_repeat_speaker"        "What is the repeat-speaker threshold for a single speaker?"

# ── Pure-retrieval (answer should come from PDF text, not lookup_rule) ─────────
ask "ret_01_fmv_definition"       "What does the PhRMA Code say about determining fair market value for HCP services?"
ask "ret_02_oig_needs_assessment" "What does the OIG Compliance Program Guidance say about needs assessments for HCP arrangements?"
ask "ret_03_speaker_fraud_risk"   "What are the OIG's listed risk indicators for speaker program fraud?"

# ── Unanswerable (well-formed but answer not in corpus) ────────────────────────
ask "un_01_state_specific"        "What is the meal limit for HCPs in California specifically?"
ask "un_02_telehealth_caps"       "What is Nova Pharma's policy on telehealth-only HCP interactions?"

# ── False-premise (premise doesn't match any real rule) ────────────────────────
ask "fp_01_annual_meal"           "What is the annual meal cap for HCPs?"
ask "fp_02_quarterly_speaker"     "What is the quarterly speaker fee limit?"
ask "fp_03_per_specialty_cap"     "What is the per-specialty compensation cap for cardiologists?"

echo "Done. Results in $OUTDIR/"
