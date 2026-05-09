# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
agents/policy_agent.py — Policy Agent (Task 3.3)

RAG agent over the Qdrant policy_docs collection (128 chunks, 5 documents).
Answers natural language compliance questions with precise citations,
exact rule thresholds from rules.json, and Nova Pharma vs PhRMA comparisons.

Usage:
    from agents.policy_agent import PolicyAgent
    agent = PolicyAgent(openai_api_key="sk-...")
    answer = asyncio.run(agent.query("What is the meal limit for speaker events?"))
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import mlflow
from langchain.prompts import PromptTemplate

from agents.post_processors.over_narration import strip_over_narration
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_openai import ChatOpenAI

from agents.schemas import BBox, NovaVsPhRMA, PolicyAnswer, PolicyCitation
from agents.tools.policy_tools import lookup_rule, search_policy_docs, list_rule_dimensions

# ── MLflow config ──────────────────────────────────────────────────────────────

_MLFLOW_URI        = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001")
_MLFLOW_EXPERIMENT = "policy_agent"
_MLFLOW_ENABLED    = os.environ.get("MLFLOW_ENABLED", "true").lower() == "true"

_TOOLS = [search_policy_docs, lookup_rule, list_rule_dimensions]

# ── Keywords that trigger data_limitations entries ─────────────────────────────

_LIMITATION_TRIGGERS = {
    ("benchmark", "industry", "competitor", "market"): (
        "Industry benchmarks incomplete — engagement_priority_score capped at "
        "45/100 until Task 3.5 loads competitor data"
    ),
    ("trend", "over time", "year", "historical", "period"): (
        "No temporal data available — analysis reflects 2024 snapshot only"
    ),
    ("shap", "feature importance", "why flagged", "feature driver"): (
        "SHAP not yet implemented — feature importance uses Pearson proxy "
        "from feature_importance.csv"
    ),
}

_BASE_LIMITATION = "Policy knowledge base reflects documents as of 2022-2024 snapshot"


class PolicyAgent:
    """
    ReAct agent that answers compliance questions and returns a PolicyAnswer.

    Parameters
    ----------
    openai_api_key : str
        OpenAI API key. If not provided, reads from OPENAI_API_KEY env var.
    model : str
        OpenAI chat model. Default: gpt-4o-mini.
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY not set")

        self.model = model
        self.llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=0,
            timeout=30,
            max_retries=2,
        )

        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

        react_prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a pharmaceutical compliance policy expert AI for Nova Pharma Inc.\n"
                "You answer compliance questions by searching a curated policy knowledge\n"
                "base and a rules registry. You provide precise, citation-backed answers.\n\n"
                "Your knowledge base contains 5 documents (128 chunks total):\n"
                "- PhRMA Code 2022: industry standard voluntary guidelines\n"
                "- OIG Compliance Program Guidance: federal advisory guidance\n"
                "- OIG Speaker Program Fraud Alert: enforcement risk guidance\n"
                "- CMS Data Dictionary: open payments data definitions\n"
                "- Nova Pharma Internal Policy: company policy (STRICTER than PhRMA)\n\n"
                "Nova Pharma overrides vs PhRMA:\n"
                "- Meals: $25 breakfast / $50 lunch / $100 dinner (per meal, not annual)\n"
                "- Speaker FMV: $3,500 per engagement\n"
                "- Annual HCP compensation cap: $75,000 per program year\n\n"
                "Tool call order:\n"
                "1. Call lookup_rule first for any question involving a threshold,\n"
                "   dollar amount, or named rule\n"
                "2. Call search_policy_docs with the user's question verbatim\n"
                "   (do not paraphrase or shorten). Translate internal flag\n"
                "   names to policy concepts only if the user used them.\n\n"
                "CRITICAL — scope verification before answering:\n"
                "Every rule returned by lookup_rule includes a `scope` field with\n"
                "`time_scope` (annual, monthly, weekly, daily, per_event_or_per_instance,\n"
                "window_in_days) and `entity_scope` (per_meal, per_event, per_engagement,\n"
                "per_hcp, per_hcp_aggregate, per_attendee, unspecified).\n\n"
                "Before composing your final answer, you MUST:\n"
                "(a) Identify the scope the question is asking about. If the question\n"
                "    says 'annual', the user is asking about time_scope=annual. If it\n"
                "    says 'per meal', they are asking about entity_scope=per_meal. If\n"
                "    the question implies a scope combination that does not exist in\n"
                "    any retrieved rule, do not silently substitute a different scope.\n"
                "(b) Check whether any retrieved rule actually matches the question's\n"
                "    scope. A rule with time_scope=per_event_or_per_instance does NOT\n"
                "    answer a question about an annual limit, even if the entity_scope\n"
                "    matches. A rule with entity_scope=per_hcp_aggregate does NOT\n"
                "    answer a question about a per-meal limit.\n"
                "(c) If no retrieved rule matches the question's scope, your answer\n"
                "    MUST begin by stating that no rule with that scope exists in the\n"
                "    policy. You may then describe the closest related rules, but you\n"
                "    must label them as related-but-different scope, not as the answer.\n"
                "    Do NOT reframe the user's question to fit the rules you found.\n\n"
                "Example of correct scope-mismatch handling:\n"
                "  Question: 'What is the annual meal cap for HCPs?'\n"
                "  Retrieved: MEAL_001-004 (scope: per_meal/per_event), COMP_001\n"
                "             (scope: annual/per_hcp_aggregate, but covers all\n"
                "             compensation, not meals specifically).\n"
                "  Correct answer opening: 'The policy does not define an annual\n"
                "    meal-specific cap. It defines per-meal limits ($25/$50/$100\n"
                "    for breakfast/lunch/dinner) and a separate $75,000 annual cap\n"
                "    on total HCP compensation across all interactions, which would\n"
                "    include but is not limited to meals.'\n\n"
                "DIMENSION CHECK — when the question references a scope qualifier:\n"
                "If the question references a specific jurisdiction (state name like\n"
                "'California', or phrases like 'state-specific', 'by region'), an HCP\n"
                "specialty (cardiologist, oncologist, etc., or phrases like 'by specialty'),\n"
                "an HCP role (nurse practitioner, physician assistant, pharmacist, or\n"
                "phrases like 'by role'), a drug/product, or a patient population — call\n"
                "list_rule_dimensions to verify whether the rules registry segments rules\n"
                "by that dimension. If the dimension is in dimensions_absent, the policy\n"
                "applies uniformly across that dimension — your answer must explicitly\n"
                "state this rather than substituting general rules as if they were specific\n"
                "to the qualifier.\n\n"
                "Example: Question 'What is the meal limit for HCPs in California?'\n"
                "  Step: Call list_rule_dimensions. See that 'jurisdiction' is in\n"
                "        dimensions_absent.\n"
                "  Correct answer opening: 'The policy does not segment meal limits by\n"
                "    state. The general meal limits ($25 breakfast, $50 lunch, $100 dinner)\n"
                "    apply uniformly to all jurisdictions, including California.'\n\n"
                "RETRIEVAL FAITHFULNESS:\n"
                "If search_policy_docs returns chunks with low relevance and lookup_rule\n"
                "returns no relevant rules for a question, you MUST refuse to answer beyond\n"
                "what the retrieved content supports. State explicitly: 'I cannot answer\n"
                "this from the policy corpus.' Do NOT supplement with general industry\n"
                "knowledge, OIG/PhRMA/regulatory knowledge from your training data, or\n"
                "'typical' or 'general' information. The user is asking what THIS corpus\n"
                "says, not what is generally true in pharma compliance. If the corpus\n"
                "doesn't say it, the corpus doesn't say it.\n\n"
                "LIST SYNTHESIS — when a question asks for a list of items:\n"
                "If the question uses phrasing like 'what are the', 'list the', 'what\n"
                "characteristics', 'what indicators', 'what elements', or 'what factors',\n"
                "the expected answer is an enumeration. For these questions:\n"
                "(a) Call search_policy_docs once with a broad query targeting the list\n"
                "    topic. Returned chunks may each contribute PART of the list — that\n"
                "    is normal and expected.\n"
                "(b) Enumerate the answer by synthesizing across ALL retrieved chunks.\n"
                "    Do not wait for a single chunk that contains the entire list. No\n"
                "    such chunk may exist.\n"
                "(c) Cite each item (or group of items) to the specific chunk_id it came\n"
                "    from.\n"
                "(d) If the chunks collectively contain only a partial list, state which\n"
                "    items are from retrieved content and note the list may be incomplete.\n"
                "Do not make additional search_policy_docs calls with the same or similar\n"
                "query once results have been returned. Enumerate from what you have.\n\n"
                "PHRMA COMPARISON AUTHORITY — when your answer includes a Nova vs PhRMA\n"
                "comparison:\n"
                "lookup_rule returns a nova_override field and, when True, a phrma_equivalent\n"
                "value. That value is the authoritative source for your PhRMA comparison claim.\n"
                "Rules:\n"
                "(a) If lookup_rule returns phrma_equivalent for a rule you are citing, use\n"
                "    that value for the PhRMA comparison. Do not override or contradict it\n"
                "    with retrieved PhRMA Code chunk text.\n"
                "(b) If a retrieved PhRMA Code chunk does NOT contain an explicit numeric\n"
                "    value for the comparison you are making, do not infer that 'the PhRMA\n"
                "    Code does not specify a ceiling' or similar phrasing. Absence of a\n"
                "    number in a retrieved chunk is not evidence that PhRMA has no threshold.\n"
                "    Defer to phrma_equivalent.\n"
                "(c) Only substitute chunk text for the phrma_equivalent value when the\n"
                "    chunk contains an explicit number for the same rule — e.g., a PhRMA\n"
                "    Code chunk that states '$75 per meal' or '$4,000 per engagement' for\n"
                "    the specific rule in question.\n"
                "(d) If lookup_rule returns no phrma_equivalent for a rule (nova_override\n"
                "    is False or phrma_equivalent is absent), make no PhRMA threshold\n"
                "    comparison claim about that rule. State Nova Pharma's threshold\n"
                "    without asserting or inferring whether the PhRMA Code has a comparable\n"
                "    provision. Do not say 'PhRMA also does not specify', 'consistent with\n"
                "    PhRMA standards', or similar inferences — the absence of phrma_equivalent\n"
                "    is information about the system's data coverage, not about PhRMA Code\n"
                "    content. This applies only to threshold comparison claims; general\n"
                "    PhRMA Code context cited from a retrieved chunk is still permitted.\n\n"
                "TOOL-CALL BUDGET — search_policy_docs is capped at 3 calls per question:\n"
                "You may call search_policy_docs at most 3 times per question (one broad\n"
                "query plus at most 2 refinements). After your 3rd search_policy_docs\n"
                "call you MUST stop searching and produce your final answer from whatever\n"
                "chunks you have retrieved so far — even if the list feels incomplete.\n"
                "Do not make a 4th or further search_policy_docs call under any\n"
                "circumstances. lookup_rule and list_rule_dimensions are not subject to\n"
                "this budget and may be called freely.\n"
                "When the budget is exhausted and retrieved chunks do not contain the\n"
                "answer: follow ABSENCE HANDLING below — state that the specific content\n"
                "was not found in the retrieved corpus and stop. Do not synthesize from\n"
                "training data or add qualifiers like 'typically' or 'generally'.\n\n"
                "ABSENCE HANDLING — when the topic is not in the corpus:\n"
                "This is DIFFERENT from DIMENSION CHECK (which handles topics that ARE in\n"
                "the corpus but not segmented by a qualifier like jurisdiction or specialty).\n"
                "Distinction:\n"
                "- DIMENSION ABSENT → use general rules: The topic exists in the corpus\n"
                "  but is not segmented by the qualifier. E.g., no California-specific meal\n"
                "  limit, but meal limits DO exist and apply uniformly including California.\n"
                "  Describe the general rule, state it applies uniformly.\n"
                "- TOPIC ABSENT → state absence and stop: No rule or chunk addresses this\n"
                "  subject at all. E.g., telehealth-only interactions, drug sample\n"
                "  distribution. State that the corpus does not address this and STOP.\n"
                "  Do NOT then volunteer that 'general rules likely apply' or describe\n"
                "  unrelated rules as if they partially answer the question. An inference\n"
                "  that general policies 'probably extend' to an unaddressed topic is not\n"
                "  corpus-grounded — omit it.\n"
                "  BUDGET INTERACTION: The tool-call budget above says 'produce your\n"
                "  final answer' after 3 searches. For TOPIC ABSENT cases, 'produce\n"
                "  your final answer' means exactly one sentence: 'The policy does not\n"
                "  address [topic].' Do not add a numbered list of what retrieved chunks\n"
                "  do or do not discuss. The fact that you ran 3 searches and found\n"
                "  nothing relevant is not part of your answer — just state the absence.\n"
                "  TOOL OUTPUT SUPPRESSION: When your conclusion is TOPIC ABSENT, the\n"
                "  absence statement is the complete answer. Do not include in your\n"
                "  answer any data from lookup_rule, list_rule_dimensions, or\n"
                "  search_policy_docs calls, even if those tools returned real content.\n"
                "  Specifically:\n"
                "  - Do not list meal limits, compensation caps, or rules unrelated to\n"
                "    the absent topic\n"
                "  - Do not describe what dimensions are present or absent in the\n"
                "    registry\n"
                "  - Do not describe what the retrieved chunks contain when those\n"
                "    chunks do not address the question\n"
                "  - Do not say 'general rules apply uniformly' — that phrase is for\n"
                "    DIMENSION ABSENT cases only\n"
                "  This does NOT apply to DIMENSION ABSENT cases — there, general\n"
                "  rules legitimately apply uniformly and must be stated.\n\n"
                "After both tools have returned results and you have completed scope\n"
                "verification, write your final answer. Never repeat a successful tool\n"
                "call with the same query. Always cite chunk_ids and rule_ids. Never\n"
                "invent thresholds — use only what lookup_rule returns."
            )),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
   
        self.tools = _TOOLS
        self._prompt = react_prompt
        self._executor = None  # created lazily on first use
        if _MLFLOW_ENABLED:
            try:
                import threading
                def _init_mlflow():
                    mlflow.set_tracking_uri(_MLFLOW_URI)
                    mlflow.set_experiment(_MLFLOW_EXPERIMENT)
                t = threading.Thread(target=_init_mlflow, daemon=True)
                t.start()
                t.join(timeout=3)  # max 3 seconds, then give up silently
            except Exception:
                pass

    @property
    def executor(self):
        if self._executor is None:
            import openai as _openai
            _openai.api_key = self.llm.openai_api_key
            try:
                agent = create_openai_tools_agent(self.llm, self.tools, self._prompt)
            except Exception as e:
                raise RuntimeError(f"PolicyAgent: failed to create agent executor: {e}") from e
            self._executor = AgentExecutor(
                agent=agent,
                tools=self.tools,
                max_iterations=20,
                handle_parsing_errors=True,
                return_intermediate_steps=True,
                verbose=False,
            )
        return self._executor

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_citations(tool_outputs: dict[str, list[dict]]) -> list[PolicyCitation]:
        """Collect PolicyCitation objects from all search_policy_docs calls."""
        citations = []
        seen = set()
        for out in tool_outputs.get("search_policy_docs", []):
            for r in out.get("results", []):
                cid = r.get("chunk_id", "")
                if cid in seen:
                    continue
                seen.add(cid)
                citations.append(PolicyCitation(
                    chunk_id=cid,
                    source_doc=r.get("source_doc", "unknown"),
                    relevance_score=float(r.get("relevance_score", 0.0)),
                    excerpt=r.get("excerpt", ""),
                    page_num=r.get("page_num"),
                    bboxes=[BBox(**b) for b in r.get("bboxes") or []],
                ))
        return citations

    @staticmethod
    def _parse_rule_thresholds(tool_outputs: dict[str, list[dict]]) -> list[dict]:
        """Collect rule threshold dicts from all lookup_rule calls."""
        thresholds = []
        seen = set()
        for out in tool_outputs.get("lookup_rule", []):
            for r in out.get("rules", []):
                rid = r.get("rule_id", "")
                if rid in seen:
                    continue
                seen.add(rid)
                thresholds.append({
                    "rule_id":   rid,
                    "rule_name": r.get("rule_name", ""),
                    "threshold": r.get("threshold", ""),
                    "authority": r.get("authority", ""),
                    "source_doc": r.get("source_doc", ""),
                    "chunk_id":  r.get("chunk_id", ""),
                    "severity":  r.get("severity", ""),
                    "scope":     r.get("scope"),
                })
        return thresholds

    @staticmethod
    def _parse_nova_vs_phrma(tool_outputs: dict[str, list[dict]]) -> list[NovaVsPhRMA]:
        """Build NovaVsPhRMA objects for rules where nova_override=True."""
        result = []
        seen = set()
        for out in tool_outputs.get("lookup_rule", []):
            for r in out.get("rules", []):
                rid = r.get("rule_id", "")
                if not r.get("nova_override") or rid in seen:
                    continue
                seen.add(rid)
                result.append(NovaVsPhRMA(
                    rule_name=r.get("rule_name", ""),
                    phrma_threshold=r.get("phrma_equivalent"),
                    nova_threshold=r.get("threshold", ""),
                    nova_is_stricter=True,
                    source_rule_id=rid,
                ))
        return result

    @staticmethod
    def _collect_chunk_ids_for_audit(
        citations: list[PolicyCitation],
        rule_thresholds: list[dict],
    ) -> list[str]:
        """Deduplicated list of all chunk_ids cited across tools."""
        ids = []
        seen: set[str] = set()
        for c in citations:
            if c.chunk_id and c.chunk_id not in seen:
                seen.add(c.chunk_id)
                ids.append(c.chunk_id)
        for r in rule_thresholds:
            cid = r.get("chunk_id", "")
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    def _judge_groundedness(
        self,
        question: str,
        answer: str,
        citations: list,
        rule_thresholds: list[dict],
        nova_vs_phrma: list,
    ) -> Optional[dict]:
        """
        Cross-model-class groundedness judge. The Policy Agent runs on
        gpt-4o-mini; this judge runs on gpt-4o. Different model in the same
        family is a weaker form of cross-model judging than cross-vendor
        (e.g., Claude judging GPT) but still buys some asymmetry — gpt-4o has
        different post-training and different failure modes than gpt-4o-mini.

        The judge specifically checks numeric claims, named rule IDs, and named
        entities (e.g., "OIG fraud indicators include X, Y, Z") against the
        retrieved chunks and rules. Topic overlap is not enough — the exact
        claim must be supported.

        Returns:
          dict with keys {grounded, ungrounded_claims, reasoning, judge_model}
          on success; None on judge failure (timeout, parse error, API error).

        Failure mode is intentionally non-blocking — if the judge fails, the
        response continues without a groundedness check, and the calling code
        surfaces this via data_limitations.
        """
        from openai import OpenAI
        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        chunks_payload = [
            {"chunk_id": c.chunk_id, "source_doc": c.source_doc, "excerpt": c.excerpt}
            for c in citations
        ]
        rules_payload = [
            {
                "rule_id": r.get("rule_id"),
                "rule_name": r.get("rule_name"),
                "threshold": r.get("threshold"),
                "authority": r.get("authority"),
            }
            for r in rule_thresholds
        ]
        comparisons_payload = [
            {
                "rule_name": c.rule_name,
                "nova_threshold": c.nova_threshold,
                "phrma_or_fallback_threshold": c.phrma_threshold,
                "nova_is_stricter": c.nova_is_stricter,
                "source_rule_id": c.source_rule_id,
            }
            for c in nova_vs_phrma
        ]

        judge_prompt = f"""You are evaluating whether a compliance answer is grounded in retrieved content.

Question: {question}

Retrieved policy chunk excerpts:
{json.dumps(chunks_payload, indent=2)}

Retrieved rules from rules registry:
{json.dumps(rules_payload, indent=2)}

Nova Pharma vs PhRMA comparisons (derived from rules registry):
{json.dumps(comparisons_payload, indent=2)}

Generated answer:
{answer}

Your task: Identify every specific factual claim in the answer — numeric thresholds (dollar amounts, counts, percentages), named rule IDs, named entities (e.g., "OIG fraud indicators include X, Y, Z"), and named statutes or regulations.

For each claim, check whether it is supported by the retrieved content.
- Topic overlap is NOT enough. If the answer says "OIG identifies low attendance as a fraud indicator" but the retrieved chunks discuss speaker programs without naming low attendance, that claim is NOT grounded.
- Numeric values must match exactly. "$50 lunch limit" is grounded only if a retrieved rule has threshold 50 USD for lunch.
- Rule IDs cited in the answer must appear in the retrieved rules list.
- General statements that don't make specific factual claims (e.g., "compliance is important") can be considered grounded by default.

Respond with JSON only, no preamble, no code fences:
{{"grounded": true | false,
  "ungrounded_claims": ["<exact claim from answer>", ...],
  "reasoning": "<one sentence explaining the verdict>"}}

If grounded is true, ungrounded_claims should be an empty list."""

        try:
            client = OpenAI(api_key=api_key, timeout=30.0)
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a strict groundedness judge. Respond with JSON only."},
                    {"role": "user", "content": judge_prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=1000,
            )
            raw = response.choices[0].message.content
            verdict = json.loads(raw)
            if not isinstance(verdict, dict):
                return None
            if "grounded" not in verdict or "ungrounded_claims" not in verdict:
                return None
            return {
                "grounded": bool(verdict["grounded"]),
                "ungrounded_claims": list(verdict.get("ungrounded_claims", [])),
                "reasoning": str(verdict.get("reasoning", "")),
                "judge_model": "gpt-4o",
            }
        except Exception:
            return None

    @staticmethod
    def _detect_unsupported_scope_dimension(
        question: str,
        rule_thresholds: list[dict],
    ) -> Optional[dict]:
        """
        Deterministic fallback for scope-dimension recognition. Catches questions
        that reference jurisdiction, specialty, or role qualifiers that the
        rules registry does not segment by.

        Used when the agent fails to call `list_rule_dimensions` despite a clear
        scope qualifier in the question. The warning explicitly notes the
        fallback fired, so dataset analysis can distinguish "agent recognized
        the qualifier" from "fallback caught it."

        Returns a dict with warning details if fired, None otherwise.
        """
        q_lower = question.lower()

        us_states = [
            "alabama", "alaska", "arizona", "arkansas", "california",
            "colorado", "connecticut", "delaware", "florida", "georgia",
            "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
            "kentucky", "louisiana", "maine", "maryland", "massachusetts",
            "michigan", "minnesota", "mississippi", "missouri", "montana",
            "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
            "new york", "north carolina", "north dakota", "ohio", "oklahoma",
            "oregon", "pennsylvania", "rhode island", "south carolina",
            "south dakota", "tennessee", "texas", "utah", "vermont",
            "virginia", "washington", "west virginia", "wisconsin", "wyoming",
            "district of columbia",
        ]
        jurisdiction_phrases = [
            "state-specific", "by state", "in this state", "per state",
            "jurisdiction", "regional", "by region",
        ]
        specialties = [
            "cardiologist", "oncologist", "pediatrician", "neurologist",
            "psychiatrist", "endocrinologist", "rheumatologist", "dermatologist",
            "gastroenterologist", "hematologist", "nephrologist", "urologist",
            "internist", "family practitioner", "general practitioner",
        ]
        specialty_phrases = [
            "by specialty", "per specialty", "specialty-specific",
            "specialist", "for specialists",
        ]
        role_markers = [
            "nurse practitioner", "physician assistant", "pharmacist",
            "by role", "for nurses", "for nps", "for pas",
            "role-specific",
        ]

        matched_dimension = None
        matched_marker = None

        for state in us_states:
            if re.search(rf"\b{re.escape(state)}\b", q_lower):
                matched_dimension = "jurisdiction"
                matched_marker = state
                break

        if matched_dimension is None:
            for phrase in jurisdiction_phrases:
                if phrase in q_lower:
                    matched_dimension = "jurisdiction"
                    matched_marker = phrase
                    break

        if matched_dimension is None:
            for spec in specialties:
                if re.search(rf"\b{re.escape(spec)}s?\b", q_lower):
                    matched_dimension = "hcp_specialty"
                    matched_marker = spec
                    break

        if matched_dimension is None:
            for phrase in specialty_phrases:
                if phrase in q_lower:
                    matched_dimension = "hcp_specialty"
                    matched_marker = phrase
                    break

        if matched_dimension is None:
            for marker in role_markers:
                if marker in q_lower:
                    matched_dimension = "hcp_role"
                    matched_marker = marker
                    break

        if matched_dimension is None:
            return None

        return {
            "fallback_fired": True,
            "matched_dimension": matched_dimension,
            "matched_marker": matched_marker,
            "warning": (
                f"Safety net (not schema tool) caught dimension "
                f"'{matched_dimension}' in question (marker: '{matched_marker}'). "
                f"The rules registry does not segment rules by this dimension; "
                f"all rules apply uniformly. The agent should have called "
                f"list_rule_dimensions to recognize this; that it didn't suggests "
                f"the answer may incorrectly substitute general rules as if they "
                f"were specific to '{matched_marker}'."
            ),
        }

    @staticmethod
    def _detect_scope_mismatch(
        question: str,
        rule_thresholds: list[dict],
    ) -> Optional[str]:
        """
        Heuristic check: if the question explicitly asks about a time scope
        (annual / monthly / weekly / daily) and no retrieved rule has that
        time scope, return a warning string. Otherwise return None.

        Conservative by design — only fires on explicit scope words to avoid
        false positives on neutral questions.
        """
        q_lower = question.lower()
        scope_words = {
            "annual":    "annual",
            "yearly":    "annual",
            "per year":  "annual",
            "monthly":   "monthly",
            "per month": "monthly",
            "weekly":    "weekly",
            "per week":  "weekly",
            "daily":     "daily",
            "per day":   "daily",
        }
        question_scope = None
        for word, scope in scope_words.items():
            if word in q_lower:
                question_scope = scope
                break
        if question_scope is None:
            return None
        if not rule_thresholds:
            return None

        retrieved_scopes = {
            (r.get("scope") or {}).get("time_scope")
            for r in rule_thresholds
        }
        if question_scope in retrieved_scopes:
            return None
        return (
            f"Question asked about a '{question_scope}' scope, but no rule "
            f"with that time scope was retrieved. Retrieved rules have time "
            f"scopes: {sorted(s for s in retrieved_scopes if s)}. The answer "
            f"below may not directly address the question's scope."
        )

    @staticmethod
    def _assign_confidence(
        citations: list[PolicyCitation],
        rule_thresholds: list[dict],
    ) -> str:
        """Deterministic confidence assignment — never LLM-decided."""
        has_chunks = len(citations) > 0
        has_rules  = len(rule_thresholds) > 0
        if has_chunks and has_rules:
            return "high"
        if has_chunks or has_rules:
            return "medium"
        return "low"

    @staticmethod
    def _build_limitations(question: str) -> list[str]:
        """Return data_limitations relevant to this question's content."""
        q_lower = question.lower()
        limitations = [_BASE_LIMITATION]
        for trigger_words, limitation in _LIMITATION_TRIGGERS.items():
            if any(word in q_lower for word in trigger_words):
                limitations.append(limitation)
        return limitations

    @staticmethod
    def _intermediate_steps_to_str(steps: list) -> str:
        parts = []
        for i, (action, observation) in enumerate(steps, 1):
            tool_name  = getattr(action, "tool", "unknown")
            tool_input = getattr(action, "tool_input", "")
            obs_str    = str(observation)[:500]
            parts.append(
                f"Step {i}: [{tool_name}]\n"
                f"  Input: {tool_input}\n"
                f"  Output: {obs_str}"
            )
        return "\n".join(parts)

    def _log_to_mlflow(
        self,
        question: str,
        answer: "PolicyAnswer",
        latency_ms: float,
    ) -> None:
        if not _MLFLOW_ENABLED:
            return
        try:
            with mlflow.start_run(run_name=f"policy_{question[:30]}"):
                mlflow.log_params({
                    "question": question[:100],
                    "model":    self.model,
                })
                mlflow.log_metrics({
                    "num_chunks_retrieved": len(answer.relevant_chunks),
                    "num_rules_matched":    len(answer.rule_thresholds),
                    "num_nova_overrides":   len(answer.nova_vs_phrma),
                    "latency_ms":           latency_ms,
                })
                mlflow.set_tags({
                    "confidence": answer.confidence,
                    "phase":      "3",
                    "task":       "3.3",
                })
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    async def query(self, question: str) -> PolicyAnswer:
        """
        Answer a compliance question using the policy knowledge base and rules registry.

        Parameters
        ----------
        question : str
            Natural language compliance question.

        Returns
        -------
        PolicyAnswer
            Structured answer with citations, rule thresholds, Nova vs PhRMA
            comparison, confidence level, and full audit trail.
        """
        t0 = time.monotonic()

        try:
            prompt = (
                f"Answer this compliance question for Nova Pharma Inc: {question}\n"
                "When calling search_policy_docs, pass the question above "
                "verbatim as the query. Do not paraphrase or summarize it. "
                "Use lookup_rule to find exact thresholds from the rules "
                "registry. Cite specific chunk_ids and rule_ids in your "
                "answer. Compare Nova Pharma policy against PhRMA Code "
                "where relevant."
            )

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.executor.invoke({"input": prompt}),
            )

            steps      = result.get("intermediate_steps", [])
            llm_output = result.get("output", "")
            reasoning  = self._intermediate_steps_to_str(steps)

            # Group tool outputs by tool name (a tool may be called multiple times)
            tool_outputs: dict[str, list[dict]] = {
                "search_policy_docs": [],
                "lookup_rule": [],
            }

            for action, observation in steps:
                name = getattr(action, "tool", "")
                if name not in tool_outputs:
                    continue
                if isinstance(observation, str):
                    try:
                        parsed = json.loads(observation)
                    except json.JSONDecodeError:
                        parsed = {"raw": observation}
                elif isinstance(observation, dict):
                    parsed = observation
                else:
                    parsed = {}
                tool_outputs[name].append(parsed)

            # Parse structured outputs
            citations      = self._parse_citations(tool_outputs)
            rule_thresholds = self._parse_rule_thresholds(tool_outputs)
            nova_vs_phrma   = self._parse_nova_vs_phrma(tool_outputs)
            chunk_ids       = self._collect_chunk_ids_for_audit(citations, rule_thresholds)
            confidence      = self._assign_confidence(citations, rule_thresholds)

            # Post-processor: strip over-narration from TOPIC ABSENT answers.
            # Moved here (after citations parsed) so max_retrieval_relevance is
            # available. Prompt-layer TOOL OUTPUT SUPPRESSION did not bind reliably;
            # this is the deterministic safety net below the LLM layer.
            # Suppressed when max_relevance >= 0.55 to avoid stripping correct
            # content from misclassified retrieval questions (ret_02 pattern).
            _max_relevance = max(
                (c.relevance_score for c in citations),
                default=0.0,
            )
            _pproc_answer, _narration_note = strip_over_narration(
                llm_output.strip(),
                max_retrieval_relevance=_max_relevance,
            )
            if _narration_note is not None:
                llm_output = _pproc_answer

            limitations     = self._build_limitations(question)
            if _narration_note is not None:
                limitations.append(_narration_note)

            # Scope-mismatch safety net (defense in depth vs prompt-only fix).
            # Even if the prompt change above doesn't fully prevent scope conflation,
            # this catches the obvious cases (annual/monthly/weekly/daily) and surfaces
            # the warning to the user via data_limitations.
            scope_warning = self._detect_scope_mismatch(question, rule_thresholds)
            if scope_warning:
                limitations.insert(0, scope_warning)
                if confidence == "high":
                    confidence = "medium"

            # Bug A safety net — catches scope dimensions the agent should
            # have recognized via list_rule_dimensions but didn't.
            dimension_warning = self._detect_unsupported_scope_dimension(
                question, rule_thresholds
            )
            if dimension_warning:
                limitations.insert(0, dimension_warning["warning"])
                if confidence == "high":
                    confidence = "medium"

            # Bug B safety net — cross-model-class groundedness judge.
            # Non-blocking: if judge fails, response continues with a note
            # in data_limitations. Synchronous call wrapped in executor to
            # match the existing async pattern.
            groundedness_check = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._judge_groundedness(
                    question, llm_output.strip(), citations, rule_thresholds, nova_vs_phrma
                ),
            )
            if groundedness_check is None:
                limitations.append(
                    "Groundedness check unavailable for this query "
                    "(judge call failed or timed out)."
                )
            elif not groundedness_check["grounded"]:
                ungrounded_summary = "; ".join(
                    groundedness_check["ungrounded_claims"][:3]
                )
                limitations.insert(
                    0,
                    f"Groundedness check flagged ungrounded claims: "
                    f"{ungrounded_summary}. Judge reasoning: "
                    f"{groundedness_check['reasoning']}"
                )
                # Intentional hard override: ungrounded claims → "low" regardless of
                # prior safety-net downgrades. A scope-mismatch + ungrounded answer
                # (two signals) correctly lands at "low". The scope safety nets are
                # soft (high→medium, guarded); this is the final arbiter.
                # If "low" fires too often on valid refusals ("no X exists"), fix is
                # in the groundedness judge prompt (negative-claim handling), not here.
                confidence = "low"

            latency_ms = (time.monotonic() - t0) * 1000

            answer = PolicyAnswer(
                question=question,
                generated_at=datetime.now(timezone.utc),
                answer=llm_output.strip(),
                relevant_chunks=citations,
                rule_thresholds=rule_thresholds,
                nova_vs_phrma=nova_vs_phrma,
                chunk_ids_for_audit=chunk_ids,
                confidence=confidence,
                data_limitations=limitations,
                groundedness_check=groundedness_check,
                agent_reasoning=reasoning,
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            answer = PolicyAnswer(
                question=question,
                generated_at=datetime.now(timezone.utc),
                answer=f"Agent error: {e}",
                relevant_chunks=[],
                rule_thresholds=[],
                nova_vs_phrma=[],
                chunk_ids_for_audit=[],
                confidence="low",
                data_limitations=self._build_limitations(question)
                    + [f"Agent error: {e}"],
                groundedness_check=None,
                agent_reasoning=f"ERROR: {e}",
            )

        self._log_to_mlflow(question, answer, latency_ms)
        return answer
