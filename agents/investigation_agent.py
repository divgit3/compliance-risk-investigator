"""
agents/investigation_agent.py — Investigation Agent (Task 3.1)

Orchestrates five LangChain tools via a ReAct loop to produce a structured,
policy-grounded InvestigationReport for a single HCP.

Usage:
    from agents.investigation_agent import InvestigationAgent
    agent = InvestigationAgent(openai_api_key="sk-...")
    report = asyncio.run(agent.investigate("HCP_00001"))
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mlflow
import pandas as pd
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_openai import ChatOpenAI

from agents.schemas import (
    AnomalousFeature,
    InvestigationReport,
    PeerBenchmark,
    PolicyCitation,
    RuleFlag,
)
from agents.tools.data_tools import (
    get_hcp_risk_profile,
    get_peer_benchmark,
    get_rule_flags,
    get_top_anomalous_features,
)
from agents.tools.policy_tools import search_policy_docs
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
# ── MLflow config ──────────────────────────────────────────────────────────────

_MLFLOW_URI        = "http://localhost:5001"
_MLFLOW_EXPERIMENT = "investigation_agent"

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a pharmaceutical compliance investigator AI for Nova Pharma Inc.
Your role is to investigate HCP (Healthcare Professional) interactions
for compliance risks under the PhRMA Code, OIG guidelines, and Nova
Pharma internal policy. Nova Pharma policy is STRICTER than PhRMA.

Key policy thresholds:
- Meal limit: $25 (internal), $50 (external), $100 (international)
- Speaker FMV cap: $3,500 per engagement
- Annual speaker program cap: $75,000 per program year
- Risk tiers: critical>=60, high>=25, medium>=10, low<10
- Score = 60% rule-based + 40% Isolation Forest

Instructions:
- Always call get_hcp_risk_profile first
- Always call get_rule_flags and get_peer_benchmark
- Always call search_policy_docs with a query relevant to the
  flags found (e.g. "speaker FMV compliance threshold" or
  "meal expense limit PhRMA")
- Ground every recommendation in specific policy citations
- Be precise with dollar amounts and percentages
- Never speculate beyond the data your tools return
- The recommended_action will be set deterministically by the
  calling code — focus on writing clear rationale narratives"""

_TOOLS = [
    get_hcp_risk_profile,
    get_rule_flags,
    get_peer_benchmark,
    get_top_anomalous_features,
    search_policy_docs,
]

_TIER_TO_ACTION = {
    "critical": "investigate",
    "high":     "review",
    "medium":   "monitor",
    "low":      "continue",
}


class InvestigationAgent:
    """
    ReAct agent that investigates a single HCP and returns an InvestigationReport.

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
        OPENAI_API_KEY = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise EnvironmentError("OPENAI_API_KEY not set")

        self.model = model
        self.llm = ChatOpenAI(
            model=model,
            api_key=OPENAI_API_KEY,
            temperature=0,
        )


        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

        react_prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a pharmaceutical compliance investigator AI for Nova Pharma Inc.\n"
                "Your role is to investigate HCP (Healthcare Professional) interactions\n"
                "for compliance risks under the PhRMA Code, OIG guidelines, and Nova\n"
                "Pharma internal policy. Nova Pharma policy is STRICTER than PhRMA.\n\n"
                "Key policy thresholds:\n"
                "- Meal limit: $25 (internal), $50 (external), $100 (international)\n"
                "- Speaker FMV cap: $3,500 per engagement\n"
                "- Annual speaker program cap: $75,000 per program year\n"
                "- Risk tiers: critical>=60, high>=25, medium>=10, low<10\n"
                "- Score = 60% rule-based + 40% Isolation Forest\n\n"
                "Tool call order:\n"
                "1. Call get_hcp_risk_profile first\n"
                "2. Call get_rule_flags\n"
                "3. Call get_peer_benchmark\n"
                "4. Call get_top_anomalous_features\n"
                "5. Call search_policy_docs with a query relevant to the flags found\n\n"
                "After all 5 tools have returned results, write your final answer immediately.\n"
                "Never repeat a tool call you have already made successfully.\n"
                "Never speculate beyond the data your tools return.\n"
                "Ground every recommendation in specific policy citations.\n"
                "Be precise with dollar amounts and percentages."
            )),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        self.tools = _TOOLS 
        agent = create_openai_tools_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=react_prompt,
        )
        self._executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            max_iterations=12,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
            verbose=False,
        )

        # MLflow setup (best-effort — failures never crash the agent)
        try:
            mlflow.set_tracking_uri(_MLFLOW_URI)
            mlflow.set_experiment(_MLFLOW_EXPERIMENT)
        except Exception:
            pass

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_rule_flags(tool_result: dict) -> list[RuleFlag]:
        flags = []
        for f in tool_result.get("fired_flags", []):
            flags.append(RuleFlag(
                flag_name=f["flag_name"],
                flag_value=float(f["flag_value"]),
                threshold=f.get("threshold"),
                policy_citation=f.get("policy_citation"),
                severity=f.get("severity", "medium"),
            ))
        return flags

    @staticmethod
    def _parse_peer_benchmark(tool_result: dict) -> PeerBenchmark:
        return PeerBenchmark(
            percentile_rank=float(tool_result.get("percentile_rank", 0.0)),
            peer_avg_total_spend=float(tool_result.get("peer_avg_total_spend", 0.0)),
            peer_max_total_spend=float(tool_result.get("peer_max_total_spend", 0.0)),
            hcp_total_spend=float(tool_result.get("hcp_total_spend", 0.0)),
            specialty=tool_result.get("specialty"),
            state=tool_result.get("state"),
        )

    @staticmethod
    def _parse_anomalous_features(tool_result: dict) -> list[AnomalousFeature]:
        features = []
        for f in tool_result.get("features", []):
            features.append(AnomalousFeature(
                feature_name=f["feature_name"],
                hcp_value=float(f["hcp_value"]),
                importance_score=float(f["importance_score"]),
                pearson_r=float(f.get("pearson_r", f["importance_score"])),
                direction=f.get("direction", "high"),
            ))
        return features

    @staticmethod
    def _parse_policy_citations(tool_result: dict) -> list[PolicyCitation]:
        citations = []
        for r in tool_result.get("results", []):
            citations.append(PolicyCitation(
                chunk_id=r["chunk_id"],
                source_doc=r["source_doc"],
                relevance_score=float(r["relevance_score"]),
                excerpt=r["excerpt"],
            ))
        return citations

    @staticmethod
    def _intermediate_steps_to_str(steps: list) -> str:
        """Serialise ReAct intermediate steps to a readable audit string."""
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

    @staticmethod
    def _fallback_report(
        hcp_id: str,
        error_msg: str,
    ) -> InvestigationReport:
        """Minimal report when the agent fails — reads risk_scores.parquet directly."""
        try:
            root = Path(__file__).resolve().parents[1]
            risk = pd.read_parquet(root / "models/outputs/risk_scores.parquet")
            row  = risk[risk["hcp_id"] == hcp_id]
            if not row.empty:
                r    = row.iloc[0]
                tier = str(r["risk_tier"])
                return InvestigationReport(
                    hcp_id=hcp_id,
                    generated_at=datetime.now(timezone.utc),
                    risk_score=float(r["risk_score"]),
                    risk_tier=tier,
                    rule_score=float(r["rule_score"]),
                    if_score=float(r["anomaly_score"]),
                    score_explanation=f"Agent error: {error_msg}",
                    rule_flags=[],
                    peer_benchmark=PeerBenchmark(
                        percentile_rank=0.0,
                        peer_avg_total_spend=0.0,
                        peer_max_total_spend=0.0,
                        hcp_total_spend=0.0,
                    ),
                    top_anomalous_features=[],
                    policy_citations=[],
                    recommended_action=_TIER_TO_ACTION.get(tier, "monitor"),
                    action_rationale="Fallback report — agent failed.",
                    agent_reasoning=f"ERROR: {error_msg}",
                )
        except Exception:
            pass

        return InvestigationReport(
            hcp_id=hcp_id,
            generated_at=datetime.now(timezone.utc),
            risk_score=0.0,
            risk_tier="unknown",
            rule_score=0.0,
            if_score=0.0,
            score_explanation=f"Agent error: {error_msg}",
            rule_flags=[],
            peer_benchmark=PeerBenchmark(
                percentile_rank=0.0,
                peer_avg_total_spend=0.0,
                peer_max_total_spend=0.0,
                hcp_total_spend=0.0,
            ),
            top_anomalous_features=[],
            policy_citations=[],
            recommended_action="monitor",
            action_rationale="Fallback report — agent failed.",
            agent_reasoning=f"ERROR: {error_msg}",
        )

    def _log_to_mlflow(
        self,
        hcp_id: str,
        report: InvestigationReport,
        latency_ms: float,
    ) -> None:
        try:
            with mlflow.start_run(run_name=f"investigate_{hcp_id}"):
                mlflow.log_params({
                    "hcp_id":    hcp_id,
                    "model":     self.model,
                    "risk_tier": report.risk_tier,
                })
                mlflow.log_metrics({
                    "risk_score":  report.risk_score,
                    "num_flags":   len(report.rule_flags),
                    "latency_ms":  latency_ms,
                })
                mlflow.set_tags({
                    "recommended_action": report.recommended_action,
                    "phase":              "3",
                })
        except Exception:
            pass  # Never let logging crash the investigation

    # ── Public API ─────────────────────────────────────────────────────────────

    async def investigate(self, hcp_id: str) -> InvestigationReport:
        """
        Investigate a single HCP and return a structured InvestigationReport.

        Parameters
        ----------
        hcp_id : str
            The HCP identifier to investigate.

        Returns
        -------
        InvestigationReport
            Structured report with risk scores, rule flags, peer benchmark,
            anomalous features, policy citations, recommended action, and
            full ReAct audit trail.
        """
        t0 = time.monotonic()

        try:
            prompt = (
                f"Investigate HCP {hcp_id}. Use all available tools to gather:\n"
                "1. Risk profile (score, tier, key metrics)\n"
                "2. All fired rule flags with policy citations\n"
                "3. Peer benchmark comparison\n"
                "4. Top anomalous features driving the IF score\n"
                "5. Relevant policy context from the policy docs\n"
                "Then write a score_explanation and action_rationale."
            )

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._executor.invoke({"input": prompt}),
            )

            steps      = result.get("intermediate_steps", [])
            llm_output = result.get("output", "")
            reasoning  = self._intermediate_steps_to_str(steps)

            # Extract tool results from intermediate steps
            tool_outputs: dict[str, dict] = {}
            for action, observation in steps:
                name = getattr(action, "tool", "")
                if isinstance(observation, str):
                    try:
                        tool_outputs[name] = json.loads(observation)
                    except json.JSONDecodeError:
                        tool_outputs[name] = {"raw": observation}
                elif isinstance(observation, dict):
                    tool_outputs[name] = observation

            # Parse structured outputs from tool results
            profile   = tool_outputs.get("get_hcp_risk_profile", {})
            flags_out = tool_outputs.get("get_rule_flags", {})
            bench_out = tool_outputs.get("get_peer_benchmark", {})
            feats_out = tool_outputs.get("get_top_anomalous_features", {})
            policy_out= tool_outputs.get("search_policy_docs", {})

            # Fallback risk values from direct parquet read if tool failed
            if "error" in profile or not profile:
                root = Path(__file__).resolve().parents[1]
                risk = pd.read_parquet(root / "models/outputs/risk_scores.parquet")
                row  = risk[risk["hcp_id"] == hcp_id]
                if not row.empty:
                    r = row.iloc[0]
                    profile = {
                        "risk_score": float(r["risk_score"]),
                        "risk_tier":  str(r["risk_tier"]),
                        "rule_score": float(r["rule_score"]),
                        "if_score":   float(r["anomaly_score"]),
                    }

            risk_tier = str(profile.get("risk_tier", "medium"))
            rule_flags = self._parse_rule_flags(flags_out) if "fired_flags" in flags_out else []
            peer_bench = (
                self._parse_peer_benchmark(bench_out)
                if "percentile_rank" in bench_out
                else PeerBenchmark(
                    percentile_rank=0.0,
                    peer_avg_total_spend=0.0,
                    peer_max_total_spend=0.0,
                    hcp_total_spend=0.0,
                )
            )
            anomalous = self._parse_anomalous_features(feats_out) if "features" in feats_out else []
            citations  = self._parse_policy_citations(policy_out) if "results" in policy_out else []

            # Split LLM output into score_explanation + action_rationale
            # Heuristic: if output contains "rationale" keyword, split there
            if "rationale" in llm_output.lower():
                parts = llm_output.split("rationale", 1)
                score_explanation = parts[0].strip() if parts else ""
                action_rationale  = ("rationale" + parts[1]).strip() if len(parts) > 1 else (
                    f"Based on risk tier '{risk_tier}', "
                    f"action '{_TIER_TO_ACTION.get(risk_tier, 'monitor')}' is recommended."
                )
            else:
                score_explanation = llm_output.strip()
                action_rationale  = (
                    f"Based on risk tier '{risk_tier}', "
                    f"action '{_TIER_TO_ACTION.get(risk_tier, 'monitor')}' is recommended."
                )

            latency_ms = (time.monotonic() - t0) * 1000

            report = InvestigationReport(
                hcp_id=hcp_id,
                generated_at=datetime.now(timezone.utc),
                risk_score=float(profile.get("risk_score", 0.0)),
                risk_tier=risk_tier,
                rule_score=float(profile.get("rule_score", 0.0)),
                if_score=float(profile.get("if_score", profile.get("anomaly_score", 0.0))),
                score_explanation=score_explanation,
                rule_flags=rule_flags,
                peer_benchmark=peer_bench,
                top_anomalous_features=anomalous,
                policy_citations=citations,
                recommended_action=_TIER_TO_ACTION.get(risk_tier, "monitor"),
                action_rationale=action_rationale,
                agent_reasoning=reasoning,
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            report = self._fallback_report(hcp_id, str(e))

        self._log_to_mlflow(hcp_id, report, latency_ms)
        return report
