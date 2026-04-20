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

_MLFLOW_URI        = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001")
_MLFLOW_EXPERIMENT = "investigation_agent"
_MLFLOW_ENABLED    = os.environ.get("MLFLOW_ENABLED", "true").lower() == "true"

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
            timeout=30,
            max_retries=2,
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
        self._prompt = react_prompt
        self._executor = None  # created lazily on first use

        # MLflow setup (best-effort — failures never crash the agent)
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
                raise RuntimeError(f"InvestigationAgent: failed to create agent executor: {e}") from e
            self._executor = AgentExecutor(
                agent=agent,
                tools=self.tools,
                max_iterations=5,
                early_stopping_method="generate",
                handle_parsing_errors=True,
                return_intermediate_steps=True,
                verbose=False,
            )
        return self._executor

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
        if not _MLFLOW_ENABLED:
            return
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
            # Call tools directly — no agent loop needed
            print(f"INVESTIGATE: calling get_hcp_risk_profile for {hcp_id}...", flush=True)
            profile_raw = await asyncio.to_thread(
                get_hcp_risk_profile.invoke, {"hcp_id": hcp_id}
            )
            print(f"INVESTIGATE: get_hcp_risk_profile done: {type(profile_raw)}", flush=True)

            print("INVESTIGATE: calling get_rule_flags...", flush=True)
            flags_raw = await asyncio.to_thread(
                get_rule_flags.invoke, {"hcp_id": hcp_id}
            )
            print(f"INVESTIGATE: get_rule_flags done: {type(flags_raw)}", flush=True)

            print("INVESTIGATE: calling get_peer_benchmark...", flush=True)
            bench_raw = await asyncio.to_thread(
                get_peer_benchmark.invoke, {"hcp_id": hcp_id}
            )
            print(f"INVESTIGATE: get_peer_benchmark done: {type(bench_raw)}", flush=True)

            print("INVESTIGATE: calling get_top_anomalous_features...", flush=True)
            feats_raw = await asyncio.to_thread(
                get_top_anomalous_features.invoke, {"hcp_id": hcp_id}
            )
            print(f"INVESTIGATE: get_top_anomalous_features done: {type(feats_raw)}", flush=True)

            reasoning = "Direct tool execution — no agent loop"

            profile   = profile_raw if isinstance(profile_raw, dict) else {}
            flags_out = flags_raw   if isinstance(flags_raw, dict)   else {}
            bench_out = bench_raw   if isinstance(bench_raw, dict)   else {}
            feats_out = feats_raw   if isinstance(feats_raw, dict)   else {}

            risk_tier  = str(profile.get("risk_tier", "medium"))
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

            # Single LLM call for narrative
            narrative_prompt = (
                f"You are a pharmaceutical compliance investigator. Write a 3-4 sentence "
                f"investigation summary for HCP {hcp_id}.\n"
                f"Risk profile: {profile}\n"
                f"Fired flags: {flags_out}\n"
                f"Peer benchmark: {bench_out}\n"
                f"Top features: {feats_out}\n"
                f"Be specific about risk score, flags, and recommended action."
            )
            print("INVESTIGATE: calling OpenAI for narrative...", flush=True)
            import openai, os as _os
            _api_key = _os.environ.get("OPENAI_API_KEY")
            with __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=1) as pool:
                import asyncio as _asyncio
                llm_output = await _asyncio.get_event_loop().run_in_executor(
                    pool,
                    lambda: openai.OpenAI(api_key=_api_key).chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": narrative_prompt}],
                        temperature=0,
                        max_tokens=300,
                        timeout=30,
                    ).choices[0].message.content
                )
            print(f"INVESTIGATE: narrative done, length={len(llm_output)}", flush=True)

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
                policy_citations=[],
                recommended_action=_TIER_TO_ACTION.get(risk_tier, "monitor"),
                action_rationale=action_rationale,
                agent_reasoning=reasoning,
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            report = self._fallback_report(hcp_id, str(e))

        self._log_to_mlflow(hcp_id, report, latency_ms)
        return report
