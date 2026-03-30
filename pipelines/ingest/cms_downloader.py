"""
cms_downloader.py
-----------------
Downloads CMS Open Payments general payments data for:
  - NovaPharma Inc (anchor; real name: Supernus Pharmaceuticals, Inc.)
  - 4 competitor companies
  - Full HCP population (all HCPs ever paid by NovaPharma, from any company)

Outputs 9 Parquet files to S3:
  s3://{bucket}/raw/cms_open_payments/{year}/{layer}_payments_{year}.parquet

Usage:
  python pipelines/ingest/cms_downloader.py
"""

import io
import os
import time

import boto3
import pandas as pd
import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

TARGET_COMPANY_FILTER = "Supernus"
TARGET_COMPANY_DISPLAY_NAME = "NovaPharma Inc"

COMPETITOR_FILTERS = [
    "UCB",
    "Lundbeck",
    "Intra-Cellular Therapies",
    "Otsuka",
]

YEARS = [2022, 2023, 2024]

DATASET_IDS = {
    2022: "8e974948-5540-49d4-8e9c-a76b79c01d94",
    2023: "fb3a65aa-c901-4a38-a813-b04b00dfa2a9",
    2024: "direct_csv",
}

CMS_2024_CSV_URL = (
    "https://download.cms.gov/openpayments/PGYR2024_P06302025_06162025/"
    "OP_DTL_GNRL_PGYR2024_P06302025_06162025.csv"
)

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_PREFIX = "raw/cms_open_payments"

API_BASE = "https://openpaymentsdata.cms.gov/api/1/datastore/query"
PAGE_SIZE = 10_000
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# ── S3 client ─────────────────────────────────────────────────────────────────

s3 = boto3.client(
    "s3",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def stream_parquet_to_s3(df: pd.DataFrame, bucket: str, key: str) -> None:
    """
    Serialize a DataFrame to Parquet in memory and upload to S3.
    No temp files written to disk.
    """
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    logger.info(f"Uploaded s3://{bucket}/{key}  ({len(df):,} rows)")


def _get_with_retry(url: str, params: dict) -> dict:
    """GET request with up to MAX_RETRIES attempts on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"All {MAX_RETRIES} retries failed for {url}")


# ── API download (2022 & 2023) ────────────────────────────────────────────────

def download_via_api(dataset_id: str, company_filters: list[str]) -> pd.DataFrame:
    """
    Page through the CMS Open Payments API and return all matching rows
    for the given list of company name substrings.
    """
    all_rows = []
    url = f"{API_BASE}/{dataset_id}/0"

    for company_filter in company_filters:
        offset = 0
        logger.info(f"  Fetching '{company_filter}' from dataset {dataset_id[:8]}...")

        while True:
            params = {
                "conditions[0][property]": "applicable_manufacturer_or_applicable_gpo_making_payment_name",
                "conditions[0][value]": company_filter,
                "conditions[0][operator]": "contains",
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            data = _get_with_retry(url, params)
            results = data.get("results", [])
            if not results:
                break

            all_rows.extend(results)
            offset += len(results)
            logger.debug(f"    offset={offset:,}  fetched={len(results)}")

            if len(results) < PAGE_SIZE:
                break

    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


def download_population_via_api(
    dataset_id: str, hcp_ids: set, year: int
) -> pd.DataFrame:
    """
    Download ALL payments (from any company) to a specific set of HCP profile IDs.
    Pages through in batches of PAGE_SIZE per HCP ID.
    """
    all_rows = []
    url = f"{API_BASE}/{dataset_id}/0"
    hcp_list = list(hcp_ids)
    logger.info(f"  Fetching population layer for {len(hcp_list):,} HCPs  year={year}")

    for i, hcp_id in enumerate(hcp_list):
        if i % 500 == 0:
            logger.info(f"    Progress: {i:,}/{len(hcp_list):,} HCPs")
        offset = 0
        while True:
            params = {
                "conditions[0][property]": "physician_profile_id",
                "conditions[0][value]": hcp_id,
                "conditions[0][operator]": "=",
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            data = _get_with_retry(url, params)
            results = data.get("results", [])
            if not results:
                break
            all_rows.extend(results)
            offset += len(results)
            if len(results) < PAGE_SIZE:
                break

    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


# ── Direct CSV download (2024) ────────────────────────────────────────────────

def download_2024_direct(company_filters: list[str]) -> pd.DataFrame:
    """
    Stream the 2024 CMS CSV in chunks, filter to matching companies,
    and return a single DataFrame. Never loads the full CSV into memory.
    """
    col = "applicable_manufacturer_or_applicable_gpo_making_payment_name"
    chunks = []

    logger.info(f"  Streaming 2024 CSV from CMS (chunked, filter={company_filters})...")
    for chunk in pd.read_csv(
        CMS_2024_CSV_URL,
        chunksize=50_000,
        dtype=str,
        low_memory=False,
    ):
        chunk.columns = [c.lower() for c in chunk.columns]
        if col not in chunk.columns:
            continue
        mask = chunk[col].str.contains(
            "|".join(company_filters), case=False, na=False
        )
        filtered = chunk[mask]
        if not filtered.empty:
            chunks.append(filtered)

    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def download_2024_population_direct(hcp_ids: set) -> pd.DataFrame:
    """
    Stream the 2024 CSV in chunks, filter to rows matching known HCP IDs,
    and return combined DataFrame.
    """
    col = "physician_profile_id"
    chunks = []

    logger.info(f"  Streaming 2024 CSV for population ({len(hcp_ids):,} HCPs)...")
    for chunk in pd.read_csv(
        CMS_2024_CSV_URL,
        chunksize=50_000,
        dtype=str,
        low_memory=False,
    ):
        chunk.columns = [c.lower() for c in chunk.columns]
        if col not in chunk.columns:
            continue
        mask = chunk[col].isin(hcp_ids)
        filtered = chunk[mask]
        if not filtered.empty:
            chunks.append(filtered)

    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


# ── Layer builders ────────────────────────────────────────────────────────────

def build_target_layer(year: int) -> pd.DataFrame:
    logger.info(f"[{year}] Building TARGET layer ({TARGET_COMPANY_DISPLAY_NAME})")
    if year == 2024:
        df = download_2024_direct([TARGET_COMPANY_FILTER])
    else:
        df = download_via_api(DATASET_IDS[year], [TARGET_COMPANY_FILTER])
    logger.info(f"[{year}] TARGET: {len(df):,} rows")
    return df


def build_competitor_layer(year: int) -> pd.DataFrame:
    logger.info(f"[{year}] Building COMPETITOR layer")
    if year == 2024:
        df = download_2024_direct(COMPETITOR_FILTERS)
    else:
        df = download_via_api(DATASET_IDS[year], COMPETITOR_FILTERS)
    logger.info(f"[{year}] COMPETITOR: {len(df):,} rows")
    return df


def get_all_target_hcp_ids(target_dfs: dict[int, pd.DataFrame]) -> set:
    """
    Union of all physician_profile_id values across all years of target data.
    This is the HCP population NovaPharma ever touched (2022-2024).
    """
    hcp_ids = set()

    for year, df in target_dfs.items():
        if df.empty:
            continue
        col = next((c for c in df.columns if c.lower() == "physician_profile_id"), None)
        if col:
            ids = df[col].dropna().astype(str).unique()
            hcp_ids.update(ids)
            logger.info(f"  Year {year}: {len(ids):,} unique HCP IDs")

    logger.info(f"Total unique HCP IDs across all years: {len(hcp_ids):,}")
    return hcp_ids


def build_population_layer(year: int, hcp_ids: set) -> pd.DataFrame:
    logger.info(f"[{year}] Building POPULATION layer ({len(hcp_ids):,} HCPs)")
    if year == 2024:
        df = download_2024_population_direct(hcp_ids)
    else:
        df = download_population_via_api(DATASET_IDS[year], hcp_ids, year)
    logger.info(f"[{year}] POPULATION: {len(df):,} rows")
    return df


# ── Main orchestration ────────────────────────────────────────────────────────

def download_all_years() -> None:
    summary = []
    target_dfs: dict[int, pd.DataFrame] = {}

    # ── Pass 1: Target + Competitor layers ───────────────────────────────────
    for year in YEARS:
        # Target
        df_target = build_target_layer(year)
        target_dfs[year] = df_target
        key = f"{S3_PREFIX}/{year}/target_payments_{year}.parquet"
        stream_parquet_to_s3(df_target, S3_BUCKET, key)
        summary.append({"year": year, "layer": "target", "rows": len(df_target), "s3_key": key})

        # Competitor
        df_comp = build_competitor_layer(year)
        key = f"{S3_PREFIX}/{year}/competitor_payments_{year}.parquet"
        stream_parquet_to_s3(df_comp, S3_BUCKET, key)
        summary.append({"year": year, "layer": "competitor", "rows": len(df_comp), "s3_key": key})

    # ── Pass 2: Population layer (needs HCP IDs from all target years) ───────
    hcp_ids = get_all_target_hcp_ids(target_dfs)

    for year in YEARS:
        df_pop = build_population_layer(year, hcp_ids)
        key = f"{S3_PREFIX}/{year}/population_payments_{year}.parquet"
        stream_parquet_to_s3(df_pop, S3_BUCKET, key)
        summary.append({"year": year, "layer": "population", "rows": len(df_pop), "s3_key": key})

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("DOWNLOAD SUMMARY")
    logger.info("=" * 60)
    total = 0
    for row in sorted(summary, key=lambda r: (r["year"], r["layer"])):
        logger.info(
            f"  {row['year']} | {row['layer']:<12} | {row['rows']:>10,} rows"
            f" | s3://{S3_BUCKET}/{row['s3_key']}"
        )
        total += row["rows"]
    logger.info(f"\n  TOTAL: {total:,} rows across {len(summary)} files")
    logger.info("=" * 60)


def main() -> None:
    logger.info("Starting CMS Open Payments download")
    logger.info(f"Target: {TARGET_COMPANY_DISPLAY_NAME} (filter: '{TARGET_COMPANY_FILTER}')")
    logger.info(f"Competitors: {COMPETITOR_FILTERS}")
    logger.info(f"Years: {YEARS}")
    logger.info(f"S3 destination: s3://{S3_BUCKET}/{S3_PREFIX}/")
    download_all_years()
    logger.info("Done.")


if __name__ == "__main__":
    main()
