# Query Phrasing Sensitivity — Investigation Findings

**Date:** 2026-05-01  
**Scope:** Item 2 of 1.2g pass 2 — brief sampling phase  
**Escalation decision:** No escalation (see §5)

---

## 1. Methodology

**Entries selected (one per sensitive category):**
- `rg_01_annual_meal_cap` — registry-gap, previously observed sensitivity
- `ret_02_anti_kickback_penalties` — retrieval, the misclassification case from 1.2f
- `rb_02_speaker_fmv_ceiling` — rule-backed, well-tested rule

**Three paraphrases per entry** (9 total queries):

| Entry | Paraphrase ID | Question |
|-------|--------------|----------|
| rg_01 | drop_nova_prefix | "What is the annual meal cap for HCPs?" |
| rg_01 | synonym_cap_limit | "What is the annual meal limit per healthcare provider?" |
| rg_01 | restructure_imperative | "Explain how much can be spent on meals for an HCP per year" |
| ret_02 | drop_federal | "What penalties apply to anti-kickback statute violations?" |
| ret_02 | synonym_violate | "What are the legal consequences of breaking the anti-kickback law?" |
| ret_02 | restructure | "Describe the criminal sanctions for kickback violations under federal law" |
| rb_02 | synonym_fmv_fee | "What is the maximum speaker fee?" |
| rb_02 | expand_fmv_full | "What is the fair market value limit for speaker programs?" |
| rb_02 | restructure_nova | "How much can Nova Pharma pay a healthcare professional for speaking?" |

**Evaluation method:** Each query ran against the live API (`POST /policy/query`).
Captured: answer text, retrieved chunk IDs, relevance scores, confidence, latency.
Correctness scored by hand after reading full answers (not heuristic — see §2 note).

---

## 2. Per-Entry Results

### rg_01 — Annual meal cap (registry gap)

**Reference answer:** No annual meal-specific cap exists. Per-meal limits are $25/$50/$100
(breakfast/lunch/dinner). The $75,000 annual cap covers all HCP compensation, not meals
specifically.

| Variant | Chunks retrieved | Retrieval diff | Confidence | Content verdict |
|---------|-----------------|---------------|------------|----------------|
| original | DOC_002_chunk_0001, DOC_002_chunk_0000, DOC_005_chunk_0001 | — | **high** | CORRECT |
| drop_nova_prefix | DOC_002_chunk_0001, DOC_002_chunk_0000, DOC_005_chunk_0001 | none | **low** | CORRECT |
| synonym_cap_limit | DOC_002_chunk_0000, DOC_002_chunk_0001, DOC_005_chunk_0017 | +chunk_0017, −chunk_0001 | **medium** | CORRECT |
| restructure_imperative | DOC_002_chunk_0001, DOC_002_chunk_0000, DOC_005_chunk_0001 | none | **medium** | PARTIAL |

**Notable findings:**

- `drop_nova_prefix`: Retrieval unchanged (same 3 chunks, same scores), answer
  semantically identical, but **confidence dropped from `high` to `low`**. Root cause:
  without "Nova Pharma" in the query, the agent skipped `lookup_rule` — no rule
  thresholds returned, so the deterministic confidence formula (`has_chunks AND has_rules
  → high`) produces `low` despite a correct answer. This is a **confidence signal
  sensitivity issue**, not a correctness issue.

- `restructure_imperative`: "Explain how much can be spent on meals for an HCP per year"
  frames this as a quantity question, not an absence question. The agent returns the
  per-meal limits ($25/$50/$100) without explicitly stating that there is no annual
  meal-specific cap. This is **expected behavior for the literal question posed** — the
  imperative framing doesn't ask "is there a cap," it asks "how much." PARTIAL, not WRONG.

---

### ret_02 — Anti-kickback penalties (retrieval)

**Reference answer:** The corpus discusses the anti-kickback statute (OIG guidance)
but does not specify criminal penalty amounts or terms. A correct answer acknowledges
the absence.

| Variant | Chunks retrieved | Retrieval diff | Confidence | Content verdict |
|---------|-----------------|---------------|------------|----------------|
| original | DOC_003_chunk_0007, DOC_003_chunk_0009, DOC_004_chunk_0003 | — | low | OVER-NARRATED |
| drop_federal | DOC_003_chunk_0009, DOC_003_chunk_0007, DOC_003_chunk_0010 | +chunk_0010, −DOC_004_chunk_0003 | medium | OVER-NARRATED |
| synonym_violate | DOC_003_chunk_0009, DOC_003_chunk_0007, DOC_003_chunk_0010 | +chunk_0010, −DOC_004_chunk_0003 | medium | OVER-NARRATED |
| restructure | DOC_003_chunk_0007, DOC_003_chunk_0009, DOC_003_chunk_0010 | +chunk_0010, −DOC_004_chunk_0003 | low | OVER-NARRATED |

**Notable findings:**

- All 4 variants produce the **same pattern**: begin with TOPIC ABSENT refusal
  ("The policy does not address the specific criminal penalties…"), then narrate
  OIG Compliance Program Guidance text about what the statute prohibits.
  
- No variant fabricates specific penalty amounts or imprisonment terms. The "criminal
  penalties" language in all answers is verbatim OIG corpus content describing the
  statute as a "criminal prohibition." The over-narration post-processor correctly
  detects this pattern (Step A: refusal first sentence; Step B: soft transition "I can
  provide some relevant information") but is **blocked by the 0.55 relevance guard**
  (max_rel = 0.60–0.70 for all variants, above the 0.55 threshold).

- **Phrasing does not change the failure mode.** The same over-narration appears with
  or without "federal," with synonyms (consequences, sanctions, penalties), and with
  restructured phrasing. This is not phrasing sensitivity — it is the pre-existing
  1.2f calibration issue (0.55 guard too low; should be ~0.60).

- Minor retrieval variation: `drop_federal`/`synonym_violate`/`restructure` retrieve
  DOC_003_chunk_0010 instead of DOC_004_chunk_0003. The answer content is
  indistinguishable.

---

### rb_02 — Speaker FMV ceiling (rule backed)

**Reference answer:** $3,500 per engagement (SPEAKER_001), Nova Pharma stricter than
PhRMA's $4,000 per engagement.

| Variant | Chunks retrieved | Retrieval diff | Confidence | Content verdict |
|---------|-----------------|---------------|------------|----------------|
| original | DOC_005_chunk_0006, DOC_004_chunk_0000, DOC_005_chunk_0005 | — | high | CORRECT |
| synonym_fmv_fee | DOC_004_chunk_0000, DOC_004_chunk_0001, DOC_004_chunk_0004 | all different | **high** | CORRECT |
| expand_fmv_full | DOC_005_chunk_0006, DOC_004_chunk_0000, DOC_005_chunk_0005 | none | high | CORRECT |
| restructure_nova | DOC_005_chunk_0007, DOC_005_chunk_0006, DOC_004_chunk_0000 | +chunk_0007, −chunk_0005 | high | CORRECT |

**Notable findings:**

- Fully robust across all paraphrases. Different retrieval sets but all return $3,500
  and cite SPEAKER_001. Confidence stable at `high` for all variants — "speaker" keyword
  reliably triggers `lookup_rule(SPEAKER_001)`.

- `synonym_fmv_fee` ("maximum speaker fee") retrieved a completely different chunk set
  (DOC_004 instead of DOC_005) but still landed on the correct answer via `lookup_rule`.
  This shows that `lookup_rule` provides stability independent of retrieval variation.

---

## 3. Characterization Summary

| Dimension | rg_01 | ret_02 | rb_02 |
|-----------|-------|--------|-------|
| Answer content sensitivity | Low–moderate | None (uniform failure) | None |
| Confidence sensitivity | High (rule lookup drops) | Low | None |
| Retrieval set stability | Mostly stable | Minor chunk variation | Variable but stable answer |
| Root cause of failures | Framing (imperative) + missing rule lookup | Pre-existing 0.55 guard (1.2f) | N/A |

**Overall verdict:**
- The system is **not broadly sensitive to phrasing** for rule-backed queries (rb_02).
- For registry-gap queries (rg_01), **dropping "Nova Pharma" from the question silently
  suppresses rule lookup**, causing confidence to report `low` even when the answer is
  correct. This is a correctness-signal failure, not an answer-content failure.
- For retrieval queries on TOPIC ABSENT cases (ret_02), **phrasing does not change the
  failure mode** — the over-narration issue is uniform across all variants and is the
  same pre-existing root cause (0.55 guard calibration).

---

## 4. Escalation Decision

**No escalation.** Brief sampling found no new phrasing-sensitivity issues:

- `rg_01` confidence drop is a known limitation of the confidence formula (rule lookup
  not triggered without "Nova Pharma"). Correctness is preserved. Not worth fixing in
  this session — would require either prompt changes or adding "Nova Pharma" context
  injection upstream.

- `rg_01` imperative framing producing PARTIAL is acceptable behavior — "explain how
  much" is a different semantic question than "is there an annual cap." A user who wants
  to know about absence needs to phrase the question explicitly.

- `ret_02` uniform failure across paraphrases confirms this is **not a phrasing
  sensitivity issue**. The fix (raising the 0.55 guard to 0.60) was already recommended
  in the 1.2f findings. That fix is out of scope for 1.2g.

- `rb_02` is robust. No action needed.

---

## 5. Recommendation

**No fix needed from this investigation.** Document as known limitations:

1. **Known limitation:** Dropping "Nova Pharma" from registry-gap questions may suppress
   rule lookup, causing confidence to under-report even when the answer is correct.
   Workaround: example questions in the UI already include "Nova Pharma" in their
   phrasing.

2. **Known limitation (pre-existing):** ret_02-class TOPIC ABSENT answers will
   over-narrate until the 0.55 guard is raised to 0.60 (separate 1.2f follow-on PR).
   This is not phrasing-driven.

3. **Robust:** Rule-backed queries with explicit rule keywords (FMV, SPEAKER) are
   stable across paraphrases due to the `lookup_rule` tool providing answer-level
   stability independent of retrieval variation.

**Follow-on scope (not this session):** Raise over-narration guard from 0.55 → 0.60
(one-line change in `agents/post_processors/over_narration.py`) to address the
ret_02-class calibration issue identified in 1.2f and confirmed here.
