"""
streamlit_app/components/api_client.py — Synchronous HTTP client for the FastAPI backend.

All data in the dashboard flows through this module.
No parquet reads or DuckDB imports anywhere in this file.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import API_BASE_URL


class APIError(Exception):
    """Raised when the API returns a non-2xx response or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class APIClient:
    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get(self, endpoint: str, params: dict | None = None) -> dict | list:
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            resp = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.RequestError as exc:
            raise APIError(f"Network error reaching {url}: {exc}") from exc
        if resp.status_code != 200:
            raise APIError(
                f"GET {endpoint} returned {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()

    def get_agent(self, endpoint: str, params: dict | None = None) -> dict | list:
        """For agent endpoints that need longer timeout (120s)."""
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            response = httpx.get(url, params=params, timeout=180)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise APIError("Agent request timed out after 120s", 408) from exc
        except httpx.HTTPStatusError as exc:
            raise APIError(str(exc), exc.response.status_code) from exc
        except httpx.RequestError as exc:
            raise APIError(f"Network error reaching {url}: {exc}", 503) from exc

    def post(self, endpoint: str, json: dict | None = None) -> dict:
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            resp = httpx.post(url, json=json, timeout=self._timeout)
        except httpx.RequestError as exc:
            raise APIError(f"Network error reaching {url}: {exc}") from exc
        if resp.status_code not in (200, 201):
            raise APIError(
                f"POST {endpoint} returned {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()


# Module-level singleton
_client: APIClient | None = None


def get_client() -> APIClient:
    global _client
    if _client is None:
        _client = APIClient(base_url=API_BASE_URL, timeout=30)
    return _client
