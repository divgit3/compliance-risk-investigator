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

## Note to future self

Don't rewrite this when drafting the article. Lift specific anecdotes,
reframe in article voice, but keep this raw. The honesty in raw notes
beats the polish of post-hoc reconstruction every time. If something
in here reads as embarrassing or rough — keep it that way; it's what
makes the article real.
