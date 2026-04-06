"""
agents/monitoring_agent.py — Monitoring Agent (Task 3.2)

Population-level compliance monitoring across 97,011 HCPs. Accepts optional
specialty, state, and risk_tier filters. Returns a structured MonitoringReport
with risk distribution, top flags, high-risk segments, systemic issues, and a
compliance-officer-facing narrative.

Usage:
    from agents.monitoring_agent import MonitoringAgent
    agent = MonitoringAgent(openai_api_key="sk-...")
    report = asyncio.run(agent.monitor(state="TX"))
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import mlflow
from langchain import hub
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_openai import ChatOpenAI

from agents.schemas import (
    FlagTrend,
    MonitoringReport,
    RiskDistribution,
    SegmentRisk,
    SystemicIssue,
)
from agents.tools.monitoring_tools import (
    detect_systemic_issues,
    get_flag_patterns,
    get_high_risk_segments,
    get_risk_distribution,
)

# ── MLflow config ──────────────────────────────────────────────────────────────

_MLFLOW_URI        = "http://localhost:5001"
_MLFLOW_EXPERIMENT = "monitoring_agent"

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a pharmaceutical compliance monitoring AI for Nova Pharma Inc.
Your role is to analyze population-level compliance risk patterns
across Nova Pharma's HCP interaction data.

You are monitoring 97,011 HCPs under the PhRMA Code, OIG guidelines,
and Nova Pharma internal policy (stricter than PhRMA).

Key thresholds:
- Meal limit: $25 (internal), $50 (external), $100 (international)
- Speaker FMV cap: $3,500 per engagement
- Annual speaker program cap: $75,000 per program year
- Risk tiers: critical>=60, high>=25, medium>=10, low<10
- Score = 60% rule-based + 40% Isolation Forest

Instructions:
- Always call get_risk_distribution first to establish baseline
- Always call get_flag_patterns to identify top compliance issues
- Always call get_high_risk_segments to find geographic and specialty hotspots
- Always call detect_systemic_issues — this is the most actionable output
- Write summary_narrative for a compliance officer, not a data scientist
  Focus on: what needs immediate action, what needs monitoring, what is healthy
- Always mention data limitations in your narrative
- Never invent numbers — use only what the tools return
- Do not flag engagement_priority_score anomalies (capped at 45pts — known issue)
- Do not use np_escalating_rank in any analysis (0-filled — known limitation)
- Note that specialty data is unavailable in the dev environment (all NULL)"""

_TOOLS = [
    get_risk_distribution,
    get_flag_patterns,
    get_high_risk_segments,
    detect_systemic_issues,
]

# ── Always-present data limitations ───────────────────────────────────────────

_DATA_LIMITATIONS = [
    "No temporal data available — report reflects 2024 snapshot only; trend analysis is not possible",
    "np_escalating_rank is 0-filled due to Athena/DuckDB split — excluded from all analysis",
    "engagement_priority_score capped at 45/100 — industry benchmarks incomplete until Task 3.5",
    "Peer benchmarks use specialty filter only — geographic sub-filtering not yet implemented",
    "specialty field is NULL for all HCPs in DuckDB dev environment — specialty segments unavailable",
]


class MonitoringAgent:
    """
    ReAct agent that monitors the HCP population and returns a MonitoringReport.

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
        self.llm = ChatOpenAI(model=model, api_key=api_key, temperature=0, timeout=30, max_retries=2)

        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
        react_prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a pharmaceutical compliance monitoring AI for Nova Pharma Inc.\n"
                "Your role is to analyze population-level compliance risk patterns\n"
                "across Nova Pharma's 97,011 HCP interaction dataset.\n\n"
                "Key policy thresholds:\n"
                "- Meal limit: $25 (internal), $50 (external), $100 (international)\n"
                "- Speaker FMV cap: $3,500 per engagement\n"
                "- Annual speaker program cap: $75,000 per program year\n"
                "- Risk tiers: critical>=60, high>=25, medium>=10, low<10\n"
                "- Score = 60% rule-based + 40% Isolation Forest\n\n"
                "Tool call order:\n"
                "1. Call get_risk_distribution first to establish baseline\n"
                "2. Call get_flag_patterns to identify top compliance issues\n"
                "3. Call get_high_risk_segments to find specialty and state hotspots\n"
                "4. Call detect_systemic_issues — this is the most actionable output\n\n"
                "After all 4 tools have returned results, write your final answer immediately.\n"
                "Never repeat a tool call you have already made successfully.\n"
                "Write your summary_narrative for a compliance officer, not a data scientist.\n"
                "Focus on: what needs immediate action, what needs monitoring, what is healthy.\n"
                "Never invent numbers — use only what the tools return.\n"
                "Do not flag engagement_priority_score anomalies (capped at 45pts — known issue).\n"
                "Do not use np_escalating_rank in any analysis (0-filled — known limitation).\n"
                "Always mention data limitations in your narrative."
            )),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        # react_prompt.template = _SYSTEM_PROMPT + "\n\n" + react_prompt.template
        self.tools = _TOOLS 
        print("AGENT_INIT: creating openai_tools_agent...", flush=True)
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

        try:
            mlflow.set_tracking_uri(_MLFLOW_URI)
            mlflow.set_experiment(_MLFLOW_EXPERIMENT)
        except Exception:
            pass

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _build_scope_description(
        specialty: Optional[str],
        state: Optional[str],
        risk_tier: Optional[str],
    ) -> str:
        parts = []
        if specialty:
            parts.append(f"Specialty: {specialty}")
        if state:
            parts.append(f"State: {state}")
        if risk_tier:
            parts.append(f"Risk tier: {risk_tier}")
        return ", ".join(parts) if parts else "Full population (97,011 HCPs)"

    @staticmethod
    def _parse_risk_distribution(tool_result: dict) -> Optional[RiskDistribution]:
        required = {"total_count", "critical_count", "high_count", "medium_count",
                    "low_count", "critical_pct", "high_pct", "medium_pct", "low_pct",
                    "avg_risk_score", "median_risk_score"}
        if not required.issubset(tool_result.keys()):
            return None
        return RiskDistribution(
            critical_count=int(tool_result["critical_count"]),
            high_count=int(tool_result["high_count"]),
            medium_count=int(tool_result["medium_count"]),
            low_count=int(tool_result["low_count"]),
            total_count=int(tool_result["total_count"]),
            critical_pct=float(tool_result["critical_pct"]),
            high_pct=float(tool_result["high_pct"]),
            medium_pct=float(tool_result["medium_pct"]),
            low_pct=float(tool_result["low_pct"]),
            avg_risk_score=float(tool_result["avg_risk_score"]),
            median_risk_score=float(tool_result["median_risk_score"]),
        )

    @staticmethod
    def _parse_flag_trends(tool_result: dict) -> list[FlagTrend]:
        trends = []
        for f in tool_result.get("flags", []):
            trends.append(FlagTrend(
                flag_name=f["flag_name"],
                count=int(f["count"]),
                rate=float(f["rate"]),
                policy_citation=f.get("policy_citation"),
                severity=f.get("severity", "medium"),
            ))
        return trends

    @staticmethod
    def _parse_segments(tool_result: dict) -> list[SegmentRisk]:
        segments = []
        for seg_type in ("specialty_segments", "state_segments"):
            for s in tool_result.get(seg_type, []):
                segments.append(SegmentRisk(
                    segment_type=s["segment_type"],
                    segment_value=s["segment_value"],
                    hcp_count=int(s["hcp_count"]),
                    critical_count=int(s["critical_count"]),
                    high_count=int(s["high_count"]),
                    critical_rate=float(s["critical_rate"]),
                    high_rate=float(s["high_rate"]),
                    avg_risk_score=float(s["avg_risk_score"]),
                    top_flag=s["top_flag"],
                ))
        return segments

    @staticmethod
    def _parse_systemic_issues(tool_result: dict) -> list[SystemicIssue]:
        issues = []
        for iss in tool_result.get("issues", []):
            issues.append(SystemicIssue(
                issue_type=iss["issue_type"],
                description=iss["description"],
                affected_hcp_count=int(iss["affected_hcp_count"]),
                severity=iss["severity"],
                top_flags=iss.get("top_flags", []),
                recommendation=iss.get("recommendation", ""),
            ))
        return issues

    @staticmethod
    def _intermediate_steps_to_str(steps: list) -> str:
        parts = []
        for i, (action, observation) in enumerate(steps, 1):
            tool_name  = getattr(action, "tool", "unknown")
            tool_input = getattr(action, "tool_input", "")
            obs_str    = str(observation)[:600]
            parts.append(
                f"Step {i}: [{tool_name}]\n"
                f"  Input: {tool_input}\n"
                f"  Output: {obs_str}"
            )
        return "\n".join(parts)

    def _fallback_report(
        self,
        scope_description: str,
        specialty: Optional[str],
        state: Optional[str],
        risk_tier: Optional[str],
        error_msg: str,
        limitations: list[str],
    ) -> MonitoringReport:
        """Minimal report built directly from tools (no LLM) when agent fails."""
        try:
            dist_raw = get_risk_distribution.invoke({
                "specialty": specialty or "",
                "state": state or "",
            })
            dist = self._parse_risk_distribution(dist_raw)
        except Exception:
            dist = None

        if dist is None:
            dist = RiskDistribution(
                critical_count=0, high_count=0, medium_count=0, low_count=0,
                total_count=0, critical_pct=0.0, high_pct=0.0,
                medium_pct=0.0, low_pct=0.0,
                avg_risk_score=0.0, median_risk_score=0.0,
            )

        return MonitoringReport(
            generated_at=datetime.now(timezone.utc),
            scope_description=scope_description,
            specialty_filter=specialty,
            state_filter=state,
            risk_tier_filter=risk_tier,
            total_hcps_in_scope=dist.total_count,
            risk_distribution=dist,
            top_flags=[],
            high_risk_segments=[],
            systemic_issues=[],
            summary_narrative=f"Agent error — fallback report. Error: {error_msg}",
            data_limitations=limitations + [f"Agent error: {error_msg}"],
            agent_reasoning=f"ERROR: {error_msg}",
        )

    def _log_to_mlflow(
        self,
        report: MonitoringReport,
        latency_ms: float,
    ) -> None:
        try:
            with mlflow.start_run(run_name=f"monitor_{report.scope_description[:40]}"):
                mlflow.log_params({
                    "specialty_filter": report.specialty_filter or "none",
                    "state_filter":     report.state_filter or "none",
                    "risk_tier_filter": report.risk_tier_filter or "none",
                    "model":            self.model,
                })
                mlflow.log_metrics({
                    "total_hcps_in_scope": report.total_hcps_in_scope,
                    "critical_count":      report.risk_distribution.critical_count,
                    "high_count":          report.risk_distribution.high_count,
                    "num_systemic_issues": len(report.systemic_issues),
                    "latency_ms":          latency_ms,
                })
                mlflow.set_tags({"phase": "3", "task": "3.2"})
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    async def monitor(
        self,
        specialty: Optional[str] = None,
        state: Optional[str] = None,
        risk_tier: Optional[str] = None,
    ) -> MonitoringReport:
        """
        Generate a population-level compliance monitoring report.

        Parameters
        ----------
        specialty : str, optional
            Filter to a single specialty (e.g. "Oncology").
            Note: NULL for all HCPs in DuckDB dev — specialty filter has no effect.
        state : str, optional
            Filter to a single US state abbreviation (e.g. "TX").
        risk_tier : str, optional
            Contextual label for the report scope (does not filter data directly).

        Returns
        -------
        MonitoringReport
        """
        t0 = time.monotonic()
        scope_description = self._build_scope_description(specialty, state, risk_tier)
        limitations = list(_DATA_LIMITATIONS)

        try:
            prompt = (
                f"Generate a compliance monitoring report for: {scope_description}.\n"
                "Use all available tools to gather:\n"
                "1. Risk tier distribution across the population in scope\n"
                "2. Top compliance flags by frequency (top 10)\n"
                "3. High-risk segments by specialty and state (top 5 each)\n"
                "4. Any systemic issues requiring escalation\n"
                "Then write a summary_narrative that a compliance officer can act on.\n"
                "Note any data limitations in your narrative."
            )

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._executor.invoke({"input": prompt}),
            )

            steps      = result.get("intermediate_steps", [])
            llm_output = result.get("output", "")
            reasoning  = self._intermediate_steps_to_str(steps)

            # Collect tool outputs keyed by tool name
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

            # Parse each tool's output
            dist_raw     = tool_outputs.get("get_risk_distribution", {})
            flags_raw    = tool_outputs.get("get_flag_patterns", {})
            segs_raw     = tool_outputs.get("get_high_risk_segments", {})
            issues_raw   = tool_outputs.get("detect_systemic_issues", {})

            # Add tool errors to limitations
            for tool_name, out in tool_outputs.items():
                if "error" in out:
                    limitations.append(f"Tool '{tool_name}' error: {out['error']}")

            dist     = self._parse_risk_distribution(dist_raw)
            top_flags = self._parse_flag_trends(flags_raw)
            segments  = self._parse_segments(segs_raw)
            issues    = self._parse_systemic_issues(issues_raw)

            # Fallback distribution via direct tool call if agent skipped it
            if dist is None:
                try:
                    direct = get_risk_distribution.invoke({
                        "specialty": specialty or "",
                        "state": state or "",
                    })
                    dist = self._parse_risk_distribution(direct)
                except Exception:
                    pass

            if dist is None:
                dist = RiskDistribution(
                    critical_count=0, high_count=0, medium_count=0, low_count=0,
                    total_count=0, critical_pct=0.0, high_pct=0.0,
                    medium_pct=0.0, low_pct=0.0,
                    avg_risk_score=0.0, median_risk_score=0.0,
                )

            latency_ms = (time.monotonic() - t0) * 1000

            report = MonitoringReport(
                generated_at=datetime.now(timezone.utc),
                scope_description=scope_description,
                specialty_filter=specialty,
                state_filter=state,
                risk_tier_filter=risk_tier,
                total_hcps_in_scope=dist.total_count,
                risk_distribution=dist,
                top_flags=top_flags,
                high_risk_segments=segments,
                systemic_issues=issues,
                summary_narrative=llm_output.strip(),
                data_limitations=limitations,
                agent_reasoning=reasoning,
            )

        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            report = self._fallback_report(
                scope_description, specialty, state, risk_tier, str(e), limitations
            )

        self._log_to_mlflow(report, latency_ms)
        return report
