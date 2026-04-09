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
import time
from datetime import datetime, timezone
from typing import Optional

import mlflow
from langchain.prompts import PromptTemplate
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_openai import ChatOpenAI

from agents.schemas import NovaVsPhRMA, PolicyAnswer, PolicyCitation
from agents.tools.policy_tools import lookup_rule, search_policy_docs

# ── MLflow config ──────────────────────────────────────────────────────────────

_MLFLOW_URI        = "http://localhost:5001"
_MLFLOW_EXPERIMENT = "policy_agent"

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a pharmaceutical compliance policy expert AI for Nova Pharma Inc.
You answer compliance questions by searching a curated policy knowledge
base and a rules registry. You provide precise, citation-backed answers.

Your knowledge base contains 5 documents (128 chunks total):
  - PhRMA Code 2022: industry standard voluntary guidelines
  - OIG Compliance Program Guidance: federal advisory guidance
  - OIG Speaker Program Fraud Alert: enforcement risk guidance
  - CMS Data Dictionary: open payments data definitions
  - Nova Pharma Internal Policy: company policy (STRICTER than PhRMA)

Nova Pharma key overrides vs PhRMA:
  Meals: $25 breakfast / $50 lunch / $100 dinner
         (PhRMA allows up to ~$100 flat — Nova is stricter for most meal types)
  Speaker FMV: $3,500 per engagement cap
  Annual speaker cap: $75,000 per program year

Risk model context (for questions about scoring):
  Tiers: critical>=60, high>=25, medium>=10, low<10
  Score: 60% rule-based + 40% Isolation Forest anomaly detection

Instructions:
  - ALWAYS call lookup_rule first for any question with a threshold,
    dollar amount, or named rule (meals, FMV, speaker, cap, rationale)
  - ALWAYS call search_policy_docs for every question — even threshold
    questions benefit from narrative policy context
  - Call search_policy_docs multiple times with different queries if
    the question has multiple aspects
  - Always cite chunk_ids and rule_ids explicitly in your answer
  - When Nova Pharma policy differs from PhRMA, always flag the difference
  - Never invent thresholds — use only what lookup_rule returns
  - If a question is outside your knowledge base, say so clearly
    and cite what partial information you do have"""

_TOOLS = [search_policy_docs, lookup_rule]

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
                "- Meals: $25 internal / $50 external / $100 international\n"
                "  (PhRMA allows $100 flat — Nova is stricter for domestic events)\n"
                "- Speaker FMV: $3,500 per engagement\n"
                "- Annual speaker cap: $75,000 per program year\n\n"
                "Tool call order:\n"
                "1. Call lookup_rule first for any question involving a threshold,\n"
                "   dollar amount, or named rule (meals, FMV, speaker, cap, rationale)\n"
                "2. Call search_policy_docs for every question — use broad regulatory\n"
                "   language, not internal flag names\n"
                "   Good queries: 'fair market value speaker honoraria'\n"
                "                 'pharmaceutical representative meal entertainment limit'\n"
                "                 'anti-kickback statute speaker program'\n"
                "                 'OIG compliance program pharmaceutical manufacturer'\n\n"
                "After both tools have returned results, write your final answer immediately.\n"
                "Never repeat a tool call you have already made successfully.\n"
                "Always cite chunk_ids and rule_ids explicitly in your answer.\n"
                "When Nova policy differs from PhRMA, always flag the difference.\n"
                "Never invent thresholds — use only what lookup_rule returns.\n"
                "If a question is outside your knowledge base, say so clearly."
            )),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
   
        self.tools = _TOOLS
        self._prompt = react_prompt
        self._executor = None  # created lazily on first use
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
                max_iterations=12,
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
                "Use search_policy_docs to find relevant policy chunks from the "
                "knowledge base. Use lookup_rule to find exact thresholds from "
                "the rules registry. Cite specific chunk_ids and rule_ids in "
                "your answer. Compare Nova Pharma policy against PhRMA Code "
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
            limitations     = self._build_limitations(question)

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
                agent_reasoning=f"ERROR: {e}",
            )

        self._log_to_mlflow(question, answer, latency_ms)
        return answer
