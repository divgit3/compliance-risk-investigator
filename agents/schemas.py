# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
agents/schemas.py — Pydantic v2 output models shared across all Phase 3 agents.

Used by:
  - InvestigationAgent  → InvestigationReport
  - MonitoringAgent     → MonitoringReport (Task 3.2)
  - PolicyAgent         → PolicyAnswer (Task 3.3)
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Shared building blocks ─────────────────────────────────────────────────────

class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    page_num: int  # 1-indexed, matches page_num convention throughout


class PolicyCitation(BaseModel):
    chunk_id: str
    source_doc: str
    relevance_score: float
    excerpt: str  # full chunk text
    page_num: Optional[int] = None
    bboxes: Optional[List[BBox]] = None


class RuleFlag(BaseModel):
    flag_name: str
    flag_value: float
    threshold: Optional[float] = None
    policy_citation: Optional[str] = None  # rule_reference from rules.json
    severity: str  # critical | high | medium | low


class PeerBenchmark(BaseModel):
    percentile_rank: float
    peer_avg_total_spend: float
    peer_max_total_spend: float
    hcp_total_spend: float
    specialty: Optional[str] = None
    state: Optional[str] = None


class AnomalousFeature(BaseModel):
    feature_name: str
    hcp_value: float
    importance_score: float
    pearson_r: Optional[float] = None
    direction: str  # "high" | "low" vs population mean


# ── Investigation Agent output ─────────────────────────────────────────────────

class InvestigationReport(BaseModel):
    hcp_id: str
    generated_at: datetime
    risk_score: float
    risk_tier: str
    rule_score: float
    if_score: float
    score_explanation: str
    rule_flags: List[RuleFlag] = Field(default_factory=list)
    peer_benchmark: PeerBenchmark
    top_anomalous_features: List[AnomalousFeature] = Field(default_factory=list)
    policy_citations: List[PolicyCitation] = Field(default_factory=list)
    recommended_action: str  # investigate | review | monitor | continue
    action_rationale: str
    agent_reasoning: str  # full ReAct chain for audit


# ── Monitoring Agent output (Task 3.2) ─────────────────────────────────────────

class FlagTrend(BaseModel):
    flag_name: str
    count: int                      # HCPs with this flag fired
    rate: float                     # count / total_hcps_in_scope
    policy_citation: Optional[str] = None  # from rules.json
    severity: str                   # critical | high | medium | low


class SegmentRisk(BaseModel):
    segment_type: str               # "specialty" | "state"
    segment_value: str              # e.g. "Oncology" | "CA"
    hcp_count: int
    critical_count: int
    high_count: int
    critical_rate: float            # critical_count / hcp_count
    high_rate: float
    avg_risk_score: float
    top_flag: str                   # most common fired flag in this segment


class SystemicIssue(BaseModel):
    issue_type: str                 # "high_flag_rate_specialty" |
                                    # "high_flag_rate_state" |
                                    # "dominant_flag_pattern" |
                                    # "critical_cluster"
    description: str
    affected_hcp_count: int
    severity: str                   # critical | high | medium
    top_flags: List[str] = Field(default_factory=list)
    recommendation: str             # deterministic string, not LLM


class RiskDistribution(BaseModel):
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    total_count: int
    critical_pct: float
    high_pct: float
    medium_pct: float
    low_pct: float
    avg_risk_score: float
    median_risk_score: float


class MonitoringReport(BaseModel):
    generated_at: datetime
    scope_description: str          # human-readable filter summary
    specialty_filter: Optional[str] = None
    state_filter: Optional[str] = None
    risk_tier_filter: Optional[str] = None
    total_hcps_in_scope: int
    risk_distribution: RiskDistribution
    top_flags: List[FlagTrend] = Field(default_factory=list)         # top 10 by rate
    high_risk_segments: List[SegmentRisk] = Field(default_factory=list)  # top 5 specialty + top 5 state
    systemic_issues: List[SystemicIssue] = Field(default_factory=list)
    summary_narrative: str          # LLM-generated
    data_limitations: List[str] = Field(default_factory=list)       # always populated
    agent_reasoning: str            # full ReAct chain for audit


# ── Policy Agent output (Task 3.3) ─────────────────────────────────────────────

class NovaVsPhRMA(BaseModel):
    rule_name: str
    phrma_threshold: Optional[str] = None  # e.g. "$100 per meal" (from fallback_rules)
    nova_threshold: str                    # e.g. "$25 breakfast / $50 lunch"
    nova_is_stricter: bool
    source_rule_id: str                    # rules.json rule_id


class PolicyAnswer(BaseModel):
    question: str
    generated_at: datetime
    answer: str                                              # LLM narrative, grounded in citations
    relevant_chunks: List[PolicyCitation] = Field(default_factory=list)   # from Qdrant
    rule_thresholds: List[dict] = Field(default_factory=list)  # [{rule_id, rule_name, threshold, authority, source_doc}]
    nova_vs_phrma: List[NovaVsPhRMA] = Field(default_factory=list)        # only when nova_override=True
    chunk_ids_for_audit: List[str] = Field(default_factory=list)          # deduplicated
    confidence: str = "medium"                               # "high" | "medium" | "low"
    data_limitations: List[str] = Field(default_factory=list)
    groundedness_check: Optional[dict] = None               # judge output: {grounded, ungrounded_claims, reasoning, judge_model}
    agent_reasoning: str = ""                                # full ReAct chain for audit
