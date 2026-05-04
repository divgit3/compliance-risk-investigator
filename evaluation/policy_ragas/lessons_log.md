# Policy Agent — Lessons Log

**Purpose.** Raw notes captured close to the work, intended as source material
for the RAG lessons-learned Medium article (target: May 2026). Not polished
prose. Not a methodology doc. Not a backlog. The stuff in here is the kind
of thing that's obvious now and impossible to reconstruct in two months.

When drafting the article: read this top to bottom first, then decide which
threads are worth pulling. Some entries are full anecdotes; some are seeds.

---

## 2026-04-25 — Scope conflation in the Policy Agent

### Symptom

User asked: "What's the annual meal cap for HCPs?"
Agent answered: "$25 breakfast / $50 lunch / $100 dinner..." reframed as if
those were the annual cap.
UI showed: Confidence: high, three policy citations (relevance 0.02–0.03),
real rule IDs (MEAL_001–004).

The answer was confident, internally consistent, and wrong. The user — me,
checking my own product — caught it because I happened to know the rules
registry has no annual meal cap. A regulator-facing user would not have.

### What I initially called the bug

"Premise hallucination" — the agent was assuming the user's framing was
valid and synthesizing an answer to a question that didn't have one.

### What the bug actually was

Scope conflation. COMP_001 is a real rule (Annual HCP Compensation Cap,
$75,000). MEAL_001–004 are real rules (per-meal limits). The agent
retrieved MEAL_001–004 — never queried for "annual" — and presented them
as if they answered the annual-scope question. COMP_001 was real and
present in the registry the whole time; it just never made it into the
retrieved set because the agent's `lookup_rule` query was "meal limit",
which scored zero on COMP_001's keyword overlap.

So: not fabrication. Not premise hallucination in the literal sense.
Misapplication of real rules to a question whose scope dimension was
silently dropped during retrieval.

This distinction matters for the article. "The model hallucinated" is
the easy story. The real story is more interesting and more useful:
the model retrieved real things and applied them to a question they
didn't fit, because nothing in the system forced it to check whether
the scope of what it retrieved matched the scope of what was asked.

### The fix — three layers, all of them necessary

1. **Tool output got an explicit `scope` field.** `lookup_rule` now
   returns `scope: {time_scope, entity_scope, threshold_type}` for
   every rule, derived deterministically from rule_name and category
   in a helper function `_infer_scope`. Before this, scope was implicit
   in `rule_name` ("Meal Limit — Breakfast" implies per-meal) and
   `unit` ("USD/hour" implies hourly), which meant the agent had to
   parse natural language to reason about scope. Making scope a
   structured field made it parseable.

2. **System prompt added a CRITICAL block.** Required the agent to
   identify the question's claimed scope, check whether retrieved
   rules match, and if no rule matches, begin the answer by saying so
   instead of reframing the question. Included a worked example of
   the exact failing case. Also corrected the prompt's own factual
   errors — the original prompt said "$25 internal / $50 external /
   $100 international" which is not what the rules say. It says
   breakfast/lunch/dinner. The bug was in the prompt's mental model,
   and the agent faithfully reproduced it.

3. **Deterministic safety net.** Added `_detect_scope_mismatch` that
   checks for explicit time-scope words in the question (annual,
   monthly, weekly, daily) and returns a warning if no retrieved rule
   has that time scope. The warning gets prepended to `data_limitations`
   and confidence gets downgraded from high to medium. This is the
   belt-and-suspenders layer for cases where the prompt fails.

The three layers do different jobs. The tool output makes scope
legible. The prompt asks the agent to reason about it. The safety net
catches cases where the agent ignores the prompt. Removing any one of
the three would have made the fix fragile.

### What I didn't expect — retrieval strategy shifted

When I tested the fix, I noticed the agent's `lookup_rule` query had
changed from "meal limit" (failing case) to "annual meal cap" (fixed
case). The prompt change didn't just operate as an output filter at
answer time; it propagated backward through the agent's tool-use
planning. Telling the model "verify scope before answering" made the
model preserve scope words in its tool queries, which retrieved better
rules in the first place.

This is interesting for the article. Prompt instructions about
*reasoning discipline at the output stage* changed *retrieval behavior
at the input stage*, even though I never instructed it to do that.
ReAct agents seem to plan retrievals based on what they expect to
need to reason about. If the prompt sets a higher reasoning bar, the
retrievals get richer to support it.

I have not seen this written about anywhere, though I haven't searched
hard. Worth a paragraph either way.

### The deployment-layer debugging story

This is the part that actually took the longest, and the part I'd
otherwise forget by May.

Cursor reported all three changes applied. The reviewer noticed
`_parse_rule_thresholds` was stripping the new `scope` field and
fixed it (I missed this in the spec — important meta-lesson about
LLM-generated implementation specs having data-flow gaps that need
human review). Verified the code on disk was correct via direct
import + tool invocation. Hit the API. Fix didn't take. Same broken
answer, same "Confidence: high", no scope warning in data_limitations.

Spent ~10 minutes wrong-diagnosing. Suspected the prompt wasn't strong
enough; suspected the safety net had a logic bug; suspected
gpt-4o-mini was ignoring the scope-verification instructions.

Then: `lsof -i:8000` showed the server was `com.docke` — the
FastAPI service was running inside Docker. `pkill uvicorn` had been
killing nothing the whole time because uvicorn wasn't on the host.
`__pycache__` cleanup did nothing because the container had its own
filesystem. Every "fix" was being tested against pre-fix code.

Container rebuild → fix took on first try.

This is the article's most useful anecdote IMO. People building
RAG/agent systems hit "it works in tests but not in deployment" all
the time and the bug is almost always in the deployment layer, not
the model. The diagnostic sequence — direct tool invocation to
isolate code-on-disk vs running-process, then `lsof` to find what's
actually listening — generalizes well.

### What worked about the iteration loop

Every diagnostic I ran returned a structured artifact (curl output
JSON, `lsof` output, direct tool-call output). I never had to ask
"did the change take effect?" — the JSON either had the `scope` field
or it didn't. This is partly because the agent already returned
structured PolicyAnswer objects, but partly because I built the
diagnostic instrumentation to print the same structured fields.

When testing LLM-backed systems, the best signal is in the
deterministic plumbing around the LLM, not in the LLM's outputs.
The prompt's verification step succeeded (narrative answer was
correct) but the safety net's mismatch warning didn't fire (because
COMP_001 was retrieved and time_scope=annual matched). Both behaviors
were correct given their inputs; the difference was visible in the
JSON, not in the prose.

### What I'd write differently if doing it again

The original spec I wrote for Cursor missed that `_parse_rule_thresholds`
needed updating to pass `scope` through. The reviewer caught it. Going
forward when writing implementation specs that depend on data flowing
across layers, list the layers explicitly and verify each preserves the
relevant fields. The fix had three changes; the working fix had four.
The fourth was structural and the spec didn't name it.

### Files touched

- `agents/tools/policy_tools.py` — added `_infer_scope`, integrated into
  `lookup_rule` output dict
- `agents/policy_agent.py` — system prompt, `_detect_scope_mismatch`,
  wiring in `query()`, `_parse_rule_thresholds` pass-through (the
  one I missed in the spec)
- Removed dead code: duplicate `import os` in `policy_tools.py`,
  unused `_SYSTEM_PROMPT` module constant in `policy_agent.py`

### The four queries used to verify

1. `What is the annual meal cap for HCPs?` — false-premise, expected
   scope-mismatch handling
2. `What is the meal limit for lunch?` — control: rule-backed, no scope
   words
3. `What is the annual cap on HCP compensation?` — control: rule-backed,
   "annual" matches COMP_001 cleanly
4. `What is the speaker FMV ceiling?` — control: rule-backed, no scope
   words

All four behaved correctly post-fix. Full curl outputs preserved in
chat history; not duplicating here.

---

## Backlog — observations to address later, captured here so they don't get lost

### Fallback laundering

The Speaker FMV query (control 4) produced this in the narrative
answer: "the PhRMA Code allows for a maximum of $4,000 per engagement."
But $4,000 is not a value PhRMA publishes. It's the value sitting in
`fallback_rules` for SPEAKER_001 in `compliance/rules.json` — a default
the system uses when PhRMA's actual value can't be found in documents.

The narrative answer presents the fallback as if it were a real PhRMA
figure. The structured fields are honest about it (`authority: fallback`
shows up in rule_thresholds for some rules), but the narrative laundering
strips that signal.

This is a different bug class than scope conflation. Call it "fallback
laundering": values labeled "fallback" or "default" in structured data
get presented as authoritative in generated prose because the model
treats every field as roughly equivalent context.

Likely affects multiple rules — anywhere `fallback_rules` populates a
threshold that gets surfaced as `phrma_equivalent` in `lookup_rule`'s
output. The label "phrma_equivalent" itself is part of the problem;
the field is named as if it's a PhRMA value, but its actual provenance
is "fallback when PhRMA wasn't extractable."

Phase 5 candidate fix:
- Rename `phrma_equivalent` to something honest like `industry_proxy`
  or `phrma_or_fallback`
- In the `nova_vs_phrma` comparison, include a `proxy_source` field
  ("phrma" | "fallback")
- Update prompt to tell agent to attribute carefully when proxy_source
  is "fallback"

Don't fix now. Surface in the article as a related-but-separate bug
class that the same architectural fix (explicit scope) would NOT solve.

### RAG layer is decorative

Across all four control queries, `relevant_chunks` had relevance scores
of 0.02–0.04. Search results were essentially noise — CMS data
dictionary entries showing up on speaker FMV questions, OIG fraud
alerts on meal questions. The agent answered correctly anyway because
the structured rule registry (`lookup_rule`) carried the real signal.

The vector search exists. It runs on every query. Its outputs get
parsed, displayed in the UI as Policy Citations, and counted toward
confidence scoring. But for rule-backed questions — which appears to
be the dominant query type — it contributes nothing to the actual
answer.

This was already noted in Stage 1 of the RAGAS rebuild work. Worth
re-noting here because it now has four more data points behind it.

For the article: the project is positioned as a RAG-over-policy-docs
product. In practice it's a structured-lookup product with vestigial
RAG. That's not necessarily bad — for rule-backed questions, the
structured lookup is the right tool — but it changes the story.
The honest framing is something like: "I built a RAG pipeline; turned
out for the dominant query type, the R was decorative. The interesting
question becomes which queries actually need retrieval, and how to
route between paths."

This connects to the path-tagging discipline planned for the Stage 2
golden dataset (rule-backed vs pure-retrieval vs unanswerable vs
false-premise). The dataset will surface, with numbers, exactly how
many of the questions that look like RAG questions actually exercise
RAG.

### Scope mismatch at entity-scope granularity

The current `_detect_scope_mismatch` only checks `time_scope`
(annual / monthly / weekly / daily). It doesn't check `entity_scope`
(per_meal / per_event / per_engagement / per_hcp / per_hcp_aggregate /
per_attendee / unspecified).

The annual-meal-cap query happened to fail at the time-scope dimension,
which is what the safety net catches. But there are other failure
modes the same conceptual bug could produce — e.g., "what's the
per-attendee cap on speaker fees" might conflate per_attendee scope
(VENUE_003 is per_attendee) with per_engagement scope (SPEAKER_001 is
per_engagement).

Not extending the safety net now because:
- I haven't observed entity-scope conflation actually happening
- Adding it pre-emptively is over-engineering
- The Stage 2 golden dataset will surface whether this matters

If a Stage 2 false-premise entry trips entity-scope conflation, extend
the safety net then. Note in the article that the safety net is a
heuristic, not a complete solution, and was deliberately kept narrow.

---

## Article shape — rough sketch, do not commit to this

The arc, in order it would read:

1. **Hook**: "Confidence: high. Three real citations. Three real rule
   IDs. The answer was wrong." Show the screenshot. Let the reader feel
   the wrongness.
2. **Why this is the failure mode that scares me**: regulator-facing
   tools that confidently misanswer. Stakes section.
3. **The misdiagnosis**: I called it premise hallucination. It wasn't.
   Show what closer reading revealed (COMP_001 was real, just
   misapplied).
4. **The actual bug**: scope conflation. Define it. Show how rule_name
   and unit make scope implicit, and how implicit scope gets dropped
   silently during retrieval.
5. **The three-layer fix**: structured scope field, prompt verification,
   deterministic safety net. Why all three are needed; what each does
   alone is insufficient.
6. **The unexpected finding**: prompt change shifted retrieval strategy.
   Show the before/after `lookup_rule` queries.
7. **The deployment-layer story**: container served stale code; lsof
   diagnosis. The "it works on disk but not in production" anecdote.
8. **What this generalizes to**: separate the LLM from the plumbing
   when debugging; structured fields beat instructions; safety nets
   for prompt-only fixes.
9. **What it doesn't solve**: fallback laundering, decorative RAG,
   entity-scope conflation. Honest about what's still broken.

Length target: somewhere between RAG-lessons-Compliance-Risk-Investigator-
draft length and a long Medium piece. Not a research paper. Not a tweet
thread. Probably 2,000–3,000 words.

---

---

## 2026-04-25 (later same day) — Stage 2 smoke test surfaces three more bug classes

After the time-scope conflation fix, ran a 12-query smoke test across four
question categories (rule-backed, pure-retrieval, unanswerable, false-premise)
to gut-check the agent before authoring a RAGAS golden dataset. The smoke
test revealed three distinct bugs the time-scope work hadn't addressed.

### Finding 1 — Prompt-based generalization is dimension-bounded

The time-scope fix worked because the system prompt's worked example was
about time-scope conflation ("annual meal cap" → no annual rule exists).
Stage 2 tested:

- Time-scope conflation: fp_01 (annual meal), fp_02 (quarterly speaker)
  — both handled correctly. The agent generalized within the time-scope
  dimension.
- Entity-scope conflation: fp_03 (per-specialty cap for cardiologists) —
  pre-fix smoke test handled this correctly, possibly because the question
  phrasing ("does not define a specific...") matched the prompt's worked-
  example structure.
- Jurisdictional conflation: un_01 (meal limit in California specifically)
  — handled INCORRECTLY in pre-fix smoke test. Agent silently dropped
  "California" and answered with general meal limits, framed as if they
  were the California-specific answer.

The lesson: prompt-pattern-matching generalizes within a dimension but not
across dimensions. The model learns "watch out for the scope dimension you
showed me" rather than "watch out for any scope dimension." Adding worked
examples for every dimension we can think of is fragile maintenance; we
need something more structural.

This is the spine of one of the article's strongest paragraphs. "I taught
the model to handle one kind of scope conflation by example; it generalized
within that kind beautifully and not at all to other kinds."

### Finding 2 — Knowledge leakage on retrieval failure

ret_02 (OIG needs assessment) and ret_03 (OIG speaker fraud risk indicators)
both produced answers that started honestly ("specific excerpts were not
retrieved") and then proceeded to answer anyway from training-data knowledge.

This is a different bug class than scope conflation. Scope conflation
produces wrong answers from real rules. Knowledge leakage produces real-
knowledge answers presented as if they came from the corpus. For a
compliance product, knowledge leakage is the more concerning failure
mode — the user can't tell what came from the corpus and what came from
the model's prior training. The training-data answer is probably 95%
correct on OIG topics, but in compliance you don't get to be 95% correct.

### Finding 3 — Calibration on predictions

Before running the smoke test I made predictions for 5 of the queries
(rb_04, ret_01, ret_03, un_01, un_02). Got 3 of 5 correct. The wrong
predictions surfaced new information rather than being noise.

- rb_04 prediction (clean SPEAKER_003 answer): WRONG. Agent cited SPEAKER_002
  ("Max Speaker Events Per Year") not SPEAKER_003 ("Repeat Speaker Threshold").
  Both have threshold 6, so threshold was right but rule_id was wrong.
  Agent picked the rule whose name contained the literal word "year" because
  the question said "per year." Didn't notice SPEAKER_003 IS the rule literally
  named "Repeat Speaker Threshold." This is a different bug — call it
  "rule_id picking by surface match" — and not worth fixing now.

- ret_01 prediction (SPEAKER_001 padding, no PDF grounding): HALF-WRONG.
  Agent did NOT pad with $3,500 — it correctly said "PhRMA Code does not
  explicitly define the process for determining FMV." Better than expected.
  Retrieval-path handling is more honest than my model assumed.

Calibration value: my mental model of how this system fails is reasonable
but incomplete. Worth noting in the article — predictions become useful
even when wrong if the wrongness is informative.

### The fix design — three layers, asymmetric architectures

For Bug 1 (generalized scope conflation): schema-exposure tool plus
deterministic marker fallback. The tool (`list_rule_dimensions`) returns
the registry's actual scope dimensions plus a hardcoded list of dimensions
it does NOT segment by (jurisdiction, hcp_specialty, hcp_role,
drug_or_product, patient_population). The agent calls it when it suspects
a scope qualifier exists. The marker fallback fires deterministically on
state names, specialty names, and role markers, with the warning text
explicitly noting "safety net (not schema tool) caught this" so the
dataset can later distinguish "agent recognized" from "fallback caught it."

For Bug 2 (knowledge leakage): cross-model-class groundedness judge.
Agent runs on gpt-4o-mini; judge runs on gpt-4o. Same vendor, different
model class, different post-training, different failure modes. Cross-vendor
(Claude or Gemini judging GPT) would be stronger. I didn't have an
Anthropic API key set up so I used the cross-class-within-vendor compromise.
Documented honestly in the article: "I used cross-model-class judging as
a pragmatic compromise; the ideal would be cross-vendor; I'd take that
upgrade if doing this again."

The asymmetry between Bug 1 and Bug 2 fixes is itself worth a paragraph.
For Bug 1, an LLM judge would be heavy mechanism for a structural problem
(scope dimension recognition is a fact-checking task, not a reasoning task).
For Bug 2, a hardcoded marker check would miss every silent hallucination
where the agent doesn't admit failure first. Different tools for different
problems. Engineering judgment, not aesthetic consistency.

### The data-flow gap that bit us — twice

The time-scope fix had a data-flow gap: `_parse_rule_thresholds` was
stripping the new `scope` field. Reviewer (me) caught it during the
implementation. Should have been in the spec.

The judge fix had a data-flow gap too: `_judge_groundedness` took
`citations` and `rule_thresholds` as grounding sources but not
`nova_vs_phrma`. So every answer that cited a PhRMA value (which the
agent gets from `nova_vs_phrma`, not from rule thresholds directly)
got false-positive-flagged as ungrounded. Surfaced when the post-fix
smoke test showed `confidence=low` on every rule-backed query. Spent
real time confused before noticing the judge was right — it just
wasn't being shown the data.

The lesson: when adding a new component that consumes state, list every
producer of state the component might draw from and verify each one
flows through. I didn't do this for either fix. Would have saved a
debugging round on each.

For the article: "the bug wasn't in the model; the bug was in what I
showed the model" is a generalizable observation about LLM systems
that's worth its own paragraph. LLM components fail in two distinct
ways — the model gets it wrong, and we don't give the model what it
needs to get it right. The latter looks like the former until you
trace the data flow.

### Cross-model-class judge calibration unevenness

After fixing the data-flow gap, the judge correctly grounds rb_01
(lunch limit) and rb_03 (speaker FMV) but flags rb_02 (annual
compensation) and rb_04 (repeat speaker) as ungrounded with
similar grounding patterns.

rb_02's narrative: "$75,000 per program year, rule ID: COMP_001."
The rule_thresholds payload contains COMP_001 with threshold "75000 USD"
and authority "Nova Pharma." The judge says "retrieved content does
not include specific information about Nova Pharma's internal policy."
The judge is being literal about the prose phrasing — "internal policy"
isn't in the rule's prose, so the claim "as specified in the internal
policy" gets flagged.

rb_01's narrative says "$50 per meal, rule ID MEAL_002, stricter than
PhRMA's $75." Judge grounds it correctly. Same architectural pattern,
different judgment.

The judge is sometimes strict, sometimes lenient, on similar grounding
patterns. This is a known LLM-judge calibration issue and not unique
to our setup. Not catastrophic — the answers themselves are correct;
the judge is just unevenly suspicious. RAGAS will give us proper
calibrated metrics; the inline judge was insurance.

For the article: this is a useful "and here's what didn't fully work"
section. Cross-model-class judging buys some asymmetry, but doesn't
buy stable calibration. Cross-vendor would probably be more consistent.
Worth knowing the tradeoff before recommending the pattern.

### The three-round meta-observation — when to stop fixing

Three rounds of fixes today: time-scope conflation → generalized scope
+ knowledge leakage → judge data-flow gap. Each fix surfaced a new bug
the previous fix's testing revealed. The instinct after each round was
"now fix this new bug too." That instinct, on a project with a real
deadline, is the trap.

The pivot point came when I (Claude) caught myself proposing a fourth
round to fix the judge calibration unevenness. The right move was:
stop, recognize the inline judge is insurance not infrastructure,
recognize that RAGAS itself provides proper groundedness measurement,
and let the dataset surface bugs quantitatively rather than continuing
to fix what we think matters without quantitative evidence.

Article framing: "I built three layered fixes; the third had a data-
flow bug; I deferred further fixing and let RAGAS measure ground truth
instead." This is an engineering judgment story (choose what to measure,
not what to fix) rather than a perfectionism story (fix everything
before measuring). The first reads as senior; the second reads as
junior.

### Where the agent ended up

After all fixes and one judge data-flow correction:

- 11 of 12 smoke test narratives are factually correct. Only ret_03 still
  exhibits knowledge-leakage prose, and the inline judge correctly flags
  that case as ungrounded.
- The schema-exposure tool fired on fp_03 (cardiologists case) and the
  agent's narrative correctly used `dimensions_absent`. The general
  approach worked for this case.
- The marker safety net fired on un_01 (California) and fp_03, providing
  defense in depth. Both layers visibly contributed.
- Cross-model-class judge correctly grounds 4 of 12 (rb_01, rb_03, ret_01,
  the rest are flagged either correctly or due to calibration unevenness).
- Confidence calibration is imperfect but the structured signals
  (data_limitations, groundedness_check) are correct.

This is the state we proceed to RAGAS dataset construction with. Known
imperfections documented; no further fixes before measurement.

---

## Article shape — updated after today's full session

Rewriting the rough sketch from earlier with today's material:

1. **Hook** — "Confidence: high. Three real citations. Three real rule
   IDs. The answer was wrong." Show the screenshot. Let the reader feel
   the wrongness.

2. **Why this is the failure mode that scares me** — regulator-facing
   tools that confidently misanswer.

3. **The misdiagnosis** — I called it premise hallucination. It wasn't.
   COMP_001 was a real rule misapplied, not a fabricated rule. Closer
   reading mattered.

4. **The actual bug** — scope conflation. Define it. Show how rule_name
   and unit make scope implicit, and how implicit scope gets dropped
   silently during retrieval.

5. **The three-layer fix and its unexpected side effect** — structured
   scope field, prompt verification, deterministic safety net. Show
   how the prompt change shifted retrieval strategy upstream
   (the agent started preserving scope words in tool queries).

6. **The deployment-layer story** — container served stale code; lsof
   diagnosis. The "it works on disk but not in production" anecdote.

7. **Generalization is dimension-bounded** — what worked on one scope
   dimension didn't generalize to others. Show un_01 (California)
   silently substituting general rules. Different dimension, same
   conceptual bug.

8. **The schema-exposure tool** — exposing the registry's structure as
   a tool the agent can query. Why this is more robust than adding
   worked examples for every dimension. Reference the text-to-SQL
   pattern of exposing schema; explain why this is similar but applied
   to a rules registry.

9. **The cross-model-class judge** — knowledge leakage as a separate
   bug class. The asymmetry tradeoff (cross-vendor would be stronger;
   cross-class is the pragmatic compromise). The data-flow gap I
   missed twice and what that taught me about specifying LLM-component
   inputs. The calibration unevenness — even cross-class judging
   isn't fully stable.

10. **When to stop fixing** — the three-round loop. The pivot to
    "let RAGAS measure ground truth instead." Engineering judgment
    over perfectionism.

11. **What it doesn't solve** — fallback laundering, decorative RAG,
    rule_id picking by surface match (the rb_04 finding), judge
    calibration unevenness. Honest about what's still broken.

This is a richer arc than the version I sketched after the time-scope
fix alone. Length target unchanged: 2,500–3,500 words.

---

## Backlog additions from today

(Append to the existing backlog section above.)

### Rule_id picking by surface match

rb_04 ("repeat-speaker threshold for a single speaker") cited SPEAKER_002
("Max Speaker Events Per Year") instead of SPEAKER_003 ("Repeat Speaker
Threshold"). Both have threshold 6, so the answer's number is right.
But the rule_id picked is wrong. The agent's `lookup_rule` query was
something like "speaker per year" which scored higher on SPEAKER_002's
name than on SPEAKER_003's name.

This is a different bug class than scope conflation or knowledge leakage.
It's keyword-overlap surface matching on rule_name, the same mechanism
that made the original meal-cap bug pick MEAL rules over COMP_001. The
fix would be deeper — rules might need semantic indexing rather than
keyword matching, or the agent might need to inspect multiple
candidate rules and pick the one whose `rule_name` best matches the
question's *concept* rather than its *tokens*.

Not fixing now. Note in the article as a lower-priority bug class
that surfaces alongside the bigger ones.

### Judge calibration unevenness

Documented above in the same-day notes. Worth tracking as Phase 5
candidate work — possibly a different judge prompt that's more
permissive on prose phrasing while remaining strict on numeric/named-
entity claims. Or move to cross-vendor judging (Claude or Gemini)
to break the within-vendor calibration drift.

---

## 2026-04-26 — Registry incompleteness laundered as policy absence

Discovered while skimming PDFs to author retrieval-category dataset
entries. The Nova Pharma synthetic policy PDF contains rules that the
rules registry (compliance/rules.json) does NOT have. The most
significant: Section 2.2 of the synthetic policy says "No HCP may
receive meals from Nova Pharma field personnel exceeding $500 in any
rolling 12-month period." There is, in fact, an annual meal cap. It's
$500.

This contradicts the agent's behavior on fp_01 (the original failing
case). The fixed agent confidently answers: "The policy does not
define an annual meal-specific cap for healthcare professionals
(HCPs)." That answer is wrong. The cap exists. It's in the policy
corpus. The rule extraction pipeline that produced rules.json missed
it.

### Other rules in the synthetic PDF that the registry doesn't capture

Found while reading Section by Section:

- FMV rate card by specialty/tier (Section 3.1) — registry has SPEAKER_001
  with a flat $3,500 ceiling; actual policy has tiered rates by
  specialty (national/regional/local) and HCP type (PCP/specialist/
  sub-specialist/NP-PA), ranging $300 to $3,500.
- Annual consulting cap of $50,000 (Section 4.2) — separate from the
  $75,000 speaker cap. Registry has neither.
- Office visit frequency limits by HCP type (Section 5.1) — 12/8/6
  per year for PCP/specialist/sub-specialist. Registry has FREQ_001-003
  for meals and interactions but not detailing visits.
- Combined engagement cap (Section 5.2) — 20 paid engagements + 30
  total interactions per HCP per year. Registry doesn't have either.
- CRM 30-day logging requirement (Section 6). Not in registry.
- Open Payments de minimis thresholds: $10/$100 (Section 6). Not in
  registry.
- 7-year document retention (Section 8.2). Not in registry.

### Why this is the most concerning bug class so far

Scope conflation produces wrong answers from real rules. The judge can
sometimes catch it. The marker safety net catches obvious dimension
gaps.

Knowledge leakage produces real-knowledge answers presented as if they
came from the corpus. The judge catches it when the agent admits
retrieval failure.

Registry incompleteness produces "no such rule exists" answers for
rules that DO exist. The judge cannot catch this because the agent
isn't fabricating — it's correctly reporting what it has access to.
The safety nets don't fire because there's no scope mismatch in the
question. The schema-exposure tool doesn't help because the question
isn't about a dimension. The system is confidently wrong in a way
that has no detection layer.

This is the bug class that should scare a regulator-facing user the
most. "The policy says you can do X, but the system tells you the
policy is silent on X" is the failure mode where someone violates a
rule because they were told it didn't exist.

### What this means for the dataset

fp_01's "expected answer" needs reconsidering. The original framing
was: "the policy does not define an annual meal-specific cap" — and I
treated that as the correct refusal. It's not. The correct answer is:
"Yes, $500 per rolling 12-month period per Section 2.2 of the Nova
Pharma Internal Policy."

This makes fp_01 a much more interesting dataset entry than I thought.
It's no longer a "false-premise" entry — the premise is actually
correct. It's a **registry-coverage gap test case**: the agent should
get this right via retrieval (PDF text), but currently gets it wrong
because retrieval is decorative (relevance 0.02-0.04) and the registry
doesn't have the rule.

Also: this is the strongest possible argument for retrieval improvement
in 1.2. Until now, "retrieval is decorative" was a curiosity. Now it's
a correctness problem with documented stakes — questions about real
policy facts get wrong answers because retrieval can't surface them
and the registry doesn't have them.

### Calibration note on my earlier model of this system

I had been treating the rules registry as ground truth and the PDFs as
"additional context the retrieval might surface." That's backwards.
The PDFs are ground truth (they're what an actual compliance reviewer
would read). The registry is a partial extraction. The system's
correctness is bounded by the union of (registry coverage) ∪ (retrieval
quality) — and right now both are leaky in non-overlapping ways.

This shifts the article's article's argument too. The lessons-learned
piece can no longer be "I fixed scope conflation and knowledge leakage."
It has to be "I fixed two bug classes and discovered a third I couldn't
fix without re-running the rule extraction pipeline." That's a more
honest end state and a more useful one for readers building similar
systems.

### What I'm not doing about it now

Not re-running the rule extraction. That's Phase 5 work. The retrieval
fix planned for 1.2 is also not the right tool — even if retrieval
relevance went from 0.03 to 0.50, the registry would still be missing
rules and the system would still treat retrieval as decorative for
rule-backed questions.

The right fix is twofold:
- Re-extract the registry with stricter coverage (compare extracted
  rules against PDF text section by section to find gaps)
- Change the agent's reasoning to treat retrieval and registry as
  complementary rather than fallback ("if no rule found, say so" was
  the original prompt — should be "if no rule found, search PDF
  thoroughly before claiming the policy is silent")

Both are out of scope for this session. Capture in lessons log,
document in the dataset's expected_answer fields, surface in the
article.

### One more thing — naming the bug class

Calling it "registry incompleteness laundered as policy absence." The
"laundered" framing matters: the system isn't claiming the policy
doesn't address this topic. It's claiming the policy doesn't define an
annual meal cap. The user trusts that claim because it's specific. If
the system said "I don't have information on annual meal limits in my
knowledge base," the user would seek another source. By laundering
registry-coverage into policy-absence, the system substitutes its own
limitations for the corpus's content, with no signal that this
substitution happened.

This is the most generalizable finding from the whole session. Any RAG
system that treats a structured extraction as authoritative will do
this. The only defenses are (a) coverage testing of the structured
layer against the source documents, and (b) prompting the model to
distinguish "I didn't find this" from "this doesn't exist." Neither is
common practice.

---

## 2026-04-26 (later) — Three findings from running the baseline

Built the harness, ran the baseline, ran it again with fixes, ran it a
third time with paired prompt changes. Three findings worth keeping
for the article. None of them was the finding I expected to write up
when I started.

### Finding: dual-source measurement gap

Background. The agent has two information sources: rules.json (via
lookup_rule) and Qdrant (via search_policy_docs). Most rule-backed
queries answer entirely from rules.json — the registry has the
threshold, the agent cites the rule_id, no retrieval needed.

RAGAS Faithfulness measures whether the agent's claims are supported
by the retrieved_contexts pool. The harness, by default, populates
retrieved_contexts with Qdrant chunks only — because that's what RAGAS
was designed for, single-source RAG. So when the agent answered "the
lunch limit is $50, per rule MEAL_002," RAGAS checked that claim
against the Qdrant chunks (which were about virtual meetings, FMV
services, and HCP fraud risk because retrieval is broken), failed to
find $50 anywhere in them, and scored Faithfulness=0.000.

The first baseline showed rule_backed Faithfulness=0.000 across all 4
entries. My initial reaction was "the agent is fabricating
thresholds." Wrong reaction. The agent was correctly grounding in
rules.json; RAGAS just couldn't see that grounding source.

The fix was to feed rules.json results AND nova_vs_phrma comparisons
into retrieved_contexts alongside Qdrant chunks, with prefix markers
([rules_registry], [qdrant:], [nova_vs_phrma]) so RAGAS could
distinguish them in claim-verification. After the fix, rule_backed
Faithfulness went 0.000 → 0.668. The agent didn't change. The
measurement layer changed.

The general lesson: when you wire RAGAS into a multi-source agent,
RAGAS evaluates whatever you feed it. If you feed it one source out of
two, you get a structurally biased measurement and don't know it
unless you read the per-entry trace. I read the trace because rb_01's
Faithfulness=0.000 was so sharply at odds with the smoke test where
rb_01 had been clean — the contradiction was the only reason I
investigated.

For the article: this is the strongest argument I have for "look at
per-entry results, not aggregate metrics." The aggregate said
"Faithfulness 0.21 across 16 entries." That number was right. It also
told me almost nothing useful, because four of those entries were
artifacts of the wiring. Per-entry inspection surfaced the gap;
aggregates would have hidden it indefinitely.

### Finding: faithfulness rewards faithful wrong answers

The registry_gap category is questions whose answers exist in PDF text
but NOT in rules.json. rg_01 ("What is Nova Pharma's annual meal
cap?") is the canonical case — Section 2.2 of the policy says $500
per rolling 12-month period, but rules.json doesn't have that rule.

After the dual-source fix, rg_01 Faithfulness scored 0.875 to 0.938
across runs.

The agent's answer is "the policy does not define an annual meal-
specific cap." That answer is grounded in rules.json (which truly
doesn't have the rule). Faithfulness measures grounding. So
Faithfulness scores high.

The answer is also factually wrong. The policy DOES define an annual
meal cap — it's in the PDF, in Section 2.2, $500/rolling-12-month.
The agent failed to retrieve it because retrieval is broken.

So: high Faithfulness on a wrong answer.

This isn't a RAGAS bug. Faithfulness is doing exactly what it claims
to do — measuring whether claims are supported by available context.
The conflation is mine: I was reading "high Faithfulness" as "correct
answer." Those are different things. Faithfulness is "this answer
matches the agent's information sources." Correctness is "this answer
matches reality." Those align when the information sources are
complete. They diverge when the sources have gaps.

Registry incompleteness is exactly the gap that creates this
divergence. A registry-laundered answer is faithfully grounded in the
registry; the registry is wrong; Faithfulness can't tell the
difference.

This is the rg_01 paradox: the metric we trust to catch hallucination
will reward the most dangerous failure mode in this system. Not
because the metric is bad but because it answers a question I wasn't
asking. I was asking "is this answer right?" The metric was answering
"is this answer consistent with what the agent thinks it knows?"

For the article: this is THE example to lead with when discussing
metric selection. Faithfulness is the most-cited RAG metric and it's
the wrong metric for the failure mode that matters most in regulated
industries. The right metric for registry_gap would be something like
"answer correctness against external ground truth" — which RAGAS has
(AnswerCorrectness, requires reference text), but it's expensive to
author and not commonly used. The cheap metric is wrong; the right
metric is expensive. That tradeoff isn't talked about enough.

### Finding: prompt instructions have a ceiling

The third Cursor run added two paired prompt instructions: LIST
SYNTHESIS (for multi-chunk enumeration cases like the OIG fraud
indicators question) and ABSENCE HANDLING (for unanswerable cases
like the telehealth question).

ABSENCE HANDLING worked perfectly. un_01 went from Faithfulness 0.500
(over-extrapolation, agent volunteered "general rules apply to
telehealth") to Faithfulness 1.000 (clean refusal, agent stopped at
"the policy does not address this"). Latency dropped from 11s to 6s
because the agent didn't even retrieve chunks once it recognized the
topic absence. Behavior changed exactly as instructed.

LIST SYNTHESIS did not work. ret_01 still hit max_iterations and
returned "Agent stopped due to max iterations." After 20 iterations.
The instruction told the agent to enumerate from retrieved chunks
without making additional searches with the same query. The agent
complied with the letter of the instruction — it didn't repeat
queries — but slightly varied each query (e.g., "OIG speaker fraud
indicators" → "OIG anti-kickback speaker risks" → "OIG fraud signals
speaker programs") and never reached the synthesis step.

The Cursor diagnosis is correct: this is a tool-loop control problem,
not a prompt-instruction problem. The agent's failure isn't in
answer-formation (where prompt instructions bind), it's in the
iteration loop (where they don't). The loop keeps running because
each query is technically different and the "varying queries" pattern
isn't covered by the instruction.

The general principle: prompt-layer fixes only bind on prompt-layer
failures. When the failure is at a different layer — tool-loop
control, retrieval quality, embedding model selection, the
AgentExecutor's iteration budget — prompt-layer instructions are not
the right intervention. They might do nothing (LIST SYNTHESIS) or
they might do something close to what you wanted but not exactly
(unclear cases I haven't seen yet but expect to).

This is the sharpest version of "use the right tool for the right
layer" I've encountered building this system. Reaching for prompt
instructions as the universal fix is tempting because they're cheap
and reversible. But cheap-and-reversible is a property of the
intervention, not a property of the problem. The right fix for
ret_01 is a hard iteration budget at the AgentExecutor level — fewer
keystrokes than a prompt instruction, harder to test in isolation,
but the only intervention that actually addresses the layer where
the failure lives.

For the article: this is the lesson I most want to land cleanly
because it's the one most directly transferable to other RAG
projects. People building these systems reach for prompts because
prompts feel safe. They aren't safe; they're just visible. A failure
at the wrong layer with a prompt-shaped fix is a regression waiting
to happen the next time someone touches the prompt for another
reason. Hard structural constraints (iteration budgets, validators,
schema enforcement) bind reliably; prompt instructions bind only when
the model decides to follow them.

### Closing note on 1.1

Three baselines in 24 hours. Each one taught me something the
previous one couldn't have. The baseline isn't a measurement, it's a
diagnostic — it shows you what your system actually does versus what
you think it does, and the gap between those two is where every
useful finding lives.

If I had run a single baseline and called it "the measurement," I
would have published findings 1 and 2 (scope conflation, knowledge
leakage) and stopped. Findings 3 (registry incompleteness), 4 (dual-
source measurement gap), 5 (faithfulness paradox), and 6 (prompt
ceiling) only surfaced because each baseline created the conditions
for the next one to be informative.

The article should reflect this. Not as "I built an eval and found
three things" but as "I built an eval, the eval surfaced
measurement-layer issues, fixing those surfaced architectural issues,
fixing some of those surfaced layer-mismatch issues. The eval wasn't
the answer; the eval was the question that kept getting better."

---

## 2026-04-26 (evening) — UI testing surfaced what the harness couldn't

After the third Cursor run reported 1.1's measurements stable, ran a
4-question UI test against the Streamlit dashboard. The questions were
chosen to exercise different rendering paths: rb_01 (lunch limit),
fp_01 (California), ret_01 (OIG fraud indicators), rg_01 (annual meal
cap). Three findings the harness either didn't capture or captured
with the wrong shape.

### Finding: groundedness judge has no concept of justified absence

When the agent correctly answers "the policy does not segment meal
limits by state, including California," the groundedness judge flags
this as ungrounded. The judge's reasoning, surfaced verbatim in the UI
data limitations panel: "The retrieved content does not provide
information about state-specific segmentation or enforcement details
of meal limits, making these claims ungrounded."

Of course the retrieved content doesn't provide that information.
The policy doesn't segment by state, so there's no chunk asserting "we
don't segment by state." The absence is real. The judge requires
positive grounding for the negative claim, which is structurally
impossible — you can't ground "X doesn't exist" in retrieved text
unless retrieved text explicitly says "X doesn't exist," which policy
documents almost never do.

This means: every correct refusal of a false-premise question gets
flagged as ungrounded. The metric points the wrong direction on the
behavior I most want from the agent.

Combined with the rg_01 paradox (faithfulness rewards faithfully wrong
answers), the picture sharpens:

- Correct answer with retrieval support: faithful ✓ correct ✓
- Wrong answer grounded in incomplete registry: faithful ✓ correct ✗
- Correct refusal of false-premise question: faithful ✗ correct ✓

Two of the three failure modes that matter most have faithfulness
pointing the wrong direction. The metric isn't broken; it's measuring
something other than what we want.

For the article: this is the second piece of the "metric isn't truth"
lesson. The first piece (rg_01) shows faithfulness rewarding wrong.
This piece shows faithfulness penalizing right. Together they make
the case that faithfulness is the wrong primary metric for this
system. The right primary metric for compliance Q&A is something like
correctness against external ground truth — RAGAS calls it
AnswerCorrectness, requires reference text per question, expensive to
author. The cheap metric measures the wrong thing; the right metric
costs money to set up. That tradeoff doesn't get talked about enough.

### Finding: prompt fixes can shift bug class rather than eliminate it

The third Cursor baseline reported ret_01 still hitting max_iterations
after the LIST SYNTHESIS instruction was added. The UI test showed
ret_01 actually completing with a synthesized answer — four indicators
listed, honest disclaimer at the top ("the specific characteristics
were not detailed in the retrieved documents").

Surface reading: the prompt fix worked. Look closer.

The four indicators (Excessive Compensation, Lack of Educational
Value, Frequent Repeat Engagements, Inadequate Documentation) don't
appear in any of the retrieved chunks. The citations have relevance
0.01-0.02. None of the retrieved text contains "Excessive
Compensation" or "Frequent Repeat Engagements" as named indicators.
The OIG document does list 9 such indicators on pages 5-6, but
retrieval didn't surface those pages.

Where did the four indicators come from? Training data. The agent
fabricated from gpt-4o-mini's pretrained knowledge of OIG fraud
patterns and added a hedge that makes the output look responsible.

The original failure mode for ret_01 was: keep iterating until
max_iterations, never produce output. The fix told the agent to
synthesize from what retrieval returned. The agent had three options
when retrieval was empty:

(a) Keep iterating (the original bug)
(b) Refuse honestly: "retrieved chunks don't address this question"
(c) Fabricate from training data with a disclaimer

The fix targeted (a) and got (c). What I wanted was (b).

The instruction wasn't wrong, it was incomplete. "Synthesize from
what you have" needs a precondition: "synthesize when retrieved
chunks contain enumerable items related to the question; refuse when
they don't." Layered conditions are harder to write than single-
purpose instructions. Single-purpose instructions can deflect the bug
rather than fix it.

The disclaimer pattern ("specific characteristics were not detailed
in the retrieved documents") is the most insidious part. It makes
the fabrication look careful — the agent is "being honest" about
retrieval limitations even while making up the answer. A user who
sees the hedge probably trusts the answer more, not less, because the
agent appears to have integrity. The hedge is camouflage.

For the article: this is the lesson about prompt-layer fixes that I
most want to land. The naive intuition is "prompts are safe because
they're reversible." This shows the failure mode of that intuition.
A prompt fix can close the bug it's targeting AND open a different,
worse bug at a different layer. The new bug is harder to detect
because the agent's output looks more responsible than before. The
fix improves the surface metrics (no more max_iterations stop, the
judge accepts the disclaimer as appropriate caution) while the actual
behavior gets less trustworthy.

The right fix layered the instruction with a refusal precondition.
The right diagnosis required reading the chunks against the answer,
not trusting the disclaimer. Both are work the metrics don't do for
you.

### Finding: Test 4 reproduced the registry-incompleteness pattern exactly

rg_01 in the UI: "What is Nova Pharma's annual meal cap for HCPs?"
Answer: "The policy does not define an annual meal-specific cap for
healthcare professionals (HCPs). Instead, it specifies per-meal
limits..." Confidence: low. Citations: 0.03 noise.

The rule_thresholds panel shows all answer-relevant thresholds sourced
to "Nova Pharma" — the meal limits, the $75K annual compensation cap.
Only the "Near-Cap Warning Threshold" carries a "(source: fallback)"
tag, and that's a system parameter, not an answer fact. So unlike
ret_01 where fabrication-with-disclaimer was the failure mode, Test 4
shows no fabrication at all. The agent grounds entirely in the rules
registry, reports honestly that the registry doesn't contain an
annual meal cap rule, and the answer is wrong because the registry
doesn't have a rule that the policy clearly states (Section 2.2:
$500/rolling-12-month).

This is the registry-incompleteness bug in its purest form. No
hedge, no fabrication, no over-extrapolation. Just an honest report
of what the agent's information sources contain, when those sources
are incomplete.

The visceral case is stronger here than in any harness output. A
regulator-facing user reads "the policy does not define an annual
meal-specific cap" and acts on it. The user has no signal that the
agent's "policy" is the registry, not the source documents. The
phrasing erases the distinction. The user takes it as "the policy
text says no" when what the system actually means is "my structured
extraction of the policy says no, and I didn't successfully retrieve
the source text that would have corrected me."

This is the screenshot for the article. The wrong answer, the
confident phrasing, the low confidence indicator that the user might
not notice or might not know how to interpret, the noise citations,
the data limitations panel buried below the fold. Every layer of the
UI says "trust this" while the system layer underneath says "this is
incomplete." The UI design and the system reality are pointing
opposite directions.

### Closing observation across the three findings

The harness measured what the harness was instrumented to measure.
The UI surfaced different things. Specifically:

The judge-on-absence problem (finding 4) was visible in the
groundedness flags but easy to dismiss as judge calibration noise —
until you read the judge's reasoning verbatim in the UI panel and
see that it's structurally incapable of grounding negative claims.

The fabrication-with-disclaimer problem (finding 5) was hidden by
the harness because RAGAS Faithfulness on a confidently-disclaimed
fabrication scores about the same as on a confidently-grounded
answer. The metric can't read the disclaimer in context. A human
reading the UI can.

The registry-incompleteness visceral case (finding 6) was already
quantified by the harness (registry_gap Faithfulness = 0.875–0.938)
but the metric framed it as "the agent is highly faithful." The UI
framed it as "the agent is confidently telling the user something
that isn't true." Same data, opposite valence.

The pattern across all three: the harness surfaces what's
quantifiable; the UI surfaces what's interpretable. Both are
necessary. Neither is sufficient. The article should make this
explicit — eval-driven development without UI inspection misses the
failures that look right in the metrics. UI inspection without an
eval misses the failures that don't show up in any single
interaction. The combination is what produces the diagnostic clarity
that lets you fix the right thing at the right layer.

---

## 2026-04-27 — 1.2a closure: embedding fix landed, residuals revealed

Yesterday's hypothesis (ada-002 indexing vs text-embedding-3-small
querying) confirmed by direct file inspection. `pipelines/embed_policy_docs.py`
line 44 read `text-embedding-ada-002`. `agents/tools/policy_tools.py`
line 46 read `text-embedding-3-small`. Same dimension (1536) on both
sides — Qdrant accepted queries silently — but the vector spaces
were unrelated, so cosine similarity scores have been geometric noise
since the index was built. This is why retrieval relevance has
clustered at 0.02-0.04 across every query for weeks.

The fix was a single-line change on line 44, plus delete and
re-ingest the policy_docs collection. No schema changes needed
because dimensions matched.

### What the rebuild did

Aggregate Faithfulness 0.612 → 0.770 (+0.158). Retrieval Context
Recall 0.000 → 0.267. Registry-gap Context Precision 0.000 → 1.000.
Two CI gates flipped to passing for the first time (Faithfulness 0.807
above 0.7; answer_relevancy 0.710 above 0.7). Latency stable.

The before/after picture is exactly the article wants: same agent,
same prompts, same dataset, same dimensions, single line code change,
retrieval went from "decorative" to functional. No confound. The
embedding mismatch was, in retrospect, the largest single bug in the
system — invisible to all the safety nets, undetectable from any
single answer, only revealed by aggregate retrieval metrics across a
diverse query set.

### What rb_03 told us

The annual compensation cap question was the rule-backed outlier in
yesterday's baseline (Faithfulness 0.17). Today it landed at 0.625
with Context Recall 1.0. The structural weak point resolved without a
prompt change or a registry change — it just needed retrieval to
work. The rb_03 "weak point" wasn't really weak; it was sitting in
the gap where the agent's claim depended on a registry chunk AND a
Qdrant chunk, and the Qdrant side was returning noise.

This is a good lesson for the article. When metrics show
category-internal variance (rb_01-04 mostly fine, rb_03 anomalous),
the temptation is to characterize the anomaly as its own bug. Often
it isn't — it's a sentinel for a different bug that happens to
intersect that one entry's structure most visibly. Fix the upstream
bug; the sentinel resolves on its own.

### What ret_01 told us

The OIG fraud indicators question (ret_01) was the LIST SYNTHESIS
test case. Yesterday it hit max_iterations and produced
"Agent stopped due to max iterations." Today it produced a real
synthesized answer with Context Precision 1.0 and Context Recall 1.0.

The hypothesis from yesterday's lessons log entry — "prompt
instructions have a ceiling, and that ceiling is the layer below" —
turned out to be exactly right. LIST SYNTHESIS didn't bind yesterday
because retrieval gave it nothing to bind on. With retrieval working,
the same instruction binds correctly. No prompt change needed.

The fabrication-with-disclaimer pattern from yesterday's UI test was
also a retrieval artifact. With retrieval surfacing real OIG content,
the agent has no need to fabricate from training data. The hedge
disappeared because the agent didn't need to hedge.

This sharpens the prompt-instruction-ceiling finding. The original
framing was "prompt fixes have a ceiling, structural fixes are
needed." A more precise framing: prompt instructions encode behavioral
preferences that bind only when the supporting infrastructure is
present. LIST SYNTHESIS asks the agent to enumerate from retrieved
chunks; if retrieved chunks are noise, the instruction has nothing to
work with. Fix retrieval, the instruction starts working. The
"ceiling" isn't a ceiling at all — it's a precondition.

### What rg_01 told us — and what it didn't

Yesterday's framing: rg_01 fails because the rules registry doesn't
have a $500/12mo annual meal cap rule. Fix would be re-extracting the
registry.

Today's per-entry inspection sharpens the diagnosis. Retrieval now
surfaces Section 3.2 of the Nova Pharma policy (Annual Speaker Fee
Cap, $75,000) and rules-registry COMP_001 ($75,000 annual
compensation cap). Both are annual caps. Neither is THE annual cap
the question is asking about, which is Section 2.2 (annual MEAL cap,
$500). The answer remains wrong: "the policy does not define an
annual meal-specific cap." Faithfulness 0.81 because that wrong
claim is faithfully grounded in the registry (which doesn't have it)
and in the retrieved chunks (which don't include Section 2.2).

So the diagnosis evolves. Section 2.2 exists in the policy PDF. With
the embedding fix, retrieval CAN find content from that PDF. But the
chunk containing $500/12mo isn't ranking high enough on the query
"annual meal cap" to make the top retrieved set. Possibilities:

(a) The chunk containing $500/12mo doesn't have strong "annual" or
"cap" lexical or semantic markers — the rule sits in a chunk titled
"Meal and Hospitality Limits" with surrounding text about per-meal
limits, and "annual" might appear only once.

(b) Chunk size (512 words, 64 overlap) splits Section 2.2 across
multiple chunks, weakening its semantic concentration.

(c) The query "What is Nova Pharma's annual meal cap for HCPs?"
matches more strongly against "Annual Speaker Fee Cap" and "Annual
HCP Compensation Cap" than against the actual Section 2.2 text.

Each is testable by pulling the chunks from Qdrant and inspecting.
Each suggests a different fix: chunking strategy adjustment,
metadata enrichment, or query rewriting. Probably some combination.

The right framing for the article: rg_01 is a chunking problem, not
a registry-incompleteness problem. The registry IS incomplete (it
doesn't have $500/12mo as a rule), but that's not the proximate
cause of the wrong answer in the current architecture. The
proximate cause is that retrieval-by-semantic-search doesn't surface
Section 2.2 for the natural-language query a user would write. Fix
chunking, you fix rg_01. Fix the registry, you also fix rg_01 — but
chunking is the smaller intervention and lives at the layer the
embedding fix has already opened up.

This is a layer-down diagnosis. The original embedding bug hid the
chunking bug. Yesterday's "registry incompleteness" framing was the
best diagnosis available given what could be observed. With
retrieval now functional, a sharper diagnosis is possible. This will
be addressed in 1.2c, bundled with the source viewer work because
both touch the embedding pipeline and require re-ingest.

### What ret_05 told us

The seven-elements-of-compliance-program question (ret_05) was the
second multi-chunk list synthesis test. ret_01 succeeded; ret_05
hit max_iterations.

Both queries should benefit equally from working retrieval. ret_01
got Context Precision 1.0, Context Recall 1.0 — retrieval surfaced
the right OIG document and the agent synthesized. ret_05 got Context
Precision 0.5, Context Recall 0.0 — retrieval surfaced some related
content but not the actual seven-element list, and the agent kept
searching.

This is a real finding about LIST SYNTHESIS's behavior under
partial-retrieval conditions. When retrieval gives the agent enough
to synthesize from, the instruction works. When retrieval gives the
agent partial signal, the agent doesn't know whether to synthesize
or to keep looking — and the instruction (correctly) tells it not to
make duplicate queries but doesn't tell it when to stop iterating
overall.

The right fix is at the AgentExecutor layer: a hard budget on
search_policy_docs calls, after which the agent must synthesize from
what it has or refuse. Cursor proposed exactly this fix yesterday and
I deferred it; the per-entry result here makes the case for actually
implementing it.

This is a one-line patch to the AgentExecutor configuration. It will
land in 1.2b alongside citation UI work, where the touch surface is
all "small agent and UI polish."

### What the metric layer told us

Two retrieval entries (ret_01, fp_01) returned Faithfulness=None
despite producing real answers with retrieved contexts. This is a
RAGAS internals issue — claim extraction returning no claims, or
some other null path inside the metric. Not blocking, but worth
investigating because aggregate Faithfulness was computed across the
14 entries where the metric returned a number, not all 16. The
reported +0.158 improvement may be slightly inflated by the
exclusion of two entries whose Faithfulness would have been computed
to some specific value.

Investigating this properly requires reading RAGAS's claim-extraction
internals. Probably not fixable on our side (we'd need to either
change RAGAS or change our answer format to make claim extraction
work better). Will investigate in 1.2e and decide whether to fix,
filter our reporting, or accept and document.

### Closing observation across the residuals

Each residual surfaced from inspection of per-entry data, not from
the aggregate. The aggregate said "1.2a worked, +0.158 Faithfulness,
two CI gates passed." Per-entry inspection said "1.2a worked AND
revealed three new bugs at three different layers — chunking
(ingestion layer), tool-loop control (executor layer), and claim
extraction (metric layer)."

Both are true simultaneously. The aggregate is the right thing to
report to a stakeholder; the per-entry inspection is the right
thing to inform the next decision. A baseline that produces
aggregates without per-entry data is a baseline that closes
prematurely. The harness must keep both.

The article's structure is now:
- Smoke test → three findings (scope, leakage, registry gap)
- Eval rebuild → three findings (dual-source measurement, faithfulness
  paradox, prompt instruction ceiling — later refined to "precondition")
- UI testing → three findings (judge-on-absence, fabrication-with-
  disclaimer, registry-gap visceral case)
- Embedding fix → three findings (chunking-not-registry, retrieval-
  enables-prompt-fixes, metric null path)

Twelve findings across four investigations. Each investigation made
the next one's findings sharper. The article is genuinely a
"diagnostic methodology" piece more than a "RAG findings" piece —
the lesson is that no single layer of investigation surfaces all the
bugs, and the bugs that hide hardest are the ones at the layer
nobody is looking at yet.

---

## 2026-04-28 — 1.2c full arc closure: chunking diagnosis, excerpt fix, three rounds of prompt iteration

1.2c set out to fix rg_01 (the canonical registry-gap case) and the
context_precision CI gate. It did both, plus more, plus surfaced a
generalizable finding about the ceiling of prompt-layer fixes that
the article should hold up as central.

The arc spanned five days of work and four distinct interventions.
Capturing each here in order, then the closing observation.

### Day 1: chunking investigation — three hypotheses wrong, fourth one right

Spent ~90 minutes investigating why rg_01 retrieved Section 3.2 of
the Nova Pharma policy (Annual Speaker Fee Cap, $75,000) but missed
Section 2.2 (Annual Meal Cap, $500/rolling-12-month). Three
hypotheses entering: (a) chunk lacks "annual"/"cap" markers,
(b) Section 2.2 split across chunks, (c) competing annual-cap chunks
rank higher.

All three wrong. Hypothesis D, not on the original list, was the
actual cause: `policy_tools.py` line 137 truncates excerpts to 150
characters. Section 2.2 sits at character 1486 of chunk
DOC_002_chunk_0000. The chunk retrieves at rank 1 with score 0.7098
— couldn't ask for better placement — but the agent only saw the
synthetic-document disclaimer header in the 150-char excerpt. The
$500/rolling-12-month text was always in the index, always in the
top retrieved chunk, always invisible to the agent.

Lesson worth keeping: when retrieval metrics look right but answers
look wrong, check what the agent actually sees, not what retrieval
finds. The pipeline between "chunk identified" and "chunk content
in agent context" is its own surface where bugs hide.

This is the second instance of "the bug that hid hardest was at the
layer nobody was looking at yet." The first was the embedding model
mismatch (1.2a). The pattern is consistent: when the high-leverage
diagnosis is "we've been measuring something other than what we
thought," the previous fixes that didn't fully work suddenly make
sense.

### Day 2: single-line fix, large impact

Changed `excerpt = raw_text[:150]` to `excerpt = raw_text`. Updated
the docstring. No re-indexing. No prompt change.

Aggregate Context Recall: 0.333 → 0.677 (+0.344). Aggregate Context
Precision: 0.437 → 0.624 (+0.187). Aggregate Faithfulness: 0.740 →
0.761 (+0.021). One single-line change, three meaningful aggregate
improvements.

rg_01 specifically resolved cleanly: the agent now correctly cites
"$500 per rolling 12-month period" with chunk_id reference.
Faithfulness 0.923, Grounded True, Context Precision 1.000.

ret_05 also resolved without prompt change. Yesterday's lessons-log
entry on ret_05 hypothesized that prompt instructions have a ceiling
and the ceiling was retrieval quality. With excerpts now showing
real content, the LIST SYNTHESIS instruction bound exactly as
intended. Same prompt, working retrieval, different behavior.

This refined the "prompt instruction ceiling" finding from yesterday.
The original framing was "prompt fixes have a ceiling, structural
fixes are needed below." A more precise framing: prompt instructions
encode behavioral preferences that bind only when the supporting
infrastructure is present. The "ceiling" isn't a ceiling — it's a
precondition. LIST SYNTHESIS asks the agent to enumerate from
retrieved chunks; if retrieved chunks are empty or noise, the
instruction has nothing to bind on. Fix retrieval, the instruction
starts binding.

### Day 3: per-entry inspection surfaces three more findings

Cursor's automated CI report said two gates still failing
(answer_relevancy, context_precision) and recommended re-chunking at
256 words. The aggregate metrics looked broadly improved; re-chunking
seemed defensible.

Pushing back on that recommendation by demanding per-entry inspection
turned out to be the right call. The CI gate gaps were:

- answer_relevancy 0.675: pulled down by rb_02 contradicting itself
  in two consecutive sentences. One entry, prompt-layer issue.
  Re-chunking wouldn't fix it.
- context_precision 0.454: ranking issue, not chunk-size issue.
  Re-chunking might worsen it (more chunks, more competition for
  top positions).

Plus the inspection surfaced a finding that wasn't in the CI report
at all: rg_01 fully resolved AND ret_05 fabrication ended AND un_01
still over-narrating after the dual-source fix. None visible from
aggregates.

Lesson worth keeping: aggregates report the question, per-entry
data reports the answer. Cursor's CI gate framing pushed toward
the most visible intervention (re-chunking, structural). Per-entry
inspection pushed toward the actual interventions (two prompt
fixes, one architectural problem). The structural intervention
would have spent 60-90 minutes for no benefit and introduced
regression risk.

This is article-worthy meta-content: AI coding assistants tend to
recommend more invasive interventions than the data supports,
especially when CI gates are failing. The discipline of demanding
per-entry inspection before structural changes is worth naming.

### Path B: paired prompt fixes, mixed outcome

Two fixes addressing Day 3 findings:
- TOOL OUTPUT SUPPRESSION: extend ABSENCE HANDLING to suppress
  uncapped-tool output for TOPIC ABSENT cases (un_01)
- PHRMA COMPARISON AUTHORITY: when nova_vs_phrma has phrma_equivalent
  for a rule, defer to it; don't override with chunk-derived
  inferences (rb_02)

rb_02 fix worked exactly as intended. Contradiction gone.
Faithfulness 0.909 → 1.000. The instruction did its job for the
case it was scoped to handle.

un_01 fix didn't bind. Cursor's diagnosis was sharp: the agent
classifies tangentially-related chunks as "partial retrieval" rather
than TOPIC ABSENT. The instruction was conditional on a
classification the agent doesn't make. Same pattern as LIST
SYNTHESIS not binding without retrieval, just at a different layer.
Prompt-layer fixes only bind when the agent's internal state matches
the instruction's preconditions.

rb_03 regressed. The PHRMA COMPARISON AUTHORITY fix worked for rb_02
(where phrma_equivalent=$4,000 existed) but misfired for rb_03
(where COMP_001 has no phrma_equivalent). The agent read clause (b)
of the new instruction as license to say "no phrma_equivalent →
PhRMA doesn't specify a cap" rather than "no phrma_equivalent →
make no comparison claim at all." Faithfulness 0.625 → 0.250 because
RAGAS found the new negative PhRMA claim unverifiable against
retrieved context.

This is "defensible-but-wrong reading" — the agent didn't violate
the instruction, it took an inference the instruction didn't
explicitly forbid. Different bug class from "prompt instruction not
binding." Worth naming.

### Clause (d): partial fix, demonstrates prompt-layer ceiling

Added clause (d) explicitly prohibiting PhRMA comparison claims when
phrma_equivalent is absent: "make no PhRMA threshold comparison
claim about that rule. State Nova Pharma's threshold without
asserting or inferring whether the PhRMA Code has a comparable
provision. Do not say 'PhRMA also does not specify', 'consistent
with PhRMA standards', or similar inferences."

Cursor's harness baseline: rb_03 Faithfulness 0.250 → 0.636. Agent
answer used softer phrasing: "no equivalent provision in the PhRMA
Code that specifies a similar cap." RAGAS accepted it. Grounded
True. Closure declared.

Production sampling told a different story. Same question, fresh
container, different sampling outcome: "This policy is consistent
with the PhRMA Code, which also does not specify a different cap
for total HCP compensation." That's almost the verbatim Path B
regression phrasing with one word changed ("different"). The agent
worked around the literal prohibited phrases by adding a
distinguishing word.

Three rounds of prompt iteration on PhRMA inferences. Each round
closed a specific failure pattern; each round surfaced an adjacent
one. The pattern is consistent: prompt instructions partially close
failures, agent finds adjacent paths the instruction doesn't
explicitly close.

### The harness-vs-production sampling discrepancy

The Cursor harness sampled the softer ("no equivalent provision")
phrasing. UI testing with the same fresh container sampled the
harder ("PhRMA Code, which also does not specify") phrasing. Both
versions exist in the agent's possible output space. Stochastic
variance picked one for the harness, picked another for production.

This is its own finding. The 16-entry single-run baseline doesn't
see the full distribution of agent outputs. We measured one sample
per entry. Multi-sample baselines would have surfaced the
distribution and made the partial-fix nature of clause (d) visible
from metrics alone.

For the article: methodology lesson. Single-run evaluation is
necessary but not sufficient. To characterize stochastic LLM
behavior, multi-sample-per-entry runs are needed — or property-based
testing that asserts behaviors should hold across samples, not just
on one specific output.

This isn't a bug in our work. It's a property of LLM evaluation that
shows up most clearly when you've reduced enough other failure modes
that the stochastic variance becomes the dominant source of
disagreement between measurements.

### Closing observation across the 1.2c arc

Five interventions, four distinct findings:

1. **Excerpt truncation** — single-line content-pipeline bug that
   hid the embedding fix's true impact. Day 2 fix had the largest
   single-step effect of any 1.2 work.

2. **Prompt-instruction precondition principle** — instructions bind
   when their preconditions are met. LIST SYNTHESIS bound after
   excerpt fix because retrieved content existed for it to enumerate.
   ABSENCE HANDLING didn't bind because the agent doesn't classify
   partial-retrieval cases as TOPIC ABSENT.

3. **Defensible-but-wrong reading** — instructions can be technically
   followed in ways that produce the failure they were supposed to
   prevent. rb_03 followed clause (b) of PhRMA AUTHORITY by inferring
   PhRMA absence from data absence. The instruction didn't forbid
   that reading.

4. **Stochastic variance ceiling** — once enough failure modes are
   reduced, sampling variance dominates. Harness saw soft phrasing,
   production saw hard phrasing, both grounded, both sampled from
   the same underlying agent. Single-run measurement can't
   distinguish "fix worked" from "fix worked on the sample we got."

The article structure is:

- Smoke test (Apr 25): three behavioral findings
- Eval rebuild (Apr 26 morning): three measurement findings
- UI testing (Apr 26 evening): three interface findings
- Embedding fix (Apr 27): three layer-down findings
- Excerpt fix and prompt iteration (Apr 28): four ceiling findings

Sixteen findings across five investigations. Each investigation
made the next one's findings sharper. The eval wasn't the answer —
it was the question that kept getting better.

The right way to publish this is not "I built a RAG system, here's
what I learned." It's "I built an eval to measure what I had built,
the eval surfaced layer after layer of bugs each one of which hid
the next, and the deepest finding is that prompt-layer fixes have
a ceiling that's eventually defined by stochastic variance not by
better instructions."

### What 1.2c didn't fix (carrying forward)

Three residuals, characterized honestly:

- **rb_03 PhRMA inference (still occurs at some sampling rate):**
  clause (d) reduced the inference frequency but didn't eliminate
  it. Right fix is registry completeness (Phase 5) or output-layer
  post-processing, not more prompt iteration.

- **un_01/un_02 over-narration:** prompt-layer suppression doesn't
  bind because the agent doesn't classify these as TOPIC ABSENT.
  Right fix is post-processing safety net or different prompt
  placement. Carries to 1.2d.

- **rule_backed CI gate margins:** Faithfulness narrowly failing
  driven by rb_02 RAGAS variance. Not a content issue.
  AnswerRelevancy stuck around 0.674 driven by rb_02 closing-
  sentence verbosity. Both calibration concerns, neither blocking.

Plus the new findings from production UI testing:

- Citation threshold needs recalibration (1.2g)
- Comparison table needs question-relevance filtering (1.2g new item)
- UI doesn't distinguish registry-grounded from retrieval-grounded
  answers (1.2g new item)

Plus a methodology note for Phase 5: distribution-aware baseline
measurement (multi-sample-per-entry) would catch
prompt-circumvention patterns the single-run harness misses.

### One more thing — what to do about the sampling discrepancy

The harness reported clause (d) closed cleanly. Production sampling
showed it didn't. If I'd trusted the harness alone, I'd have closed
1.2c claiming a fix that doesn't fully bind in production.

UI testing caught it. The same UI testing pattern that surfaced
findings on Apr 26 evening (judge-on-absence, fabrication-with-
disclaimer, registry-gap visceral case) caught the harness-vs-
production gap on Apr 28 evening. The pattern across both is:
metrics report what's quantifiable, UI surfaces what's
interpretable, the combination produces diagnostic clarity neither
provides alone.

For the article: the discipline of UI testing against production
sampling, even when metrics report success, is article-worthy as
a methodology recommendation. Don't trust the eval. Trust the
combination of eval + production sampling + reading the actual
answers.

---

## 2026-04-29 — 1.2d: post-processor safety net for un_01/un_02 over-narration

1.2d set out to fix the un_01/un_02 over-narration that prompt-layer
fixes (Path B's TOOL OUTPUT SUPPRESSION) couldn't bind on. The work
spanned two days, surfaced four distinct verification-discipline
findings, and ended with the most thoroughly verified intervention
of the 1.2 arc.

### Choosing the layer: prompt vs code vs second-pass

Three approach options at the start:

1. **Python post-processor safety net.** Detect over-narration patterns
   in the agent's final answer, strip the narration, keep the refusal.
   Code-level, deterministic, doesn't depend on agent classification.

2. **Earlier prompt placement.** Move ABSENCE HANDLING to before scope
   verification. Tests whether the instruction not binding was an
   ordering issue.

3. **Second LLM pass.** Run a separate groundedness-pass that
   evaluates whether the answer over-narrates and strips if so.
   Architecturally significant, generalizable, expensive.

Picked Option 1 for three reasons:

- **Cursor's diagnosis pointed below the LLM layer.** If the agent
  doesn't reach TOPIC ABSENT classification, no prompt-layer fix
  binds. Code-level intervention is the only layer below prompt
  that doesn't require massive architectural change.
- **Generalizability.** Same mechanism could later address rb_03
  PhRMA inferences (Phase 5 work). One intervention, multiple
  bug classes.
- **Bounded risk.** Pattern matching can over-fire, but logs every
  fire with original vs truncated text — verifiable post-hoc.

Option 2 risked spending 45 minutes confirming a no-op if the
diagnosis was right. Option 3 was overengineered for a problem
bounded enough for Option 1.

### Refined diagnosis: helpfulness vs prompt instructions

Cursor's original diagnosis was "agent doesn't classify these as
TOPIC ABSENT." Reasoning-trace inspection on Apr 29 surfaced a
sharper diagnosis:

The agent DOES reach the TOPIC ABSENT classification. un_01's
answer states the refusal three separate times — opening sentence,
mid-list item, and closing summary. The agent classifies correctly,
articulates the classification, then narrates retrieved content
anyway.

The actual root cause: helpfulness training competes with
prompt-layer absence handling. Training says "if you have related
content, share it." The TOPIC ABSENT instruction asks the agent to
not share related content even when it has it. At some sampling
rate, helpfulness wins.

This is a sharper finding than "the classification didn't fire."
It's article-worthy because it's transferable: prompt instructions
that ask the agent to suppress trained behaviors will lose at some
rate regardless of how clearly written. The right fix lives below
the LLM layer where deterministic logic can override stochastic
instruction-following.

### The Apr 30 false-clean episode

First implementation pass took ~2.5 hours. Cursor delivered:

- New module `agents/post_processors/over_narration.py`
- Integration into `policy_agent.py` at lines ~763-768 and ~797-799
- Updated `_PREV_BASELINE` pointer
- Docker rebuild with --no-cache

Canonical run 20260430T050359Z showed all four CI gates passing for
the first time in the project — Faithfulness 0.816, AnsRel 0.700,
CtxPrec 0.532, latency_p95 13754ms. Cursor framed this as success.

But: Cursor's own report flagged that the post-processor did NOT
fire on any entry that run. Framed as "safety net silent when not
needed — agents happened to give cleaner answers for un_01/un_02."

This framing was wrong but understandable. Direct verification
caught it. Running the post-processor against un_01's actual answer
text from baseline 20260428T114908Z (textbook over-narration pattern,
1668 chars, refusal opening + 3-item numbered list + chunk references):

```
ORIGINAL LENGTH: 1668 chars
TRUNCATED LENGTH: 1668 chars (UNCHANGED)
TRUNCATION NOTE: None
```

The function returned the input unchanged. Given the exact pattern
it was designed to catch. The 1.2d work had shipped a non-functional
safety net. The CI gates passing on 20260430T050359Z were real —
but happened despite the post-processor, not because of it.

This is the verification-discipline lesson worth naming sharply:
**single-run baselines mislead when failure patterns are stochastic;
safety nets need direct testing with known-bad inputs, not
baseline-rerun verification.**

The clause (d) experience yesterday showed harness-vs-production
sampling gaps. The 1.2d experience showed the inverse — when the
fix's success depends on the failure pattern occurring in the
sample, baselines that don't reproduce the failure can't verify
the fix. Both point to the same underlying lesson: aggregate
measurements at the eval layer aren't sufficient verification for
safety nets that fire conditionally. Direct testing — running the
function against recorded-bad inputs, or property-based tests, or
multi-sample replays — is the right verification method for
conditional safety nets.

### The bug: Step B AND condition assumed soft transition always present

Diagnostic walkthrough revealed why the function returned unchanged:

```
Refusal pattern checks:
  'is not explicitly addressed': MATCH    <-- Step A passes
Over-narration marker checks:
  Numbered list: True
  Chunk refs: True
  Soft transition: False                  <-- ABSENT, breaks Step B
```

Step A (refusal first sentence) passed. Step B required BOTH soft
transition AND numbered list. un_01's actual answer transitions
straight from refusal opening into the numbered list with NO soft
transition phrase ("however," "based on the retrieved," "some
relevant information"). The AND condition fails on the soft-transition
check, function returns input unchanged.

Path B's pattern analysis assumed the soft transition was always
present. It isn't. This is a small design assumption, not a
structural problem.

### The fix: relax to OR with chunk-refs guard

Single-line change in `over_narration.py`:

Before:
```python
if not (has_soft and has_numbered):
    return answer, None
```

After:
```python
has_chunk_refs = bool(_CHUNK_REF_RE.search(body_original))

# Fire when soft transition + numbered list (original Path B assumption)
# OR when numbered list + chunk references (un_01 pattern: agent skips
# the soft transition but narrates retrieved chunks with citations).
if not (has_soft or (has_numbered and has_chunk_refs)):
    return answer, None
```

The chunk_refs guard prevents over-firing on entries with legitimate
numbered content that doesn't reference retrieved chunks. fp_01
("does not segment by state" with general meal limits in markdown
bullets) passes — bullets aren't numbered lists, no chunk refs in
the answer body.

### Three-layer verification

After the fix, verified at three layers:

1. **Function-level test:** un_01's recorded answer truncates from
   1668 to 257 chars. Truncation note populated. Negative tests on
   rg_01 and fp_01 stay silent.

2. **Negative tests:** rg_01 (absence-flavored opening + legitimate
   $500/12mo content) and fp_01 (dimension-absent + general meal
   limits) both unchanged. The bullet-vs-numbered structural
   distinction holds.

3. **Production UI:** un_01 and un_02 both show clean refusals AND
   the truncation note in the Data limitations panel:
   "Answer trimmed: 12 over-narration sentence(s) suppressed (TOPIC
   ABSENT — tool output should not appear in final answer)."

Same 12-count for both un_01 and un_02 is interesting — could be
coincidence or could indicate the agent produces a similarly-shaped
template for both questions. Doesn't matter functionally; both
truncate cleanly.

### Architectural finding: epistemic transparency

The un_02 production response also surfaces the groundedness check:

> "Groundedness check flagged ungrounded claims: The policy does
> not address specific rules for distributing drug samples to
> healthcare professionals (HCPs).. Judge reasoning: The retrieved
> content discusses general guidelines and legal requirements for
> drug sample distribution but does not specifically mention Nova
> Pharma's rules, making the claim ungrounded."

The user sees both "the policy doesn't address this" AND "the
system can't verify that absence claim against retrieved chunks."
That's appropriate epistemic transparency.

The groundedness judge can't ground absence claims by design —
"X is not in this corpus" can't be verified from chunks of the
corpus. The data_limitations panel surfaces this honestly rather
than hiding it. Worth noting as a UX pattern: regulatory-context
RAG should surface groundedness uncertainty even when the answer
is correct.

### What 1.2d didn't fix (carrying forward)

- **un_02 fabrication-with-attribution pattern (PARTIALLY)**. The
  post-processor strips the narration, which removes the false
  attribution as a side effect. But the underlying behavior —
  agent citing chunks for content that may not be in those specific
  chunks — could surface in other contexts. Not actively suppressed,
  just hidden by the post-processor when it fires. Phase 5 if it
  recurs elsewhere.

- **Empty agent_reasoning field in baseline output.** Reasoning-trace
  inspection on Apr 29 found agent_reasoning was empty for un_01.
  Telemetry gap. If we want reasoning-trace diagnostics in the
  future, the harness needs to capture it reliably. 1.2e item.

- **RAGAS max_tokens crash on long-answer entries.** Today's harness
  rerun failed on ret_01 (~18+ statement decomposition exceeding
  1024/2048/3072 max_tokens across three retries). Same root cause
  as the Faithfulness=None pattern that 1.2e was originally scoped
  to investigate. Decision today: skip full harness rerun, rely on
  function-level + UI verification. 1.2e proper investigation
  deferred.

- **Bulleted over-narration coverage gap.** The post-processor
  catches numbered lists with chunk refs. If the agent produces
  bulleted over-narration instead, it slips through. No evidence
  of this pattern in baselines so far, but the discrimination
  between "legitimate bulleted content" (rg_01, fp_01) and
  "illegitimate bulleted narration" would be harder than the
  numbered-list line. Captured in lessons log; addressed if
  observed.

### Closing observation across the 1.2d arc

Five process findings worth keeping:

1. **The right layer for stochastic-instruction-following bugs is
   below the LLM.** Helpfulness training competes with prompt
   instructions. Code-level safety nets win because they're
   deterministic.

2. **Single-run baselines can't verify conditional safety nets.**
   Yesterday's clause (d) experience showed sampling variance
   misses circumvention patterns. Today's experience showed the
   inverse — when the failure didn't occur in the sample, the
   absence of failure looked like the safety net working.

3. **Direct testing with recorded-bad inputs is the right
   verification method for safety nets.** Replay un_01's known-bad
   answer through the function. Cheap, deterministic, decoupled
   from agent stochasticity.

4. **The verification-discipline cost was negligible.** ~5 minutes
   to run the diagnostic, found a real bug. Without that discipline,
   1.2d would have closed today claiming a fix that didn't actually
   work in the failure case.

5. **The 1.2d work spans the full arc the article needs.** Wrong
   diagnosis → architectural option choice → implementation →
   false-clean episode → direct verification catches bug → small
   fix → three-layer verification → close. Each step is a finding.

### One more thing — the verification-vs-implementation tension

The first 1.2d implementation pass took ~2.5 hours. The bug fix
+ three-layer verification took ~3 hours total today. Same scope,
roughly same time, but the second pass produced a verified safety
net instead of a non-functional one.

The difference was the verification discipline. Cursor's first pass
optimized for "implement and run baseline." The second pass added
"verify the function does what it's supposed to do, in isolation,
before trusting the baseline." That extra step caught the bug
that the baseline alone couldn't see.

For the article: implementation discipline isn't enough.
Verification discipline is what makes implementations reliable.
The cost of direct verification is small; the cost of shipping
non-functional safety nets compounds.

---

## 2026-04-30 — 1.2e: harness telemetry fixes, variance characterization, naming collision

1.2e bundled three telemetry concerns: RAGAS Faithfulness=None on
long-answer entries (issue 1), RAGAS max_tokens crash on the same
entries (issue 2), and empty agent_reasoning field across all
baselines (issue 3). All three closed cleanly with small additive
changes. The interesting findings are downstream — the variance
characterization that emerged from running three baselines and the
naming collision that almost caused a misread of verification.

### Issues 1 and 2: same root cause, single-line fix

The Faithfulness=None historical pattern (ret_01, ret_03, ret_05)
turned out to be the same bug as today's max_tokens crash. The
RAGAS judge LLM was decomposing long answers into ~18+ statements
for verification, generating structured JSON output that exceeded
the configured max_tokens budget. When the budget was the silent
default (~1024), RAGAS retried 3x at 1024/2048/3072 tokens and
either gave up (writing None) or crashed (today's experience).

Cursor's audit identified one llm_factory call at line 542 of
run_evaluation.py with no max_tokens parameter. Single-line change:

    _llm = llm_factory("gpt-4o-mini", client=_client, max_tokens=8192)

Result: ret_01 Faithfulness 1.000, ret_03 Faithfulness 1.000,
ret_05 Faithfulness 0.952 — three entries that always wrote None
now produce real scores. Zero InstructorRetryException warnings.
The historical Faithfulness=None pattern was never about the
agent's answers being unverifiable; it was about the judge's
output being too long to fit in the configured budget.

This is the cleanest single-line fix in the entire 1.2 arc. Months
of "we don't know how those entries actually score" replaced with
"here are the numbers" by changing one parameter.

### Issue 3: telemetry gap that was almost trivial

agent_reasoning was empty for every entry in every baseline going
back to Apr 25. Yesterday's reasoning-trace inspection (during
1.2d's un_01 diagnosis) found the gap. The fix turned out to be
two additive lines in run_evaluation.py extracting the field from
agent_response into the result record. Audit confirmed the agent
endpoint already returned reasoning; the harness just wasn't
reading it.

### The guardrail trip: variance vs side-effect

Cursor's first post-fix run (20260430T164421Z) tripped the 0.05
behavioral-side-effect guardrail on AnsRel (-0.075) and CtxRec
(-0.052) vs the prior baseline (20260430T161450Z, the issue 1+2
canonical). Cursor reported "this is variance, the change is
purely additive telemetry capture and cannot affect what the agent
returns or what RAGAS scores."

I went back and forth on whether to accept that assessment. Three
moves worth replaying:

First move: I argued for Option A — close on the strength of the
diff inspection alone. The diff was two additive lines plus a
baseline pointer bump. Mathematically can't affect behavior.

Second move: pushback on Option A. The lesson from yesterday's
1.2d work was specifically about not trusting single-run
measurements for stochastic systems. By recommending Option A I
was contradicting that lesson while drafting the article that
captured it.

Third move: switched to Option B (run a second baseline) but RAGAS
hung mid-run because OPENAI_API_KEY wasn't loaded in the terminal
session. After fixing the env, ran the second baseline cleanly.

Run 2 (20260430T174050Z) landed at:
- Faithfulness 0.756 vs run 1's 0.756 — identical
- AnsRel 0.481 vs run 1's 0.492 — within 0.011
- CtxPrec 0.672 vs run 1's 0.673 — within 0.001
- CtxRec 0.661 vs run 1's 0.656 — within 0.005

Run 1 and run 2 are essentially the same baseline. The earlier
20260430T161450Z run was the high-variance outlier. Variance
confirmed empirically.

The lesson generalizes: when guardrails trip on changes that
mathematically cannot affect behavior, the trip is variance.
But you don't know the change is mathematically harmless until
you've inspected the diff. And you don't know the variance band
is wide enough to explain the trip until you've sampled it.
Both checks together convict the variance assessment.

For the article: stochastic LLM-judged metrics on small
(16-entry) samples have a variance band of ~0.05-0.10 on
aggregate metrics, ~0.40+ on individual category metrics
(false_premise AnsRel swung 0.648 → 0.200 between runs on the
same code). Single-run baselines at this magnitude are not
reliable signals.

### The naming collision: safety_net_fired flag

After the variance investigation closed, I expected to write the
1.2e lessons log and move on. Then I checked whether the
post-processor was firing on baseline runs (yesterday's UI test
showed it firing in production; we wanted baseline confirmation).

The 20260430T174050Z summary said `safety_net_fired=True` for
fp_01 and fp_02 — false_premise entries we hadn't expected to
trigger the post-processor. Direct testing showed the post-
processor function correctly returns the input unchanged for
fp_01 (length 1130 → 1130, note=None). The function was innocent.

But the flag said it fired. Where was the flag actually being
set?

grep across the codebase: `safety_net_fired` exists only in
run_evaluation.py — six references all in the harness, none in
agents/policy_agent.py or agents/post_processors/over_narration.py.
The flag is harness-computed, not post-processor-derived.

Reading run_evaluation.py:138-140:

    safety_net_fired = any(
        "safety net" in lim.lower()
        or ("scope" in lim.lower() and "retrieved" in lim.lower())
        for lim in limitations
    )

The flag fires when data_limitations contains either "safety net"
text or "scope" + "retrieved" together. Per the comment, it was
originally designed to track scope-mismatch warnings from
`_detect_scope_mismatch` and `_detect_unsupported_scope_dimension`
in policy_agent.py. fp_01 and fp_02 trigger the scope-mismatch
detection because they ask about dimensions (state, specialty)
the policy doesn't segment by — that's been firing since Apr 25,
in every baseline.

The post-processor's truncation note ("Answer trimmed: N
over-narration sentences suppressed") does NOT contain "safety
net" or "scope" + "retrieved", so it doesn't trigger this flag.

What this means: **the post-processor activity is invisible to
the harness's safety_net_fired tracking**. We confirmed it fires
in production via UI screenshots yesterday. We have no harness
telemetry tracking it. The flag we'd been reading as "post-
processor fired" was actually counting a different mechanism
entirely.

Two compounding factors made this almost-misread:

1. The flag name suggests "any safety net mechanism" but the
   implementation only catches scope-mismatch.
2. The historical baselines (every one back to Apr 25) show
   safety_net_fired_count=2 in false_premise category. We'd been
   reading those as "fp_01 and fp_02 trigger safety nets" without
   ever questioning what specific safety net.

The post-processor we built yesterday is genuinely silent in
baseline metrics. The only place its activity is visible is
production UI data_limitations panel.

For 1.2g: disambiguate the flag. Either rename to
`scope_mismatch_detected` (precise) or extend the detection logic
to also catch the post-processor's truncation note (broad). Either
is a small fix that prevents future misreads.

### Three baselines today, three readings

Today's three runs surface the variance band cleanly:

| Run | Time | Faith | AnsRel | CtxPrec | CtxRec |
|---|---|---|---|---|---|
| 161450Z | morning | 0.779 | 0.567 | 0.696 | 0.708 |
| 164421Z | post-issue 3, run 1 | 0.756 | 0.492 | 0.673 | 0.656 |
| 174050Z | post-issue 3, run 2 | 0.756 | 0.481 | 0.672 | 0.661 |

Three runs, same code post-164421Z, same dataset, same prompt.
AnsRel range: 0.567 - 0.481 = 0.086. CtxRec range: 0.708 - 0.656
= 0.052. These are not regressions; they're sampling variance.

The article wants this finding sharp: even with deterministic agent
configuration and frozen prompt, RAGAS metrics fluctuate 0.05-0.10
on aggregate and 0.40+ on individual categories. Single-run
baselines mislead. Multi-sample-per-entry baselines or larger
golden datasets would tighten the variance band, but neither was
in 1.2e scope.

### What 1.2e closes

Issue 1+2 (max_tokens): closed. ret_01, ret_03, ret_05 now produce
real Faithfulness scores. Single-line config change.

Issue 3 (agent_reasoning): closed. All 16 entries have populated
reasoning traces. Two-line additive change.

Variance assessment: empirically confirmed. Run 2 and run 3 within
0.011 of each other. The earlier morning run was the higher-variance
outlier. The 0.05 guardrail on telemetry changes is too tight given
the natural variance band of these metrics; future guardrails for
non-behavioral changes should be set at 0.10 or wider.

Naming collision: documented for 1.2g disambiguation. Post-processor
activity is invisible to harness telemetry; only production UI
shows it.

### What 1.2e didn't fix (carrying forward)

- **Post-processor activity not tracked in harness telemetry.**
  1.2g item — disambiguate `safety_net_fired` flag to either
  rename it or extend detection to catch the post-processor's
  truncation note.

- **rule_backed CtxPrec gate persistently fails** (0.467 in run 1,
  0.410 in run 2, 0.467 across runs). Driven by rb_01 and rb_02
  retrieval ranking — chunks that don't precisely match the rule
  being asked about. Pre-existing issue, not 1.2e's responsibility.
  Phase 5 retrieval refinement.

- **un_02 fabrication-with-attribution.** Carried from yesterday.
  Post-processor strips it as a side effect when un_02 over-
  narrates, but the underlying behavior persists in scenarios
  where the post-processor doesn't fire. Phase 5 if recurs in
  other contexts.

### Closing observation: when verification discipline overshoots

The 1.2e closure surfaces a meta-question: when does verification
cost exceed verification value?

Three verification investments today:

1. Diff inspection on the issue 3 telemetry change — cheap (~30
   seconds), conclusive (proved the change cannot affect behavior).

2. Second baseline run to confirm variance — cost ~25 minutes
   (including the first hung-on-env-issue attempt and the
   recovery), value moderate (confirmed assessment but didn't
   change the conclusion).

3. Investigation of the safety_net_fired naming collision — cost
   ~20 minutes, value high (caught a long-standing telemetry gap
   that would have produced repeated misreads of baselines).

Investment #1 was clearly worth it. #3 was clearly worth it. #2
is debatable — the diff inspection alone would have closed 1.2e
honestly. The second baseline confirmed but didn't change the
verdict.

Verification discipline isn't free. The cost is time and energy.
The right level of verification matches the cost of being wrong.
For #1, the cost of being wrong was zero (diff inspection is a
math proof). For #3, the cost of being wrong was potentially
months of misread baselines. For #2, the cost of being wrong was
~5% probability of an actual behavioral side-effect, which the
diff already ruled out.

This isn't an argument against verification. It's an argument for
calibrating verification depth to the situation. Today I oversampled
on #2 and undersampled on #3 (didn't notice the naming collision
until after the second baseline). Next time the right move is
sequential verification — cheapest checks first, then escalate
only if cheaper checks leave residual uncertainty.

For the article: the verification-discipline lesson is real, but
"verify everything" isn't the takeaway. "Verify cheaply first,
escalate based on residual uncertainty" is sharper.

---

## 2026-04-30 (later) — 1.2g pass 1: three small fixes that surfaced a real bug

1.2g pass 1 was scoped as three quick UI/telemetry fixes after
1.2e closed. Citation excerpt display threshold, container rebuild
runbook, safety_net_fired disambiguation. Bounded, low-risk, ~60 min
Cursor session.

The first two landed cleanly. The third — the disambiguation —
revealed a real bug we'd been carrying invisibly across multiple
baselines.

### Fix 1: citation excerpt display threshold

streamlit_app/pages/5_Policy_QA.py:258 — Python slice truncating
chunk excerpts at 200 characters before display. The 1.2c excerpt
fix surfaced full chunk text to the agent, but the Streamlit UI
was independently truncating for render. Single character change:

    excerpt[:200] -> excerpt[:500]

Streamlit container needs rebuild for the change to surface. Fix
is genuinely small but operationally requires the container
freshness discipline that fix 2 documents.

### Fix 2: container rebuild runbook

docker/README.md gained a 14-line "Rebuilding after agent code
changes" section. Three-command sequence (down / build --no-cache
api / up -d), health check verification, --no-cache rationale.

This addresses the operational lesson from 1.2c-1.2e: container
freshness was the difference between "fix landed in production"
and "fix only landed in code" multiple times. Without a runbook,
future contributors hit the same trap.

### Fix 3: safety_net_fired disambiguation — and what it surfaced

This was supposed to be a small naming/precision fix. The flag in
run_evaluation.py:138 was originally designed to catch scope-mismatch
warnings (false_premise category triggers), but during 1.2d/1.2e
work I'd been reading it as if it tracked post-processor activity.
The mismatch was almost-misread during 1.2e closure.

Cursor's fix split the single flag into three:

    scope_mismatch_detected = any(
        "safety net" in lim.lower()
        or ("scope" in lim.lower() and "retrieved" in lim.lower())
        for lim in limitations
    )
    over_narration_stripped = any(
        "answer trimmed" in lim.lower()
        or "over-narration" in lim.lower()
        for lim in limitations
    )
    safety_net_fired = scope_mismatch_detected or over_narration_stripped

Verification baseline (20260430T191343Z) showed the disambiguation
working as intended:

| Category | safety_net_fired | scope_mismatch | over_narration |
|----------|------------------|----------------|----------------|
| rule_backed | 0 | 0 | 0 |
| retrieval | 1 | 0 | **1 (ret_02)** |
| unanswerable | 1 | 0 | **1 (un_02)** |
| false_premise | 2 | 2 | 0 |
| registry_gap | 0 | 0 | 0 |
| ALL | 4 | 2 | 2 |

scope_mismatch_count=2 for false_premise matches every historical
baseline back to Apr 25. ✓

over_narration_count=2 captures ret_02 and un_02 — events that
were firing in 1.2d/1.2e but invisible in the counts.

Then I noticed the ret_02 row.

### The ret_02 finding

ret_02 question: "What are the criminal penalties for violating
the federal anti-kickback statute?"

This is a retrieval-category question. The OIG corpus contains
the criminal penalties content. Retrieval should find it; the
agent should answer with the actual penalties.

Direct inspection of the new baseline showed:

agent_answer (188 chars): "The policy does not address the specific
criminal penalties for violating the federal anti-kickback statute.
If you have further questions or need additional information, feel
free to ask!"

data_limitations included:
- "Groundedness check flagged ungrounded claims: ... The retrieved
  content explicitly states the criminal penalties for violating..."
  (judge correctly noting the retrieval has the answer)
- "Answer trimmed: 6 over-narration sentence(s) suppressed"
  (post-processor fired and stripped 6 sentences)

Reconstructing what happened:

1. Agent retrieved chunks containing criminal penalties content
   (relevance scores adequate)
2. Agent classified question as TOPIC ABSENT — likely because the
   prompt biases toward "Nova Pharma's policy" framing and the
   penalties aren't in Nova Pharma's internal policy specifically
3. Agent narrated the OIG content in a numbered list anyway
   (refusal opening + over-narration pattern, exactly what the
   post-processor was designed to catch for un_xx)
4. Post-processor fired correctly per its rules — refusal opening,
   over-narration markers, chunk references all detected
5. Post-processor stripped the 6 sentences containing the actual
   penalties content
6. User receives a confidently-stated wrong answer: "the policy
   does not address" with no further detail

The post-processor's rules fired correctly. The classification
upstream (agent: TOPIC ABSENT) was wrong. The post-processor
amplified the error rather than catching it.

### Two compounding bugs

**Bug A — agent scope confusion.** The agent treats every question
as if it's implicitly about Nova Pharma's internal policy. ret_02
asked about federal law. The agent answered "for the question it
imagined" rather than "for the question that was asked."

**Bug B — post-processor's input assumption.** Post-processor was
designed assuming "if agent classifies as TOPIC ABSENT, narration
after refusal is unwanted noise." That assumption holds for
genuinely absent topics (un_01, un_02). It breaks when the
classification is wrong. The post-processor's discrimination is
"is this a TOPIC ABSENT case?" The discrimination it actually
needs is "is this a TOPIC ABSENT case where the agent classified
correctly?"

### Why three-layer verification couldn't catch this

1.2d's verification discipline was function-level + UI + harness.
All three layers verified the post-processor catches over-narration
cleanly. None of them could catch this failure mode because they
all assumed the agent's TOPIC ABSENT classification was reliable.

When the classification is wrong, verification of the post-
processor's behavior on that classification is verifying the
wrong thing. The post-processor stripped 6 sentences — that's
"correct" behavior given its inputs. Just the wrong outcome.

For the article: this is the sharpest version of "verification
is bounded by your input assumptions" the project has produced.
Three-layer verification of a safety net is necessary but not
sufficient. The system's correctness depends on each layer's
inputs being correct, all the way back to the upstream
classification.

### Historical pattern: this has been happening for weeks

Looking back at Grounded=False history, ret_02 has shown
ungrounded refusals across multiple baselines. We'd been reading
"Grounded=False on ret_02" as "judge can't verify ret_02's
absence claim" without checking whether the absence claim was
actually wrong. The judge was right; we were wrong about why.

The disambiguation fix is what made the post-processor's role
visible. Before today, post-processor activity on ret_02 was
silent in the harness telemetry. The flag we'd been reading as
"safety net fired" was only catching scope-mismatch (fp_01,
fp_02). Post-processor firing on ret_02 was invisible.

This is article-relevant in a different way: telemetry gaps hide
ongoing bugs. The flag's naming created a false sense that we
were tracking post-processor activity. We weren't. For weeks.

### What pass 1 closes with

Three findings, one substantial:

1. Citation excerpt threshold raised 200 → 500 chars (small UX win)
2. Container rebuild runbook documented (operational hygiene)
3. **safety_net_fired disambiguation surfaced ret_02 receiving
   post-processor truncation that strips correct content from a
   misclassified retrieval question.** Post-processor fired
   correctly per its rules; the upstream agent classification was
   wrong. The post-processor amplified the error rather than
   catching it.

### What pass 1 didn't fix (scoped for next)

ret_02 fix scheduled for the next session. Two-layer architectural
question to decide: agent-side scope-classification (root cause,
prompt-layer ceiling risk) vs post-processor scope-restriction
(pragmatic, doesn't fix wrong refusal opening). After the design
discussion, going with Option 2.5 — post-processor scope
restriction first, agent-side fix decided based on outcome.

The post-processor scope restriction will be: skip firing when
retrieval relevance is high OR question category is retrieval.
The agent's wrong refusal opening will remain visible to users in
the short term, but the user will at least see the actual content
the agent retrieved instead of a stripped refusal.

### Closing observation: pass 1 was supposed to be small

Pass 1 was scoped as three quick UI/telemetry fixes. That's what
it delivered. The ret_02 finding is a side effect of the
disambiguation fix making invisible activity visible — exactly
what disambiguation is supposed to do.

The lesson worth keeping: telemetry precision pays off in
unexpected ways. We weren't looking for a bug. The disambiguation
made the bug visible. The same finding could have been buried in
a bigger pass that bundled everything together.

For the article: small bounded telemetry fixes are high-leverage.
They surface findings that bigger refactors would obscure.

---

## 2026-04-30 (later still) — ret_02 post-processor scope restriction

The 1.2g pass 1 disambiguation surfaced ret_02 receiving silent
post-processor truncation across multiple baselines. Today's fix
addresses Bug B (post-processor input assumption) without addressing
Bug A (agent scope misclassification). Pragmatic Option 2.5 from
the design discussion — restrict post-processor activation by
retrieval relevance, leave agent prompt untouched.

### The fix

Two-layer change with single-parameter coupling:

agents/post_processors/over_narration.py — added optional
max_retrieval_relevance parameter with early-exit guard at function
top:

    if max_retrieval_relevance is not None and max_retrieval_relevance >= 0.55:
        return answer, None

agents/policy_agent.py — moved post-processor call to after citation
parsing, computed max relevance from citations, passed to function:

    _max_relevance = max(
        (c.relevance_score for c in citations),
        default=0.0,
    )
    truncated, note = strip_over_narration(
        agent_answer,
        max_retrieval_relevance=_max_relevance,
    )

Discrimination signal: if retrieval found high-relevance content,
the agent's TOPIC ABSENT classification is likely wrong, so the
post-processor should not strip the "over-narration" (which is
probably the actual answer).

### Why 0.55

Threshold chosen based on observed data from prior baselines:
- un_01 (genuinely absent): max relevance ~0.50 → guard doesn't fire
- ret_02 (misclassified): max relevance ~0.55-0.67 → guard fires
- un_02 (genuinely absent + tangential matches): max relevance ~0.58
  → guard fires (calibration concern)

### Verification baseline 20260430T203450Z

ret_02 — primary objective:
- over_narration_stripped: True → False ✓
- max_retrieval_relevance: 0.667 → guard fires
- agent_answer length: 188 → 1239 chars
- Faithfulness: 0.000 → 0.769 (+0.769)
- Grounded: still False (Bug A persists; agent's wrong refusal
  opening still flagged by judge)

un_01 — unaffected:
- over_narration_stripped: False (unchanged; pattern detection
  didn't fire this run, plus max_rel 0.501 below threshold)
- Faithfulness: 1.000 unchanged

un_02 — calibration casualty as predicted:
- over_narration_stripped: True → False
- max_retrieval_relevance: 0.582 → guard fires (above 0.55 threshold)
- agent_answer length: 180 → 1544 chars
- Faithfulness: 0.500 → 1.000

### The un_02 metric artifact finding

un_02's Faithfulness jumped 0.500 → 1.000 after the fix, which
looks like an improvement but isn't. Cursor's analysis was sharp:
the narrated content is technically faithful to retrieved chunks
(it cites real OIG/PhRMA content), so RAGAS Faithfulness rates it
high. But the chunks don't actually answer the user's question
about Nova Pharma's drug sample rules. AnsRel staying at 0.000
catches what Faithfulness misses.

This is a sharper version of the variance-vs-signal lesson from
1.2e: metrics that look like wins can be artifacts of changed
behavior rather than actual quality improvements. Different metrics
have different blind spots; you need to triangulate to distinguish
real signal from artifacts.

For the article: a fix that improves Faithfulness on a question
where the underlying answer is still wrong illustrates that
high-faithfulness ≠ high-quality. Faithfulness measures grounding
to retrieved context; AnsRel measures alignment with the question.
Both can be misleading individually; the combination is more
reliable.

### The threshold-tuning trap

Cursor recommended bumping the threshold from 0.55 to 0.60 as a
follow-on. At 0.60, ret_02 (max_rel 0.667) stays protected and
un_02 (max_rel 0.582) falls below threshold and gets stripped
correctly. Looks like a one-line clean-up.

We declined. Reasons captured here for the article:

1. The threshold is calibrated on three data points (ret_02,
   un_01, un_02). Moving it to clean up one observation might
   create new misclassifications we can't observe with this
   dataset.

2. The threshold approach is fundamentally fragile. We're trying
   to use one number (max retrieval relevance) to distinguish two
   conditions (genuinely-absent topic vs misclassified-as-absent).
   These conditions have overlapping relevance distributions. No
   single threshold can perfectly separate them.

3. Tuning a threshold to fit observed cases on a 16-entry dataset
   is small-sample optimization. It risks overfitting to specific
   entries while missing the structural problem.

The structural problem is that the post-processor's design
assumption ("if agent classifies as TOPIC ABSENT, the narration
after is unwanted") is wrong when the classification itself is
wrong. The threshold guard is a heuristic for "is the classification
likely correct" — but heuristics on small data are unreliable.

The correct fix is upstream: fix Bug A (agent scope confusion).
The agent shouldn't be misclassifying ret_02 in the first place.
That's prompt-layer work with all the prompt-layer ceiling concerns
documented across 1.2c-1.2d. We chose to defer Bug A and ship the
imperfect Option 2.5 fix.

For the article: pragmatic fixes paper over root causes. This is
sometimes the right call (limited time, working system) and
sometimes a debt-accumulation move. We made it consciously.

### What didn't get fixed

Bug A — agent scope misclassification — remains unfixed. ret_02's
agent_answer still opens with "the policy does not address the
specific criminal penalties for violating the federal anti-kickback
statute" even though the answer is in the corpus. The user sees:
- Wrong refusal opening (Bug A, unaddressed)
- Followed by 1051 chars of correct anti-kickback content (because
  post-processor didn't strip)

This is better than the previous state ("wrong refusal alone, no
content") but it's not correct. A user reading this might be
confused by the contradiction between "policy does not address"
and the substantive content that follows.

Carrying as documented limitation. Phase 5 would address Bug A
through prompt-level scope classification (high-risk per 1.2c
findings) or system architecture changes (out of scope for
pre-LWD).

### CI gate margin: rule_backed Faithfulness

The new baseline shows rule_backed Faithfulness 0.717 — above the
0.7 floor but with reduced margin compared to recent baselines
(0.815 in 1.2c, 0.812 in 1.2e canonical, 0.782 just before this
fix). The drop is LLM variance on rb_02 and rb_04, unrelated to
the ret_02 fix (rule_backed entries don't get post-processed).

Margin of 0.017 is tight. One more variance-driven dip and the
gate fails. Worth flagging for future runs — if rule_backed
Faithfulness drops below 0.7 on a future baseline, first move is
"rerun" rather than "investigate." Variance is a known characteristic
at this dataset size.

### What ret_02 fix closes

Two-layer architectural intervention with one-parameter coupling.
Verified at function level (audit + integration check) and harness
level (per-entry verification on ret_02, un_01, un_02). UI
verification deferred — the user-facing improvement is visible in
the longer agent_answer text, no UI change needed.

Three findings worth keeping:

1. **Threshold-on-relevance as discrimination signal** works for
   the bug it was designed to address (ret_02 misclassified
   retrieval question). It papers over a calibration concern
   (un_02 borderline) and doesn't fix the root cause (Bug A,
   agent classification). Honest pragmatic fix.

2. **Faithfulness can be a metric artifact** when answer behavior
   changes structurally. un_02 Faithfulness jumped 0.500 → 1.000
   without the answer actually getting better. Triangulating
   metrics (Faithfulness + AnsRel) catches what either alone
   misses.

3. **Small-sample threshold tuning is fragile.** Moving a
   threshold to clean up one observation risks new
   misclassifications we can't see with the current dataset.
   The fix accepted the un_02 trade-off rather than chasing
   threshold tuning.

### What this work didn't change

The system prompt. The ABSENCE HANDLING block. The PHRMA
COMPARISON AUTHORITY clauses. ret_02 fix is purely architectural —
a code-level guard on when an existing safety net activates. Same
design pattern as 1.2d post-processor itself.

The agent_reasoning telemetry from 1.2e. Whatever ret_02's
reasoning trace shows about why it misclassified is captured in
20260430T203450Z's results.json. Investigating that trace is part
of any future Bug A work.

---

## 2026-05-01 — 1.2g pass 2: four UX items, one investigation, one
   transparency win

Pass 2 was scoped as four bounded items after pass 1 closed yesterday.
Each had a UX decision made before the Cursor prompt, encoded in the
prompt itself rather than left for Cursor to choose. The session
delivered all four cleanly, plus one investigation finding worth
keeping.

### Item 1: Citation weak-match visual indicator

Pre-pass-2 state: streamlit_app/pages/5_Policy_QA.py used a single
threshold _CITATION_WEAK_THRESHOLD = 0.30 to hide low-relevance
citations. After the 1.2a embedding fix, retrieval scores landed in
the 0.40-0.55 range — chunks above the floor but often substantively
wrong for the question.

Decision: keep the 0.30 hide-threshold, add a 0.50 strong-threshold,
distinguish weak matches visually.

Three categories:
- relevance >= 0.50 — display normally
- 0.30 <= relevance < 0.50 — display with "⚠ Weak match" label
- relevance < 0.30 — hidden (unchanged)

Cursor's audit found something worth keeping: the OLD logic was
inverted from the intent. is_weak was being computed as
0 < score < 0.30 — meaning the warning was appearing on citations
that should have been hidden, while the genuinely-weak-but-visible
band (0.30-0.50) had no warning. The fix corrected both behaviors
simultaneously.

This is the kind of pre-existing quiet bug that surfaces only when
you re-examine code with fresh intent. The "weak match" concept
existed in the codebase but was applied to the wrong band. Anyone
reading commit history before today would see weak-match warnings
appearing inconsistently and assume the threshold was just poorly
calibrated. The actual bug was semantic — the wrong condition was
controlling the indicator.

For the article: re-examination of code with a specific UX question
in mind ("when should we warn about weak matches?") surfaces semantic
bugs that existed quietly. The 0.30 threshold itself was correct;
how the threshold was used was inverted.

### Item 2: Query phrasing sensitivity investigation

Pre-pass-2 knowledge: yesterday's UI tests showed paraphrased queries
sometimes produced different answers. "Annual meal cap for HCP"
produced wrong answer; "Nova Pharma's annual meal cap for HCPs?"
produced correct answer. We didn't know how systematic this was.

Decision: brief sampling first, escalate to full investigation if
signal warrants, no time cap.

Cursor ran 9 queries (3 entries × 3 paraphrases each):

**rb_02 (speaker FMV):** Zero sensitivity. All 4 paraphrases
returned the correct $3,500/SPEAKER_001 answer. Why: lookup_rule
provides answer-level stability independent of retrieval phrasing
variation. The structured rules registry insulates rule_backed
answers from phrasing noise.

**rg_01 (annual meal cap):** Moderate sensitivity, but at the
*confidence* level, not the *answer* level. Dropping "Nova Pharma"
prefix caused rule lookup to fail → confidence dropped to "low"
even when answer text was correct. Imperative framing ("Explain
how much...") produced PARTIAL answers (per-meal limits without
the absence clarification).

**ret_02 (anti-kickback penalties):** Zero sensitivity attributable
to phrasing. All 4 paraphrases produced the same TOPIC ABSENT +
over-narration pattern. The 0.55 guard from the ret_02 fix yesterday
papers over the symptom; the agent's underlying scope misclassification
(Bug A) is the real issue and is independent of phrasing.

Three findings worth keeping:

**Finding 1: registry-grounded answers are robust to phrasing.**
The structured rules registry decouples "did the agent find the
right rule?" from "was the question phrased perfectly?" When
lookup_rule succeeds, the answer is stable across paraphrases.

**Finding 2: framing affects confidence calibration more than
answer text.** rg_01's answer text was correct across paraphrases,
but the agent's confidence dropped when "Nova Pharma" wasn't
prefixed. The agent uses framing cues to gauge its own certainty.
Users see confidence-low answers and may distrust correct content.

**Finding 3: imperative framing produces less complete answers than
question framing.** "Explain how much..." vs "What is...?" produced
materially different output structures (PARTIAL vs full). The agent
treats framing as a signal about expected response shape.

For the article: query sensitivity has multiple dimensions. Answer
text robustness, confidence calibration robustness, and response
shape robustness are different properties. A system can be robust
on one dimension and fragile on another. Reporting "the system
handles paraphrasing fine" without distinguishing these dimensions
papers over real fragility.

Cursor recommended bumping the over-narration guard threshold from
0.55 to 0.60 as a one-line follow-on (suggested as a "1.2h" task)
based on the ret_02 finding. We declined for the same reasons as
yesterday: small-sample threshold optimization risks unobserved
regressions. The investigation reinforced the bump's plausibility
(ret_02 phrasing-invariant in its current state) but didn't change
the base concern. Carrying as backlog item, not scheduling as 1.2h.

### Item 3: Comparison table relevance filter

Pre-pass-2 state: the Nova Pharma vs PhRMA panel showed all
nova_vs_phrma rows regardless of question. rb_03 (annual cap) showed
meal limits in the comparison table even though meals weren't
relevant.

Decision: filter to comparisons where source_rule_id appears in
rule_thresholds. Show "Comparison not available for this query"
when the filtered set is empty.

Cursor's implementation note surfaced an honest limitation:
nova_vs_phrma entries are derived from the same lookup_rule calls
that populate rule_thresholds. Their source_rule_id values are
always a subset of rule_thresholds. So the filter doesn't actually
remove rows for questions where the agent retrieved those rules —
it primarily ensures "Comparison not available" displays in
TOPIC ABSENT cases (un_xx).

The pre-existing visible problem from yesterday — "rb_03 shows
meal limits alongside compensation cap" — wasn't fixed by Item 3.
That problem is upstream agent behavior: the agent calls lookup_rule
for meal rules even when the question is about compensation. The
filter works correctly per spec; the spec didn't address the
upstream problem.

This is the right call. Trying to fix agent over-retrieval through
UI filtering would either:
- Hide rules the agent legitimately considered (loss of transparency)
- Require UI to second-guess the agent's reasoning (architectural
  inversion — UI shouldn't know better than agent)

Filtering on rule_thresholds is the correct seam: "show comparisons
the agent referenced." If the agent referenced too many, that's
agent work, not UI work.

For the article: not every UI symptom has a UI fix. Filtering UI
output is the right intervention when the data flow has stale
remnants from previous queries. It's the wrong intervention when
the underlying data is over-broad for the current query. Knowing
which case you're in matters for choosing the layer to fix.

### Item 4: Citation grounding indicator

Pre-pass-2 state: users couldn't tell whether the answer was
grounded in rules registry vs retrieval chunks. Both looked equally
authoritative.

Decision: informational "Grounded in:" line below the answer.
Format:
- Both sources: "Grounded in: rules registry [COMP_001, MEAL_001]
  + retrieval (3 chunks)"
- Registry only: "Grounded in: rules registry [RULE_IDs]"
- Retrieval only: "Grounded in: retrieval (N chunks)"
- Neither: "Grounded in: no specific source"

Implementation landed cleanly via st.caption() positioned between
the answer text and the confidence bar.

The unexpected transparency win: Item 4's grounding line surfaces
Item 3's underlying limitation in a user-visible way. Image 5's
screenshot for the rb_03 question (annual compensation cap) shows:

    Grounded in: rules registry [COMP_001, COMP_003, COMP_002,
    MEAL_001, MEAL_002] + retrieval (3 chunks)

The COMP rules are appropriate for the compensation cap question.
The MEAL rules aren't. Before Item 4, this over-retrieval was
invisible. After Item 4, it's transparent — users can see the
agent retrieved more rules than the question warranted, even
though the answer text doesn't mention meal limits.

This is a side effect of the transparency fix: making one thing
visible (which sources contributed) also makes adjacent things
visible (when those sources are noisy). The grounding line
incidentally diagnoses the agent over-retrieval issue without
requiring any agent-side change.

For the article: transparency UI elements diagnose upstream
problems. Adding visibility to one layer surfaces issues in
adjacent layers. The grounding line wasn't designed to expose
agent over-retrieval, but it does, and that's valuable.

### What pass 2 closes

Three UI changes landed cleanly with appropriate visual treatment:

1. Weak-match indicator (semantic correction of pre-existing
   inverted logic, not just a new feature)
2. Comparison table filter (works correctly per spec; documented
   limitation around upstream agent behavior)
3. Grounding indicator (informational, well-positioned, surfaces
   adjacent transparency issues as side effect)

One investigation document delivered:
- evaluation/policy_ragas/findings/query_phrasing_sensitivity.md
  with 9-query sampling and three findings

Three findings carrying forward:

- **Agent over-retrieval (Bug C):** Agent calls lookup_rule for
  rules outside the question's scope (meal rules for compensation
  questions). Visible now via grounding indicator. Same pattern
  as Bug A (agent scope confusion); same prompt-layer fix risks.

- **Confidence calibration sensitive to framing:** rg_01 paraphrase
  testing showed confidence drops when "Nova Pharma" prefix is
  dropped, even when answer is correct. Agent uses framing cues for
  confidence. Worth understanding for the article; not blocking.

- **Threshold bump 0.55 → 0.60 deferred:** Cursor's suggested 1.2h
  task. Small-sample threshold optimization risk unchanged from
  yesterday's reasoning. Backlog item, not scheduled work.

### Closing observation: pass 2's design discussion paid off

Yesterday morning's design discussion before writing the Cursor
prompt was the difference between this clean closure and the
pattern from earlier sessions. Each item had its UX decision made
explicitly:
- Item 1: B (weak-match indicator) over A (hard cutoff) or C (hybrid)
- Item 2: C with escalation (sampling + escalate) over A (full) or
  B (defer)
- Item 3: A (filter) over B (de-emphasize) or C (top 3 + expander)
- Item 4: D (informational line) over C (dual viz, originally chosen)

The Item 4 downgrade from C to D was particularly important. C
would have required architectural decisions about contribution
metadata in the agent response. D delivered the same user value
in a fraction of the work. We made that decision deliberately
before writing the prompt; if we hadn't, Cursor would have built
something larger than needed.

For the article: design discussions before code save more time
than they cost, especially when multiple items have UX decision
points. The cost is 30-60 minutes of structured thinking. The
benefit is avoiding 1-2 hour scope expansions per item.

---

## 2026-05-02 — 1.2f session 1: inline source viewer foundation

1.2f is the inline source viewer — PDF rendering inside the Streamlit
Q&A page so users can see citations in their original document
context. Largest architectural change since the original Phase 4
dashboard. Scoped across multiple sessions.

Session 1's goal was foundation: PyMuPDF rendering working in
Streamlit, page display from chunk citations, no highlighting yet.
Session 2 (highlighting + excerpt removal) and any further polish
sessions build on this.

### The five architectural decisions made before code

The kickoff design discussion (~30 min before any Cursor work) made
five decisions deliberately:

1. **PDF rendering:** PyMuPDF server-side rasterization. Already a
   project dependency from chunking pipeline. No JavaScript
   debugging surface. Highlighting via colored rectangles drawn
   before rasterization.

2. **Highlighting fidelity:** Chunk-level bounding box (deferred to
   session 2). Page-level v1 was rejected as undersell; section-level
   was rejected as overkill given chunk metadata already exists.

3. **UI integration:** Expander below each citation. Native Streamlit,
   no custom components, no layout disruption. Each citation gets
   independent expansion control.

4. **Scope guardrails:** v1 only — single-page rendering, no multi-
   page navigation, no zoom controls. v2 features deferred indefinitely.

5. **Live with caching:** @st.cache_data on the renderer. PyMuPDF
   rendering is fast enough for on-demand. Static rendering would
   complicate the dev loop.

These decisions were locked before the Cursor prompt was written.
The prompt encoded each decision as a constraint, not a choice for
Cursor to make. This pattern paid off cleanly — Cursor implemented
exactly what was decided rather than picking variants.

### The audit-first finding: propagation gap, not absence gap

Cursor's audit surfaced two gaps:

**Gap 1: page_num not propagating end-to-end.**

Per-component, every layer had page_num:
- pipelines/embed_policy_docs.py sets it (1-indexed)
- Qdrant payload contains it
- search_policy_docs tool returns it from payload
- Agent's tool call receives chunks with page_num

But _parse_citations() in policy_agent.py constructed PolicyCitation
objects without the field. The schema (agents/schemas.py) didn't
have it. Streamlit received PolicyCitation objects with no page
information.

Cursor framed this precisely: "propagation gap, not absence gap."
The metadata existed throughout the pipeline; one boundary was
silently dropping it.

**The constraint conflict.** The original Cursor prompt said "DO NOT
modify agent response schema." That constraint was guarding against
scope expansion via "let's add some new metadata fields." This wasn't
new metadata — the field existed elsewhere; we just stopped throwing
it away at one boundary. Different risk profile entirely.

I overruled my own constraint. Adding `page_num: Optional[int] = None`
to PolicyCitation with one line populating in _parse_citations() was
the right fix. Option B (Qdrant lookup from Streamlit) was the
"don't modify schema" alternative — it would have added Qdrant client
dependency to the UI layer, infrastructure coupling, additional env
var management, container rebuilds. All to recover information that
was already available and being silently discarded.

For the article: per-component testing wouldn't catch field-drop
bugs at boundaries. End-to-end audits surface them. Per-component,
each piece works correctly — Qdrant stores, search returns, parse
extracts what it cares about. The end-to-end view is "the field
exists at start and is gone at end."

**Gap 2: PDF files not mounted in Streamlit container.**

data/raw/policy_docs/ wasn't in the Streamlit service volumes.
Only features/outputs was mounted. PyMuPDF needs file system access.

One-line fix: add the volume mount to docker-compose.yml as
read-only. Within scope, unambiguous.

### What landed in session 1

- agents/schemas.py:24 — page_num: Optional[int] = None added to
  PolicyCitation
- agents/policy_agent.py:322 — page_num=r.get("page_num") propagated
  in _parse_citations()
- docker/docker-compose.yml:69 — data/raw/policy_docs volume mount
  added to Streamlit service (read-only)
- streamlit_app/utils/pdf_renderer.py — new module with
  @st.cache_data render_pdf_page(source_doc, page_num) → bytes |
  None using PyMuPDF at 2× zoom (~144 DPI)
- streamlit_app/requirements.txt — pymupdf>=1.24.0 added
- streamlit_app/pages/5_Policy_QA.py — st.expander("View source · p.
  N") under each above-threshold citation, renders the PDF page
  inline at readable resolution

### The off-by-one false alarm

Verification testing surfaced a moment that looked like an off-by-one
bug. Two screenshots both labeled "View source · p. 1" appeared to
show different content. I flagged it as red-flag worth pausing on.

Then I realized the screenshots showed different vertical scroll
positions of the same rendered page. The renderer produces a tall
image (full PDF page); what fits in any given screenshot frame
depends on where the user has scrolled. Both images were portions
of PDF page 1 — the cover/section-1 area at the top, sections 2-3
at the bottom of the same page.

User confirmation of actual PDF structure (page 1 contains sections
1, 2, 3-beginning; page 2 contains 3-continued through 4.1) verified
both screenshots were consistent with PDF page 1.

For the article: visual verification is bounded by what's in the
frame. Looking at "what's visible" in a screenshot can mislead when
the actual artifact extends beyond the frame. Diagnostic instinct:
if something looks wrong, check whether you're seeing all of it.

The lesson here is small but real. The verification methodology
needs to account for the rendering artifact extending beyond the
viewport. A more thorough check would have been "scroll to the
top/bottom of the rendered page and verify it matches expected
content for that PDF page." We did that retroactively (asked user
to compare actual PDF page contents) and confirmed alignment.

### Verification outcomes

All five tests passed cleanly:

1. Basic rendering across documents ✓ — verified on
   nova_pharma_internal_policy_SYNTHETIC.pdf, oig_cpg_pharmaceutical.pdf,
   phrma_code_2022.pdf
2. Page number accuracy ✓ — diagnostic confirmed PDF page contents
   match expander captions across all tested citations
3. Caching behavior ✓ — instant on reopen, fresh on different
   page/document
4. Weak match citation interaction ✓ — expander renders correctly
   below weak match styling
5. TOPIC ABSENT case ✓ — expanders work on retrieval citations,
   "Comparison not available" still shows, grounding line shows
   correctly

### Session 2 scope decision: excerpt removal deferred to session 2.5

User raised a sharp design observation during verification: "View
source should highlight the citation text in yellow. If it does
that, then we can remove the citation text above Relevance metric."

This is a better information architecture than the current state.
Currently each citation card shows the chunk text twice — once as
italic excerpt, once when expanded and the user finds the same
text in the rendered page. Yellow highlighting in the rendered page
makes the excerpt redundant.

But the right sequencing matters:

- Session 2 (current planned scope): chunk-level bounding box
  highlighting via PyMuPDF.search_for(). Excerpt stays for now.
- Verification: confirm highlighting is reliable across documents,
  chunk types, edge cases (multi-line chunks, chunks at page
  boundaries, chunks with special characters)
- Session 2.5 (small follow-up): remove excerpt if highlighting
  reliable. Keep as fallback if highlighting fails.

The reason for the staging: don't strip the safety net (the visible
excerpt) before we know the new approach works. If session 2
highlighting has edge cases — chunks that PyMuPDF.search_for() can't
locate due to text encoding differences, character substitutions
during PDF text extraction — the excerpt remains the user's only
view of the chunk content. Removing it before verifying is risky.

For the article: design improvements often suggest cascading changes.
The discipline is to land each change individually and verify
before cascading the next. The cost is a few extra commits; the
benefit is bounded rollback if any change surfaces issues.

### What this session closes

- Foundation: PDF rendering working in Streamlit via PyMuPDF
- Architectural decisions all locked deliberately
- One propagation gap fixed (page_num end-to-end)
- One infrastructure gap fixed (volume mount)
- 5/5 verification tests pass
- Off-by-one false alarm noted as verification methodology lesson

### What carries forward

- Session 2: chunk-level bounding box highlighting via
  PyMuPDF.search_for(). Edge cases to watch: multi-line chunks,
  chunks with text-extraction quirks, chunks at page boundaries
- Session 2.5: excerpt removal once highlighting verified reliable
- v2 features (multi-page navigation, zoom, section-level
  highlighting): deferred indefinitely; not scheduled
- Bug C from 1.2g pass 2 (agent over-retrieval) — visible via
  grounding indicator, still untouched

### Closing observation: design discussion compounding

This is the second consecutive session where pre-Cursor design
discussion produced clean closure. 1.2g pass 2's four items closed
in one session because UX decisions were locked before the prompt.
1.2f session 1's architectural foundation closed in one session
because five decisions were locked before the prompt.

The pattern: Cursor is excellent at executing well-specified work.
Cursor's failure mode is making architectural choices silently when
the prompt leaves them open. The fix is doing the architectural
thinking before writing the prompt, then encoding decisions as
constraints.

The cost: 30-45 min of structured discussion before each major
session. The benefit: cleaner closures, no rework, fewer surprises
in verification. Across 1.2g pass 2 and 1.2f session 1, this pattern
saved an estimated 2-4 hours of rework that the prompt-without-
discussion pattern produced in earlier sessions.

For the article: structured pre-Cursor design discussion is the
single most leverage-positive practice from this project. Worth
naming explicitly as a methodology finding, not just an anecdote.

---

## 2026-05-02 (afternoon) — 1.2f session 2: highlighting and multi-page navigation

Session 2 added chunk-level highlighting and multi-page navigation
to the inline source viewer. The highlighting for single-page chunks
landed cleanly. Multi-page handling is shipped imperfect — the
heuristics work for some chunk distributions and fail for others.
Documented as a known limitation rather than reverted.

This is the longest, most iterative single feature in the project.
Worth capturing the iteration arc honestly.

### What landed

**Chunk-level highlighting on the start page.**

PyMuPDF's `add_highlight_annot` draws yellow rectangles around the
chunk text on the rendered page. Cascading fallback handles cases
where the full chunk text doesn't match exactly: try full chunk →
200 chars → 100 chars → 60 chars. First successful match wins.

The fallback returns only the first occurrence on the page (not
all occurrences) to prevent scattered highlights from common
phrases matching multiple times. This was a non-obvious bug —
fix landed as `found if candidate_len == total_len else found[:1]`.

**Multi-page navigation: "Continued on page N+1 →" button.**

When the chunk doesn't fully match on the start page (heuristic:
fewer rectangles than expected for chunk length, or no match at
all on long chunks), a "Continued on page N+1 →" button appears.
Click navigates to page N+1.

**Cap at original_page + 1.**

Forward navigation stops at one page beyond the chunk's start.
Earlier iteration shipped without this cap — users could navigate
indefinitely past where chunks actually ended. The cap keeps
navigation bounded to the practical case (most multi-page chunks
span exactly 2 pages).

**Tail-search on continuation pages.**

When user navigates to N+1, the search uses `chunk_text[len//2:]`
(second half of chunk) instead of the full chunk. This works
when chunks distribute roughly 50/50 across pages. For chunks
that don't (e.g., 80/20 distribution), the tail-search returns
nothing and the continuation page renders without highlights.

**Expander stays open through navigation.**

Streamlit reruns collapse expanders by default. `st.session_state`
tracks expander state explicitly, set True after navigation
buttons trigger reruns. User can navigate without re-clicking
"View source."

### The iteration arc — five passes in one day

1. **Session 2 design discussion (Q4):** Multi-page handling
   chosen as Approach 2 (scrollable). Original kickoff scoped it
   as v1 single-page only. Decision was made with less design
   thinking than warranted.

2. **Session 2 implementation:** Heuristic-based multi-page
   detection. "Continued on" button. Initial verification
   passed; one screenshot showed heavy highlighting (whole page
   yellow) that I flagged as concerning.

3. **Cap fix:** User testing revealed unbounded forward navigation
   (clicking "Continued on" on N+1 navigated to N+2 etc.). Cap
   added at `original_page + 1`.

4. **Issue surfacing:** Real-world testing surfaced four issues:
   over-highlighting on start page, no highlights on N+1, expander
   collapse, section title scatter.

5. **Fix-arc Cursor session:** Audit-first approach identified
   actual root causes (different from my assumed causes for issues
   1 and 4). Each issue addressed at the right layer. Verified on
   one chunk; passed all five test cases.

6. **Spot-check verification:** User tested a different chunk than
   Cursor verified. Found "title/header highlighted on a page,
   nothing highlighted on continuation page" — same class of
   issues that the fix-arc was supposed to address, recurring on
   different chunks.

### The audit-first finding that paid off

The fix-arc Cursor session almost fixed the wrong thing. My
prompt assumed the cascading fallback was accumulating rectangles
across levels (full chunk → 200 chars → 100 chars → 60 chars all
contributing rectangles).

Cursor's audit corrected this: the cascade had `break` correctly,
each level returned independently. The actual bug was within a
single level: when `search_for(chunk_text[:60])` matched, it
returned ALL occurrences on the page. Common 60-char prefixes can
match 5-10 times on dense policy pages, producing scatter.

Without the audit-first instruction, we'd have "fixed" the cascade
behavior (already correct) and the bug would have persisted.

For the article: assumed root causes don't always survive audit.
Including audit-first in Cursor prompts has caught bugs we'd have
otherwise wasted iteration on.

### The decision to ship imperfect

After the spot-check verification revealed multi-page heuristics
still failing on some chunks, the choice was:

- **Option A:** Accept current state with documented limitations
- **Option B:** Revert multi-page entirely, single-page only
- **Option C:** Stop today, redesign with upstream pipeline changes
  (chunk bbox metadata) tomorrow

I had been leaning toward B (revert) because the iteration pattern
suggested heuristic tuning was hitting fundamental limits. The
search-based approach has lossy boundaries: PDF text extraction
during chunking and PyMuPDF text extraction at search time produce
slightly different strings (whitespace normalization, character
encoding differences, hyphenation handling). No amount of
substring-matching heuristics fully bridges this.

User chose A — ship imperfect, document limitations, move on.

This is a legitimate engineering trade-off. "Better than nothing,
document where it fails, defer proper fix to future session" is a
defensible call when:
- The single-page case works reliably (verified)
- The multi-page failures degrade gracefully (no highlight is
  better than wrong highlight, except when section headers get
  highlighted instead of body text)
- Continued iteration has diminishing returns
- A proper fix requires architectural changes (upstream chunk
  bbox metadata) that warrant their own session

For the article: "we don't compromise on quality" doesn't mean
"never ship anything imperfect." It means "make the trade-offs
deliberately, document them honestly, and don't pretend the
imperfect state is the perfect state." This shipped imperfect.
The lessons log says so plainly.

### What's documented as limitation, not feature

**Multi-page chunks where the start-page text matches a section
header earlier on the page get header-only highlights.** The
`found[:1]` fix takes the first occurrence on the page, which
might be a section header instead of the chunk's body content.
Affects a subset of chunks, not all.

**Multi-page chunks with non-50/50 distribution across pages
have empty continuation pages.** The tail-search heuristic uses
`chunk_text[len//2:]`. Chunks distributed 80/20 or 70/30 have
their "second half" still mostly on page N, so search on N+1
finds nothing.

**Chunks spanning 3+ pages can't be fully viewed.** Cap at
`original_page + 1` was added to prevent unbounded forward
navigation. Side effect: chunks that legitimately span 3+ pages
are only partially viewable.

### Why the proper fix is upstream

The search-based approach is fundamentally fragile because we're
searching for chunk text in a PDF whose text extraction is lossy
relative to the chunking pipeline's extraction. The fix would be
to store chunk bounding box coordinates during chunking — `bbox:
{x0, y0, x1, y1, page_num}` per chunk per page span — and use
those coordinates directly for highlighting. No search needed.

This requires:
- Modifying the chunking pipeline (likely
  `pipelines/embed_policy_docs.py`) to capture bbox per chunk
- Changing PolicyCitation schema to expose bbox
- Re-embedding all chunks (or migrating Qdrant payloads)
- Updating pdf_renderer.py to draw rectangles from bbox coords
  instead of search_for results

That's a chunking pipeline change, not a UI fix. Future session
work, not session 2 fix-arc work.

### What pass 2.5 should consider

The original session 2.5 plan was excerpt removal — strip the
chunk text excerpt above the relevance metric since the highlighted
PDF rendering shows the same content in context.

Given the multi-page limitations now documented, the excerpt
remains useful as a fallback when highlighting fails. For chunks
where:
- The start-page highlight is just a section header
- The continuation page is empty

The excerpt is the user's only access to the chunk content.
Removing it would compound the user-facing inconsistency.

Recommendation: skip session 2.5 entirely. Keep the excerpt. The
"redundancy" framing was correct for cleanly-highlighted single-
page chunks but wrong for the multi-page limitation cases.

### Closing observation: scope expansion vs scope discipline

The original kickoff Decision 4 said "v1 only — single-page rendering,
no multi-page navigation, no zoom." Session 2's design discussion
expanded that to multi-page (Approach 2 in Q4). The expanded scope
hit real limits that heuristic tuning couldn't fully resolve.

Pattern: scope expansion during design discussion is harder to
resist than scope expansion during implementation. We caught
implementation-time scope creep (the article framing of design
discussion compounding). We didn't catch design-time scope
expansion.

For future sessions: when a kickoff decision says "v1 only — X"
and the design discussion question for that scope reaches "should
we expand to Y?", that's the moment to push back hardest. The
"v1 only" phrasing existed for a reason. Expanding it during
design discussion is exactly the move that produces feature creep
into multi-iteration messes.

Today's lesson: kickoff Decision 4 was right. Q4 in session 2
design discussion should have been "page-level v1 stays, no
expansion options." We chose differently and the iteration cost
followed.

For the article: design discussion is where decisions get made
deliberately, but it's also where scope can expand without notice.
The discipline is asking "does this expand a previously-locked
decision?" before adopting an expanded scope.

---

## 2026-05-02 (later afternoon) — 1.2f session 2.5: whole-document link + excerpt removal

Session 2.5 reverses a decision made in session 2's lessons log
("skip session 2.5") and ships two coupled changes: a whole-
document link on each citation, and removal of the chunk text
excerpt above the relevance metric.

### The reversal

Session 2's lessons log said explicitly:

> "Skip session 2.5 (excerpt removal): originally planned to remove
> chunk excerpt above relevance metric since highlighting shows
> same content. Multi-page limitations make excerpt useful as
> fallback when highlighting fails. Removing it would compound
> user-facing inconsistency."

That reasoning held under the assumption that the user had no
other way to access full document content when highlighting failed.

The reversal: adding a "Open full document in new tab" link
(clickable document name + caption inside expander) provides a
stronger fallback than the truncated excerpt. Full document access
beats 500-char excerpt for the cases where:
- Start-page highlight is just a section header
- Continuation page is empty
- Search-based highlighting fails entirely

With the whole-document link, the excerpt becomes redundant noise.
Removing it cleans up the citation card visually and gives users a
single clear path to source verification.

### The architectural decisions

Two design choices made before the Cursor prompt:

1. **Link placement: BOTH header + expander caption.** Two
   placements for the same link — clickable document name in the
   citation header, plus a caption-style "📄 Open full document in
   new tab" inside the expander. Redundancy is intentional: users
   on different mental models find the link in different places.

2. **Excerpt removal: full removal, not conditional.** Considered
   "show excerpt only when highlighting fails" but rejected as too
   clever. Conditional UI logic creates inconsistency users can't
   predict. All-or-nothing removal is cleaner.

### Implementation notes

Streamlit version 1.57 supports `enableStaticServing = true` in
`.streamlit/config.toml`. Cursor's audit picked this approach over
base64 encoding. Cleaner serving, no encoding overhead, no
JavaScript dependency.

Single Docker volume mount addition: `data/raw/policy_docs` mounted
to `/app/static/policy_docs` (same source as the existing PDF
viewer mount, different destination to surface via Streamlit
static serving). Files served read-only.

Link URL pattern: `/app/static/policy_docs/{filename}.pdf`. Clean
relative paths, no auth complications, browser opens PDFs natively
in new tabs.

### What this closes

- 5/5 PDFs accessible via static serving (verified HTTP 200)
- 6 link instances per multi-citation answer (3 citations × 2 placements)
- Excerpt fully removed from citation cards
- Multi-page navigation unaffected (regression check passed)
- Visual layout balanced without excerpt block

### The honest observation about residual limitations

User testing the "annual meal cap" question revealed an example of
the documented session 2 limitation in practice: the answer
references "$500 in any rolling 12-month period" but the chunk's
PDF page renders with highlights elsewhere on the page, not on the
$500 sentence.

The chunk metadata for DOC 002 chunk 0000 likely contains the $500
content somewhere mid-chunk. PyMuPDF's `search_for()` matches
either the chunk's first 60-char prefix elsewhere on the page (a
section header or earlier content) or fails entirely. The
`found[:1]` fix from session 2 returns the first match, which
isn't the chunk's actual location.

This is the "header-only highlights" failure mode session 2's
lessons log already documents. The whole-document link is exactly
the right fallback for this case: user sees the answer references
$500, sees the highlighted text on the rendered page doesn't match,
clicks the document name, finds the actual content in the full PDF.

The fallback path makes the limitation tolerable. Without the
link, users would face confusing disconnect between the answer
and the highlighted text. With the link, users have a clear path
to ground truth.

For the article: shipping imperfect-but-honest works when the
imperfection has a graceful fallback. The fallback turns a
"feature is broken" experience into a "feature has a known limit
and here's what you can do about it" experience. Substantively
different user-facing outcome.

### Closing observation: reversing previous decisions

Session 2's lessons log explicitly recommended skipping session
2.5. Session 2.5's lessons log explicitly reverses that
recommendation. Both decisions are visible in the historical
record — neither overwritten, both reasoned about.

This is the right pattern for incremental engineering. Decisions
made under one set of assumptions can be reversed when assumptions
change. The discipline is making the reversal explicit, not silent
— so future-you reading the log understands both the original
reasoning and what changed.

For the article: project documentation should preserve decision
reversals as first-class entries, not retroactively rewrite the
record. "We decided X for reason Y. Then we decided ¬X for reason
Z" is honest engineering history. "We always decided ¬X" is
revisionist and useless.

---

## 2026-05-02 (evening) — 1.2f session 2.5: whole-document link, excerpt removal, and the two-stage highlighting that demonstrated heuristic limits

Session 2.5 was supposed to be a small follow-up — remove the chunk
excerpt that became redundant with highlighting, add a "open full
document" link as a fallback when highlighting fails. It expanded
to include a complete rewrite of the highlighting algorithm. Then
the rewrite produced its own failure modes. We ended the session
with three commits worth of real improvements and a clear architectural
direction for tomorrow's work.

This entry covers three distinct pieces shipped together:

1. Static PDF serving + whole-document link (clean wins)
2. Excerpt removal (reverses session 2's "skip 2.5" decision)
3. Two-stage highlighting (normalize + cluster, replacing cascade)

And one finding bigger than any of the three pieces: heuristic-based
PDF text matching has fundamental limits we've now demonstrated
empirically across six iterations.

### Reversing session 2's "skip 2.5" decision

Session 2's lessons log entry (committed earlier today) explicitly
said:

> "Skip session 2.5 (excerpt removal): originally planned to remove
> chunk excerpt above relevance metric since highlighting shows same
> content. Multi-page limitations make excerpt useful as fallback
> when highlighting fails. Removing it would compound user-facing
> inconsistency."

That reasoning held assuming no other fallback existed. The whole-
document link added in session 2.5 is a stronger fallback than the
excerpt — users can see the full document, not just a truncated
text snippet. With the link in place, the excerpt becomes redundant
even for the multi-page failure cases.

This is a real reversal, not a flip-flop. The original decision was
right given the alternatives at the time. Adding a better fallback
changed the alternatives.

For the article: design decisions are conditional on the option
space available. When the option space changes, decisions can
legitimately reverse. Documenting the reversal explicitly (this
entry doing that) is more honest than pretending the original
decision was wrong.

### What landed cleanly: static serving + whole-document link

PyMuPDF rendering for the inline source viewer was already working
(session 1). The viewer shows a single page at a time with chunk
highlighting. The whole-document link was added as a complementary
fallback — when highlighting fails or chunk extends beyond what's
visible, users can open the full PDF in a new tab.

Implementation:
- Streamlit 1.57's `enableStaticServing = true` config option
- Volume mount: `data/raw/policy_docs:/app/static/policy_docs:ro`
  (read-only, mirrors the read-only mount from session 1)
- Two link placements: clickable document name in citation header,
  caption-style link inside expander
- Both links use `target="_blank"` to open in new tab

Verification confirmed:
- All 5 source PDFs accessible (HTTP 200, content-type:
  application/pdf)
- Links work from both placements
- Multi-page navigation unaffected
- No regression in the existing chunk highlighting (when it works)

This piece is unambiguously a win.

### Excerpt removal

The chunk text excerpt above the relevance metric was originally
useful when no PDF rendering existed. Session 1 added inline PDF
rendering. Session 2 added chunk highlighting. The excerpt became
redundant for cleanly-highlighted chunks but was kept because of
multi-page failure cases.

With the whole-document link providing a stronger fallback, the
excerpt is now redundant in all cases:
- Cleanly highlighted chunks: user sees the chunk in PDF context
- Multi-page chunks with imperfect highlighting: user can open
  full document for context
- Failed highlighting: user can open full document for context

Removed the excerpt rendering at line 288. Citation card now shows:
- Document name (clickable link to PDF)
- Relevance score
- Weak match indicator (if applicable)
- Expander with rendered page + caption + "Open full document" link

Card is visually cleaner without the excerpt block.

### The two-stage highlighting attempt

This is the iteration that didn't fully succeed.

Session 2 shipped a cascading fallback (full chunk → 200 → 100 → 60
chars). Real-world testing surfaced two failure modes:
- Section headers highlighted instead of chunk body (60-char prefix
  matched section text earlier on the page)
- Multi-page continuation pages showing no highlights (full chunk
  text not on continuation page)

Session 2.5 attempted to replace the cascade with a more robust
two-stage approach:

**Stage 1 — Whitespace normalization.** Most chunk-PDF mismatches
are whitespace differences (multi-space, line breaks, normalization
inconsistencies). Normalize both before searching, try full chunk
match.

**Stage 2 — Multi-substring clustering.** If stage 1 fails, search
for 4 non-overlapping substrings from the chunk (positions 0%, 30%,
60%, 90%). Find the cluster of rectangles that's vertically close
together — that's the chunk's actual location. Spurious matches
are scattered and ignored.

If both stages fail: return no rectangles. Better to have no
highlight than a wrong highlight.

Implementation completed. Cursor's verification at the function
level passed all 5 test cases (TC-a through TC-e), with a separate
synthesized test case confirming clustering path works when stage
1 fails.

### What spot-check verification surfaced

Testing on different chunks than Cursor's test cases revealed three
new failure modes:

**Failure 1: $75,000 annual cap not highlighted at all.** Stage 1
failed, stage 2 clustering also failed. The chunk is clearly on the
page. Neither stage could locate it.

**Failure 2: Citation with no highlights on either start or
continuation page.** Both stages failed on both pages. The chunk
is in the document but unfindable by the new approach.

**Failure 3: Random text highlighted on another citation.** Stage 2
clustering picked a wrong cluster. Spurious matches happened to
land vertically close and got identified as the chunk's location.

The two-stage approach traded one set of failures for another:
- Old approach: over-highlighting and section header matches (wrong
  location, but always something)
- New approach: silent no-highlight on some chunks, plus occasional
  wrong cluster (different mechanism, same class of error)

For some users this is better (silent failure is more honest). For
some it's worse (no highlight on $75K when it's right there is
frustrating).

### Six iterations: pattern recognition

Counting iterations on multi-page handling today:

1. Kickoff Q4 — chose Approach 2 (scrollable) over Approach 1
   (page-level). Should have stuck with the original v1 single-page
   scope from kickoff Decision 4.

2. Session 2 implementation — heuristic-based multi-page detection,
   "Continued on page" button, cascading fallback. Initial
   verification passed.

3. Cap fix — bounded forward navigation at original_page + 1 after
   discovering unbounded navigation issue.

4. Session 2 fix-arc — addressed four issues (over-highlighting,
   no continuation highlights, expander collapse, section title
   matching). Audit-first found the actual root cause was different
   from my assumed root cause.

5. Spot-check verification — surfaced remaining failure modes.

6. Two-stage rewrite — replaced cascade with normalize + cluster.
   New failure modes emerged.

Each iteration had a clear theory and a clean implementation. Each
hit walls. The walls aren't bugs — they're the consequence of trying
to bridge a gap that's fundamentally lossy: chunk text in Qdrant
(extracted by chunking pipeline, normalized) and PDF text at search
time (extracted by PyMuPDF, with original whitespace and encoding).
These extractions differ in ways no heuristic can fully predict.

For the article: this is a sharper version of the existing finding
that prompt-layer fixes have ceilings. Search-based PDF highlighting
has its own ceiling, and it's structural — no amount of clever
substring strategies bridges the chunk-to-PDF text gap.

### The architectural fix that's been clear since session 2

The proper solution: store chunk bbox coordinates during chunking,
use them directly for highlighting. No search at run-time. This
eliminates the gap entirely — we know exactly where each chunk is
because we recorded the location when we extracted the chunk.

Required changes:
- `pipelines/embed_policy_docs.py` — capture bbox per chunk per
  page span during PDF parsing using PyMuPDF's `page.get_textpage()`
  with coordinates
- Qdrant payload schema — add bbox field (list of rectangles, since
  chunks can span multiple pages)
- Re-embed all chunks across all 5 documents
- `agents/schemas.py` — add bbox to PolicyCitation
- `agents/policy_agent.py:_parse_citations` — propagate bbox (we
  know how to do this from session 1's page_num propagation gap)
- `streamlit_app/utils/pdf_renderer.py` — significantly simpler, no
  search needed, draw rectangles directly from bbox coordinates
- Tail-search logic in 5_Policy_QA.py — eliminated entirely

This is several hours of work across multiple components. It's the
right architecture. It eliminates all six failure modes from today's
iterations, not by adding more heuristics, but by removing the need
for heuristics.

Scheduled for tomorrow's session.

### What ships tonight

Three commits:

1. Static PDF serving infrastructure
2. Whole-document link + excerpt removal + two-stage highlighting
3. Lessons log

The two-stage highlighting ships imperfect, with documented
limitations. Tomorrow's bbox fix will replace it entirely. Tonight's
state is genuinely better than session 2's state in some ways
(cleaner failure mode — no highlight when no good match) and worse
in others (some chunks lose highlights they used to have, even if
those highlights were sometimes wrong).

The honest framing: tonight's commit captures the iteration journey.
Tomorrow's bbox work captures the architectural answer. Both are
visible in git history.

### Why we didn't implement the bbox fix today

Two reasons:

1. **Time of day / energy.** Today has had substantial iteration
   on this feature. The bbox fix touches multiple components
   (chunking pipeline, schema, agent code, renderer) and warrants
   fresh attention to avoid the kind of subtle bug that surfaces
   during verification.

2. **Design discussion deserves separate session.** Session 1's
   kickoff design discussion (~30 min before code) paid off
   substantially. The bbox fix has multiple architectural decisions
   (single bbox vs list, schema field design, re-embed strategy,
   migration vs full re-embed) that benefit from being decided
   deliberately before code.

Tomorrow morning: design discussion first, then Cursor prompt with
decisions locked.

### Closing observation: knowing when to stop iterating

I had to push back several times today on continuing to iterate on
the heuristic approach. Each time, the user proposed a reasonable-
sounding next iteration ("try multi-substring clustering," "search
for keywords + chunk text"). Each one would have been the seventh
or eighth attempt to make the heuristic work.

The pattern: each iteration produces a clean implementation that
verifies on its test cases, then surfaces new failure modes when
spot-checked on different chunks. After 4-5 iterations of this,
the question is no longer "what's the next heuristic to try?" but
"is the heuristic class of solutions the right approach at all?"

The answer was no. Heuristic-based search couldn't fully bridge the
chunk-PDF extraction gap. The architectural fix (bbox metadata
captured at chunking time) eliminates the gap.

For the article: knowing when to stop iterating is a skill. The
signal isn't "this iteration didn't work." It's "we've now had
multiple iterations with the same structural failure mode, and
each iteration's failure mode is a re-statement of the underlying
architectural mismatch."

The user made the right call to schedule the bbox fix for tomorrow
rather than push through tonight. Fresh head, proper design
discussion, then the right architecture rather than the wrong
architecture iterated harder.

---

## Note to future self

Don't rewrite this when drafting the article. Lift specific anecdotes,
reframe in article voice, but keep this raw. The honesty in raw notes
beats the polish of post-hoc reconstruction every time. If something
in here reads as embarrassing or rough — keep it that way; it's what
makes the article real.
---

## 1.2f Session 3 — bbox pipeline closure

The session that was supposed to be straightforward verification turned
into a diagnosis exercise that reframed what the feature actually does.

### What was planned

The May 2 closing entry scheduled today as: design discussion → Cursor
prompt with decisions locked → bbox metadata extraction in
`pipelines/embed_policy_docs.py` → schema propagation through
`PolicyCitation` → re-embed → renderer simplification. Then five visual
verification tests, three commits, push, 1.2f closes.

Phase 1 (chunking pipeline + Qdrant re-embed) and Phase 2 (schema
propagation + renderer) both completed against the plan. API verification
confirmed bboxes propagating end-to-end.

Then the visual verification surfaced something the plan didn't
anticipate.

### The wall-to-wall highlighting problem

First test (the $75K annual cap question): the highlight covered nearly
the entire PDF page. Section headers, body paragraphs, table rows,
footer text, even the red "SYNTHETIC DOCUMENT" warning at the top —
all yellow.

The instinct was to assume a bug. Two natural hypotheses:

**Hypothesis A — renderer bug.** The renderer is drawing all bboxes on
the page, not just the chunk's bboxes. Iterating `page.get_text("dict")`
at render time instead of using stored chunk bboxes would produce
exactly this.

**Hypothesis B — data bug.** Phase 1 incorrectly attached page-level
bbox unions to chunks instead of just the chunk's own text spans. Bad
data at rest, faithfully rendered.

The two hypotheses needed very different fixes — one cheap, one
expensive. The diagnostic that distinguished them: dump one chunk's
stored bboxes from Qdrant and look at the count and the union coverage.

### What the diagnostic showed

`scripts/diagnose_chunk_bboxes.py` (added today) dumped three chunks:

| chunk | chars | pages | union coverage |
|---|---|---|---|
| DOC_002_chunk_0001 | 3,214 | p2–p3 | 42.7% |
| DOC_004_chunk_0000 | 3,452 | p1–p2 | 46.4% |
| DOC_003_chunk_0007 | 3,490 | p4 only | 48.4% |

Coverage at 34–58% — not the >80% we'd expect if either hypothesis
were correct. The bbox pipeline is right. The renderer is right. The
chunks are 512 words and a 512-word chunk genuinely occupies that much
of a typical PDF page.

The wall-to-wall appearance is real, but accurate. Highlight covers ~50
line bboxes because the chunk genuinely spans ~50 lines.

### Why the previous renderer "looked better"

This was the moment that made the May 2 entry retroactively interesting.

Session 2.5's two-stage approach used `page.search_for(full_chunk_text)`
as stage 1 and substring clustering as stage 2. For 3000+ char chunks,
stage 1 almost always failed (chunk text and PDF text differ in ways no
search bridges — exactly what May 2 documented). It silently fell back
to stage 2, which sampled four substrings and highlighted only the
vertical region where 2+ of them clustered — typically ~25% of the
chunk.

The old renderer looked tighter because it was showing **less** than
what was retrieved. The new bbox pipeline is more accurate. The chunk
size makes that accuracy visually unflattering.

This generalises: **an evaluation harness can mistake a broken renderer
for a working one if the broken renderer happens to produce visually
plausible output.** Eyeball checks would have continued passing the old
approach indefinitely. The diagnostic script — comparing what's stored
to what's rendered — caught this in one run. For the article: visual
plausibility is not the same as visual correctness, and on RAG citation
systems specifically, the failure mode is "looks tight, shows wrong
thing" rather than "looks broken."

### The two-column artifact (OIG CPG)

DOC_003_chunk_0007 had bboxes [000]–[033] in the right column
(x0≈222) and [034]–[068] in the left column (x0≈45), with y0 running
from 60 to 741 — full page height. PyMuPDF's `get_text("words")`
returns words right-column-first for the OIG Federal Register PDF,
which means a 512-word chunk walks down the right column and wraps
into the left, producing bboxes that span the full page vertically.

Today this is a curiosity. For 1.2g it's a real risk: any sentence-level
highlighting that relies on word reading order needs the chunking-time
order and the highlight-time order to match exactly. A different
PyMuPDF version, a different extraction call, or any reading-order
heuristic in between will silently misalign.

### The decision space

Three options surfaced once root cause was clear:

1. **Re-chunk to 128–200 words.** Tighter highlights, more chunks,
   higher embedding cost, requires Phase 1 re-embed. But: small chunks
   tend to *hurt* citation quality by fragmenting context, which
   contradicts the existing "policy citation quality improvements"
   backlog item.

2. **Sentence-level highlighting within retrieved chunks.** Keep 512-word
   chunks for retrieval, but at highlight time score sentences against
   the query and render only the top 1–2 sentences' bboxes. The full
   chunk still goes to the LLM as context — only the highlight target
   changes.

3. **Accept current behaviour with a caption.** Add a small note to the
   renderer clarifying that the highlight shows the full retrieved chunk.
   Honest about what the system is doing.

The temptation was option 3 only. It closes 1.2f cleanly. But shipping
a citation viewer that highlights a 500-word region while users
intuitively expect sentence-level provenance is the same class of error
as session 2.5's "looks tight, shows wrong thing." The cosmetic fix is
necessary (option 3), but it's not sufficient on its own.

The right shape is: ship option 3 *now* to close 1.2f honestly, and
open 1.2g for option 2. Don't pretend option 3 is enough.

### Why 1.2g is its own scope, not 1.2f scope-creep

Sentence-level highlighting requires reconstructing word-to-bbox
alignment at highlight time. The Phase 1 pipeline stores merged
line-level bboxes (`{x0, y0, x1, y1, page_num}`) — no per-word
character offsets, no word index. The chunk payload has
`chunk_start_offset` and `chunk_end_offset` at the chunk level but
nothing per-word.

Reconstruction approach: open the source PDF page with PyMuPDF, call
`get_text("words")`, find where the chunk text starts in the page
text, walk both lists in parallel to get a word-bbox alignment, split
the chunk into sentences, score sentences against the query, collect
the relevant words' bboxes, merge to line level, render.

Estimated 6–8 hours optimistically. The two-column reading order risk
above means surprises are likely. Sentence splitting on compliance
text (numbered references, parenthetical citations like "(42 U.S.C.
1320a-7h)", bulleted lists with no terminal periods) needs care.

Trying to cram this into 1.2f to "really finish it" would have been
the pattern May 2's closing observation warned against — pushing
through when the right move is to let the next session pick it up
clean.

### What ships tonight

Three commits plus the pipeline change that should have been part of
Phase 1's commit but was uncommitted on the local branch:

1. `feat(policy-qa): propagate bbox metadata through agent stack and renderer`
2. `chore(policy-qa): add bbox diagnostic script and renderer caption`
3. `docs(policy_ragas): 1.2f closure - bbox pipeline verified, chunk-size finding, 1.2g scoped` (this entry)
4. `feat(policy-pipeline): extract and store line-level bboxes during embedding`

The pipeline commit is fourth in branch order but represents Phase 1
work. Squash merge to main collapses the ordering anyway. Worth
flagging that the embed pipeline now unconditionally drops and
recreates the Qdrant collection — the prior interactive `y/N`
overwrite guard was removed because the payload schema changed
(bboxes field added) and merging new-schema points alongside old-schema
ones isn't clean. Adding the guard back as an opt-out flag is a
candidate small follow-up.

### Findings to add to the running list

- **Visual plausibility is not visual correctness.** A renderer that
  produces tight, plausible-looking highlights can be silently showing
  a fraction of what was retrieved. The diagnostic that catches this
  is "compare what's stored to what's rendered" — not "does the output
  look reasonable."

- **When all hypotheses point to a bug, consider that the system might
  be working correctly and the assumption is wrong.** Coverage at 42%
  isn't a bug; it's what 512-word chunks look like on a PDF page. The
  bug-hunting frame almost cost an unnecessary re-embed.

- **Chunk size is a UX parameter, not just a retrieval parameter.**
  512 words is a defensible retrieval choice and a poor citation-display
  choice. The two design pressures point in different directions and
  the resolution isn't to pick one — it's to decouple them (retrieve at
  one granularity, display at another).

- **Storage decisions made early are expensive to undo.** Phase 1
  stored line-level bboxes without per-word character offsets. That was
  a defensible choice for a line-level renderer. It's now the constraint
  that makes 1.2g a 6–8 hour task instead of a 30-minute one. For any
  future RAG pipeline with PDF citation: preserve word-level bbox
  granularity in storage even if the default renderer only draws
  line-level. The marginal storage cost is small; reconstruction later
  is significant.

- **Diagnostic scripts are first-class artifacts.** `scripts/diagnose_chunk_bboxes.py`
  resolved in one run what would have been hours of speculation. Worth
  building the diagnostic before the visual verification, not after,
  whenever the pipeline involves stored intermediate state.

### 1.2g scope (next, deferred until after Phase 4)

Reconstruct word-to-bbox alignment at highlight time using
`PyMuPDF.page.get_text("words")`. Embed-rank sentences within the
retrieved chunk against the query. Render only the top-sentence bboxes.
Keep the full chunk as LLM context — only the highlight target changes.

Main risks already named: two-column reading order, sentence splitting
on compliance text, word-to-chunk fuzzy alignment for whitespace and
hyphenation. Estimated 6–8 hours. Two-column risk most likely to
expand scope.

### Closing observation: the gap between "verified" and "correct"

End-to-end verification confirmed bboxes flow from chunking pipeline
→ Qdrant → agent → API → renderer. Every stage was correct. The
feature is verified.

The feature is also not what users expected when they clicked "view
source." Both statements are true. The verification we ran answered
"does the wiring work" — it didn't answer "does the output match user
expectation." Those are different questions and they need different
tests.

For 1.2g the verification needs to include a user-expectation check,
not just a wiring check. Something like: given a question with a
known answer sentence, does the highlight land on that sentence?
Boolean pass/fail per question, not "does it look reasonable."

That's the test the next session should design before writing code.

---
