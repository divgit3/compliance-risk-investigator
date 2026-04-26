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

## Note to future self

Don't rewrite this when drafting the article. Lift specific anecdotes,
reframe in article voice, but keep this raw. The honesty in raw notes
beats the polish of post-hoc reconstruction every time. If something
in here reads as embarrassing or rough — keep it that way; it's what
makes the article real.
