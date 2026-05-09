# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
streamlit_app/components/charts.py — Reusable Plotly chart functions.

All functions return go.Figure. Callers are responsible for st.plotly_chart().
"""

from __future__ import annotations

import plotly.graph_objects as go

from config import RISK_TIER_COLORS, TIER_ORDER

_LAYOUT_BASE = dict(
    paper_bgcolor="white",
    plot_bgcolor="white",
    margin=dict(l=10, r=10, t=40, b=10),
    font=dict(family="Inter, sans-serif", size=13),
)


def risk_tier_pie(tier_counts: dict[str, int]) -> go.Figure:
    """Donut chart of HCP counts by risk tier."""
    labels = [t for t in TIER_ORDER if t in tier_counts]
    values = [tier_counts[t] for t in labels]
    colors = [RISK_TIER_COLORS[t] for t in labels]

    fig = go.Figure(go.Pie(
        labels=[t.capitalize() for t in labels],
        values=values,
        hole=0.45,
        marker=dict(colors=colors, line=dict(color="white", width=2)),
        textinfo="label+percent",
        hovertemplate="%{label}: %{value:,} HCPs<extra></extra>",
    ))
    fig.update_layout(
        **_LAYOUT_BASE,
        title=dict(text="Risk Tier Distribution", x=0.5, xanchor="center"),
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=False, zeroline=False),
    )
    return fig


def risk_tier_bar(tier_counts: dict[str, int]) -> go.Figure:
    """Vertical bar chart of HCP counts by risk tier."""
    tiers = [t for t in TIER_ORDER if t in tier_counts]
    counts = [tier_counts[t] for t in tiers]
    colors = [RISK_TIER_COLORS[t] for t in tiers]

    fig = go.Figure(go.Bar(
        x=[t.capitalize() for t in tiers],
        y=counts,
        marker_color=colors,
        text=[f"{c:,}" for c in counts],
        textposition="outside",
        hovertemplate="%{x}: %{y:,} HCPs<extra></extra>",
    ))
    fig.update_layout(
        **_LAYOUT_BASE,
        title=dict(text="HCPs by Tier", x=0.5, xanchor="center"),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=False, zeroline=False, title="HCP Count"),
    )
    return fig


def top_flags_bar(flag_counts: dict[str, int], top_n: int = 10) -> go.Figure:
    """Horizontal bar chart of the top N fired flags."""
    sorted_flags = sorted(flag_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not sorted_flags:
        return go.Figure()

    # Reverse so highest is at top
    names = [f[0].replace("flag_", "").replace("_", " ") for f, _ in reversed(sorted_flags)]
    counts = [c for _, c in reversed(sorted_flags)]

    fig = go.Figure(go.Bar(
        x=counts,
        y=names,
        orientation="h",
        marker_color="#6366F1",
        text=[f"{c:,}" for c in counts],
        textposition="outside",
        hovertemplate="%{y}: %{x:,} HCPs<extra></extra>",
    ))
    fig.update_layout(
        **_LAYOUT_BASE,
        title=dict(text=f"Top {top_n} Fired Flags", x=0.5, xanchor="center"),
        xaxis=dict(showgrid=False, zeroline=False, title="HCP Count"),
        yaxis=dict(showgrid=False, zeroline=False),
        height=max(300, top_n * 32),
    )
    return fig
