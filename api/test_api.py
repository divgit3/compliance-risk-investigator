"""
api/test_api.py — Smoke test for the FastAPI backend (Task 3.4)

Tests all 9 endpoints against a running server at localhost:8000.
Does NOT require OPENAI_API_KEY — agent endpoints are hit but 503
responses are accepted when the key is absent.

Usage:
    # Terminal 1 — start the server
    uvicorn api.main:app --reload --port 8000

    # Terminal 2 — run the smoke test
    source venv/bin/activate
    python api/test_api.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

_BASE = "http://localhost:8000"

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _ok(resp: httpx.Response, label: str, allow: tuple[int, ...] = ()) -> dict:
    allowed = {200, *allow}
    if resp.status_code not in allowed:
        print(f"  FAIL [{label}] status={resp.status_code}: {resp.text[:200]}")
        return {}
    data = resp.json()
    print(f"  OK   [{label}] status={resp.status_code}")
    return data


def _pick_hcp(client: httpx.Client) -> str:
    resp = client.get(f"{_BASE}/hcps", params={"limit": 1})
    data = resp.json()
    hcps = data.get("hcps", [])
    if not hcps:
        raise RuntimeError("No HCPs returned from /hcps")
    return hcps[0]["hcp_id"]


def main() -> None:
    print(f"\n{'='*60}")
    print("  Compliance Risk Investigator — API Smoke Test")
    print(f"{'='*60}\n")

    t_start = time.monotonic()

    with httpx.Client(timeout=60.0) as client:

        # 1. GET /health
        print("1. GET /health")
        resp = client.get(f"{_BASE}/health")
        _ok(resp, "health")

        # 2. GET /hcps
        print("\n2. GET /hcps")
        resp = client.get(f"{_BASE}/hcps", params={"limit": 5})
        data = _ok(resp, "hcps list")
        if data:
            print(f"     total={data.get('total')}  returned={len(data.get('hcps', []))}")

        # 3. GET /hcps  (filtered by tier)
        print("\n3. GET /hcps?tier=critical")
        resp = client.get(f"{_BASE}/hcps", params={"limit": 5, "tier": "critical"})
        data = _ok(resp, "hcps critical")
        if data:
            print(f"     critical count={data.get('total')}")

        # Pick a real HCP id for subsequent tests
        hcp_id = _pick_hcp(client)
        print(f"\n   Using HCP: {hcp_id}")

        # 4. GET /hcps/{hcp_id}
        print(f"\n4. GET /hcps/{hcp_id}")
        resp = client.get(f"{_BASE}/hcps/{hcp_id}")
        data = _ok(resp, "hcp detail")
        if data:
            print(f"     risk_score={data.get('risk_score')}  tier={data.get('risk_tier')}")

        # 5. GET /hcps/{hcp_id}/flags
        print(f"\n5. GET /hcps/{hcp_id}/flags")
        resp = client.get(f"{_BASE}/hcps/{hcp_id}/flags")
        data = _ok(resp, "hcp flags")
        if data:
            print(f"     fired_flags={data.get('fired_flags')}")

        # 6. GET /hcps/{hcp_id}/investigate  (agent — may return 503)
        print(f"\n6. GET /hcps/{hcp_id}/investigate  (agent endpoint)")
        resp = client.get(f"{_BASE}/hcps/{hcp_id}/investigate", timeout=600.0)
        data = _ok(resp, "investigate", allow=(503,))
        if resp.status_code == 503:
            print("     (agent not available — OPENAI_API_KEY not set)")
        elif data:
            print(f"     recommended_action={data.get('recommended_action')}")
            print(f"     confidence={data.get('confidence', 'n/a')}")

        # 7. GET /events
        print("\n7. GET /events")
        resp = client.get(f"{_BASE}/events", params={"limit": 5})
        data = _ok(resp, "events list")
        if data:
            print(f"     total={data.get('total')}")

        # 8. GET /monitoring  (agent — may return 503)
        print("\n8. GET /monitoring  (agent endpoint)")
        resp = client.get(f"{_BASE}/monitoring", timeout=600.0)
        data = _ok(resp, "monitoring", allow=(503,))
        if resp.status_code == 503:
            print("     (agent not available — OPENAI_API_KEY not set)")
        elif data:
            print(f"     total_hcps_in_scope={data.get('total_hcps_in_scope')}")

        # 9. POST /policy/query  (agent — may return 503)
        print("\n9. POST /policy/query  (agent endpoint)")
        payload = {"question": "What is the Nova Pharma meal limit for external events?"}
        resp = client.post(f"{_BASE}/policy/query", json=payload, timeout=600.0)
        data = _ok(resp, "policy query", allow=(503,))
        if resp.status_code == 503:
            print("     (agent not available — OPENAI_API_KEY not set)")
        elif data:
            print(f"     confidence={data.get('confidence')}")
            ans = data.get("answer", "")
            print(f"     answer[:120]={ans[:120]}")

        # 10. GET /benchmarks/{hcp_id}
        print(f"\n10. GET /benchmarks/{hcp_id}")
        resp = client.get(f"{_BASE}/benchmarks/{hcp_id}")
        data = _ok(resp, "benchmarks")
        if data:
            print(
                f"     peer_group={data.get('peer_group')}  "
                f"percentile={data.get('percentile_rank')}  "
                f"peer_count={data.get('peer_count')}"
            )

        # 11. 404 on unknown HCP
        print("\n11. GET /hcps/NONEXISTENT_HCP_ID  (expect 404)")
        resp = client.get(f"{_BASE}/hcps/NONEXISTENT_HCP_ID")
        if resp.status_code == 404:
            print("  OK   [404 check] status=404")
        else:
            print(f"  FAIL [404 check] expected 404, got {resp.status_code}")

    elapsed = time.monotonic() - t_start
    print(f"\n{'='*60}")
    print(f"  Smoke test completed in {elapsed:.1f}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
