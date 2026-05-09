# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
synthetic_generator.py
----------------------
Generates synthetic NovaPharma internal compliance data.
All data is clearly labeled synthetic and pseudonymized.

GUARDRAILS:
  - No real physician names — HCP_{profile_id} format only
  - No real company data — Nova Pharma Inc is fictional
  - All records carry synthetic_data_flag = True
  - Random seed = 42 for full reproducibility
  - hcp_violation_profile is an internal generation parameter only
    (never included in pipeline output data)

Outputs 4 Parquet files to S3:
  s3://{bucket}/synthetic/hcp_master/hcp_master.parquet
  s3://{bucket}/synthetic/hcp_interactions/hcp_interactions.parquet
  s3://{bucket}/synthetic/speaker_programs/speaker_program_events.parquet
  s3://{bucket}/synthetic/speaker_programs/speaker_program_attendees.parquet

Usage:
  python pipelines/ingest/synthetic_generator.py
"""

import io
import os
import tempfile
import time
from datetime import date, timedelta

import boto3
import numpy as np
import pandas as pd
from boto3.s3.transfer import TransferConfig
from dotenv import load_dotenv
from faker import Faker
from loguru import logger

from pipelines.business_rules_registry import get_rule

try:
    import awswrangler as wr
    _WR_AVAILABLE = True
except ImportError:
    wr = None  # type: ignore[assignment]
    _WR_AVAILABLE = False

load_dotenv()

# ── Reproducibility ───────────────────────────────────────────────────────────
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)
fake = Faker()
Faker.seed(RANDOM_SEED)

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_COMPANY = "Nova Pharma Inc"
TARGET_FILTER  = "takeda"

# Drug name mapping: real Takeda CMS names → fictional Nova Pharma names.
# Used when assigning product_discussed in interaction records.
# Real drug names never appear in any synthetic output.
TAKEDA_DRUG_MAPPING = {
    # Gastroenterology
    "ENTYVIO":    "Entavex",
    "VONVENDI":   "Colvance",
    "GATTEX":     "Entavex",
    "NATPARA":    "Colvance",
    # Neuroscience
    "TRINTELLIX": "Neurospan",
    "VYVANSE":    "Cognivex",
    "ADDERALL":   "Cognivex",
    # Oncology
    "NINLARO":    "Oncivance",
    "VELCADE":    "Hematrix",
    "ICLUSIG":    "Hematrix",
    "ALUNBRIG":   "Oncivance",
    # Rare Disease
    "TAKHZYRO":   "Rarevance",
    "ADVATE":     "Orphagen",
    "ADYNOVATE":  "Orphagen",
    "FEIBA":      "Rarevance",
    "HEMOFIL":    "Orphagen",
}

NOVA_PHARMA_PRODUCTS = sorted(set(TAKEDA_DRUG_MAPPING.values()))
# ['Cognivex', 'Colvance', 'Entavex', 'Hematrix',
#  'Neurospan', 'Oncivance', 'Orphagen', 'Rarevance']

YEARS = [2022, 2023, 2024]

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_SYNTHETIC_PREFIX = "synthetic"

CMS_S3_KEYS = {
    2022: "raw/cms_open_payments/2022/OP_DTL_GNRL_PGYR2022.csv",
    2023: "raw/cms_open_payments/2023/OP_DTL_GNRL_PGYR2023.csv",
    2024: "raw/cms_open_payments/2024/OP_DTL_GNRL_PGYR2024.csv",
}

# Local cache of aggregated CMS HCP totals.
# Populated on first run (downloads ~23 GB from S3 via multipart transfer).
# All subsequent runs load this file in <1 second.
# Delete this file to force a full refresh from S3.
CMS_TOTALS_CACHE = "data/processed/cms_hcp_totals_cache.parquet"

# Local cache for Athena-queried CMS speaker fee totals (v2 generator).
# Populated by load_cms_speaker_totals() on first v2 run.
CMS_SPEAKER_TOTALS_CACHE = "data/processed/cms_speaker_totals_cache.parquet"

ANNUAL_COMPENSATION_CAP = 75_000
RECONCILIATION_TOLERANCE = 0.05

HCP_VIOLATION_PROFILES = {
    "clean":    0.70,
    "minor":    0.20,
    "moderate": 0.06,
    "serious":  0.04,
}

# FMV rate card: per-engagement ceiling by specialty and geographic tier.
# Rate IS the ceiling — no tolerance band.
NOVA_PHARMA_FMV_RATE_CARD = {
    "Gastroenterology": {"local": 1000, "regional": 2000, "national": 3500},
    "Neurology":        {"local": 1000, "regional": 2000, "national": 3500},
    "Oncology":         {"local": 1200, "regional": 2500, "national": 4500},
    "Rare Disease":     {"local": 1500, "regional": 3000, "national": 5500},
    "Inflammation":     {"local":  750, "regional": 1500, "national": 2500},
    "Other":            {"local":  750, "regional": 1500, "national": 2500},
}

SPECIALTY_DISTRIBUTION = {
    "Gastroenterology": 0.30,
    "Oncology":         0.25,
    "Neurology":        0.20,
    "Rare Disease":     0.15,
    "Inflammation":     0.10,
}

MEAL_TYPES = ["breakfast", "lunch", "dinner"]
MEAL_TYPE_DISTRIBUTION = {"breakfast": 0.10, "lunch": 0.70, "dinner": 0.20}
MEAL_LIMITS = {
    "breakfast": int(get_rule("MEAL_001")["effective_threshold"]),
    "lunch":     int(get_rule("MEAL_002")["effective_threshold"]),
    "dinner":    int(get_rule("MEAL_003")["effective_threshold"]),
}

INTERACTION_TYPE_WEIGHTS = {
    "meal":           0.40,
    "education":      0.25,
    "consulting":     0.15,
    "advisory_board": 0.10,
    "conference":     0.10,
}

TERRITORIES = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]

NORMAL_RATIONALE_TEMPLATES = [
    "Reviewed updated prescribing guidelines for {specialty} patients",
    "Discussed clinical trial results for {product}",
    "Provided disease state education on {condition}",
    "Discussed patient case studies and outcomes",
    "Presented real-world evidence data for {product}",
    "Advisory input on patient support programs",
    "Reviewed safety profile updates for {product}",
]

VAGUE_RATIONALE_VALUES = ["", "N/A", "Other", "Meeting", "Discussion"]

CONDITIONS = {
    "Gastroenterology": ["Crohn's disease", "ulcerative colitis", "short bowel syndrome"],
    "Oncology":         ["multiple myeloma", "CML", "ALK-positive NSCLC"],
    "Neurology":        ["epilepsy", "migraine", "Parkinson's disease"],
    "Rare Disease":     ["hereditary angioedema", "hemophilia A", "hemophilia B"],
    "Inflammation":     ["rheumatoid arthritis", "psoriasis", "inflammatory bowel disease"],
    "Other":            ["chronic pain", "inflammation", "metabolic syndrome"],
}

PROGRAM_TOPICS = [
    "Entavex: Disease State Education",
    "Entavex: Clinical Trial Results",
    "Colvance: Patient Case Studies",
    "Colvance: Dosing and Administration",
    "Neurospan: Mechanism of Action",
    "Neurospan: Real World Evidence",
    "Cognivex: Prescribing Guidelines",
    "Oncivance: Treatment Algorithm",
    "Oncivance: Safety Profile Update",
    "Hematrix: Clinical Outcomes",
    "Rarevance: Patient Identification",
    "Orphagen: Disease Management",
]

VENUE_DISTRIBUTIONS = {
    "clean":    {"office": 0.60, "hospital": 0.30, "hotel": 0.10},
    "minor":    {"office": 0.50, "hospital": 0.20, "hotel": 0.20, "restaurant": 0.10},
    "moderate": {"office": 0.30, "hospital": 0.10, "hotel": 0.20,
                 "restaurant": 0.30, "entertainment_venue": 0.10},
    "serious":  {"restaurant": 0.50, "entertainment_venue": 0.30, "luxury_resort": 0.20},
}

# ── V2 Interactions Algorithm Constants ──────────────────────────────────────
# Category totals below this threshold are too small to generate a meaningful
# interaction record without inventing implausible micro-payments.
MIN_PLAUSIBLE_CATEGORY_CMS = 10

# Target dollars per event by interaction type (used to derive n_events).
PER_EVENT_TARGETS_BY_CATEGORY = {
    "meal":       50,
    "consulting": 500,
    "education":  200,
    "travel":     300,
}

# Dirichlet concentration by compliance profile (shared with speaker v2 intent).
INTERACTION_ALPHA_BY_PROFILE = {
    "clean":    2.0,
    "minor":    1.5,
    "moderate": 1.0,
    "serious":  0.6,
}

# Monthly interaction frequency estimate by profile (for tier-aware rep selection).
# Used to classify HCPs as low-touch/regular/KOL when selecting reps.
PROFILE_MONTHLY_FREQUENCY = {
    "clean":    0.15,   # ~5 interactions over 3 years
    "minor":    0.5,    # ~18 interactions over 3 years
    "moderate": 1.0,    # ~36 interactions over 3 years
    "serious":  2.0,    # ~72 interactions over 3 years (KOL territory)
}

# Hard cap on events per category per HCP per year.
MAX_EVENTS_BY_CATEGORY = {
    "meal":       30,
    "consulting": 20,
    "education":  20,
    "travel":     20,
}

# CMS nature_of_payment values → internal interaction_type (full mapping, used by v1)
CMS_TO_INTERACTION_TYPE = {
    "Food and Beverage":  "meal",
    "Consulting Fee":     "consulting",
    "Education":          "education",
    "Travel and Lodging": "travel",
}

# V2 interactions: meals and travel are handled by allocate_meals_and_travel, not here
CMS_TO_INTERACTION_TYPE_V2 = {
    "Consulting Fee": "consulting",
    "Education":      "education",
}

# US state abbreviations used to assign remote venue_state for travel events
_US_STATE_ABBRS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# Local cache for Athena-queried CMS per-category totals (v2 interactions generator).
CMS_CATEGORY_TOTALS_CACHE = "data/processed/cms_category_totals_cache.parquet"

# ── V2 Speaker Algorithm Constants ────────────────────────────────────────────
# CMS speaker totals below this threshold are too small to plausibly represent
# a real speaker engagement (the HCP would have been grossly under-reported).
MIN_PLAUSIBLE_SPEAKER_CMS  = 1_000

# Target dollars per speaker event when deriving n_events from the CMS total.
REALISTIC_PER_EVENT_TARGET = 2_000

# Hard cap on events per HCP per year (prevents extreme fragmentation for very
# high-CMS HCPs and keeps total_program_cost per event realistic).
MAX_PLAUSIBLE_EVENTS       = 24

# Dirichlet concentration parameter by compliance profile.
# Higher alpha → more uniform fee splits (clean speakers get consistent fees).
# Lower alpha → more concentrated splits (serious speakers have one dominant event).
SPEAKER_ALPHA_BY_PROFILE = {
    "clean":    2.0,
    "minor":    1.5,
    "moderate": 1.0,
    "serious":  0.6,
}

# Repeat-speaker threshold from business rules registry (SPEAKER_002 = 6 events/year).
# Used to set the repeat_speaker flag on generated events.
REPEAT_SPEAKER_THRESHOLD = int(get_rule("SPEAKER_002")["effective_threshold"])

# ── AWS client ────────────────────────────────────────────────────────────────
s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


# ── Utility helpers ───────────────────────────────────────────────────────────

def save_to_s3(df: pd.DataFrame, bucket: str, key: str) -> None:
    """Serialize DataFrame to Parquet in memory and upload to S3."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    size_mb = buf.tell() / 1024 ** 2
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    logger.info(f"  Saved s3://{bucket}/{key}  ({len(df):,} rows, {size_mb:.1f} MB)")


def _random_business_date(year: int, rng: np.random.Generator) -> date:
    """
    Return a realistic business date — skewed toward weekdays,
    with lower probability in August and December.
    """
    start = date(year, 1, 1)
    total_days = 366 if year % 4 == 0 else 365
    month_weight = {m: 0.5 if m in (8, 12) else 1.0 for m in range(1, 13)}
    for _ in range(100):
        d = start + timedelta(days=int(rng.integers(0, total_days)))
        if d.weekday() < 5 and rng.random() < month_weight[d.month]:
            return d
    return start + timedelta(days=int(rng.integers(0, total_days)))


def _get_fmv_benchmark(specialty: str, tier: str) -> float:
    spec = NOVA_PHARMA_FMV_RATE_CARD.get(specialty, NOVA_PHARMA_FMV_RATE_CARD["Other"])
    return float(spec[tier])


def _severity_from_types(violation_types: list[str]) -> str:
    HIGH = {
        "SPEAKER_VENUE_INAPPROPRIATE", "SPEAKER_FEE_EXCEEDS_FMV",
        "SPEAKER_SELECTED_BY_PRESCRIBING", "CMS_RECONCILIATION_GAP",
        "NON_HCP_ATTENDEES", "ANNUAL_COMPENSATION_CAP_EXCEEDED",
        "ATTENDEE_SAME_OFFICE_AS_SPEAKER",
    }
    MEDIUM = {
        "MEAL_COST_EXCESSIVE", "REPEAT_PROGRAM_ATTENDANCE", "LOW_ATTENDEE_COUNT",
        "ALCOHOL_PROVIDED", "RAPID_INTERACTION_PATTERN", "REPEAT_SAME_TOPIC_PROGRAMS",
        "SPEAKER_RAPID_REPEAT",
    }
    if not violation_types:
        return "none"
    s = set(violation_types)
    if s & HIGH:
        return "high"
    if s & MEDIUM:
        return "medium"
    return "low"


# ── Step 1: Load CMS HCP totals ───────────────────────────────────────────────

def _stream_year_filtered(year: int, key: str) -> pd.DataFrame:
    """
    Download one CMS CSV from S3 to a temp file using multipart transfer
    (100 MB parts, 5 concurrent connections), then parse and filter in chunks.

    Multipart download retries individual parts on failure — unlike streaming
    via get_object which must restart the entire file on any connection drop.
    Temp file is deleted after parsing regardless of success or failure.
    """
    config = TransferConfig(
        multipart_threshold=100 * 1024 * 1024,  # 100 MB
        multipart_chunksize=100 * 1024 * 1024,
        max_concurrency=5,
        use_threads=True,
    )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        logger.info(f"  [{year}] Downloading to temp file: {tmp_path}")
        s3.download_file(S3_BUCKET, key, tmp_path, Config=config)
        logger.info(f"  [{year}] Download complete — parsing...")

        year_chunks = []
        col = "applicable_manufacturer_or_applicable_gpo_making_payment_name"
        for chunk in pd.read_csv(tmp_path, chunksize=50_000, dtype=str, low_memory=False):
            chunk.columns = [c.lower() for c in chunk.columns]
            if col not in chunk.columns:
                continue
            mask = chunk[col].str.contains(TARGET_FILTER, case=False, na=False)
            filtered = chunk[mask].copy()
            if not filtered.empty:
                filtered["_year"] = year
                year_chunks.append(filtered)

        return pd.concat(year_chunks, ignore_index=True) if year_chunks else pd.DataFrame()

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
            logger.info(f"  [{year}] Temp file deleted")


def load_cms_hcp_totals() -> pd.DataFrame:
    """
    Download CMS CSVs from S3 via multipart transfer, filter for target company
    payments, and aggregate totals per HCP per year.

    Uses per-year parquet caches so a failure on year N doesn't require
    re-downloading years already completed. Delete the year cache files to
    force a fresh download.

    Returns one row per HCP with cms_total_{year} columns.
    """
    if os.path.exists(CMS_TOTALS_CACHE):
        logger.info(f"Loading CMS HCP totals from local cache: {CMS_TOTALS_CACHE}")
        df = pd.read_parquet(CMS_TOTALS_CACHE)
        logger.info(f"HCP universe: {len(df):,} unique HCPs (cached)")
        return df

    year_cache_dir = os.path.join(os.path.dirname(CMS_TOTALS_CACHE), "cms_year_cache")
    os.makedirs(year_cache_dir, exist_ok=True)

    logger.info(f"Cache not found — downloading from S3 via multipart transfer...")
    all_frames = []

    for year, key in CMS_S3_KEYS.items():
        year_cache = os.path.join(year_cache_dir, f"cms_{year}.parquet")

        if os.path.exists(year_cache):
            logger.info(f"  [{year}] Loading from year cache: {year_cache}")
            ydf = pd.read_parquet(year_cache)
        else:
            logger.info(f"  [{year}] s3://{S3_BUCKET}/{key}")
            ydf = _stream_year_filtered(year, key)
            if not ydf.empty:
                ydf.to_parquet(year_cache, index=False)
                logger.info(f"  [{year}] Year cache saved: {year_cache}")

        if not ydf.empty:
            logger.info(f"  [{year}] {len(ydf):,} {TARGET_FILTER} rows")
            all_frames.append(ydf)

    if not all_frames:
        raise RuntimeError(f"No {TARGET_FILTER} rows found — check TARGET_FILTER and S3 paths")

    raw = pd.concat(all_frames, ignore_index=True)

    # Rename CMS columns to stable internal names (all columns already lowercased in chunks).
    # Must rename before computing _payment so the column is found reliably.
    raw = raw.rename(columns={
        "covered_recipient_profile_id":      "physician_profile_id",
        "covered_recipient_specialty_1":     "physician_specialty",
        "total_amount_of_payment_usdollars": "_payment_raw",
    })
    raw["_payment"] = pd.to_numeric(raw["_payment_raw"], errors="coerce").fillna(0)

    # First-seen metadata per HCP
    meta = (
        raw.groupby("physician_profile_id")
        .agg(
            specialty=("physician_specialty", "first"),
            state=("recipient_state", "first"),
            city=("recipient_city", "first"),
        )
        .reset_index()
    )

    # Annual totals — pivot year → column
    yearly = (
        raw.groupby(["physician_profile_id", "_year"])["_payment"]
        .sum()
        .unstack(fill_value=0.0)
        .reset_index()
    )
    yearly.columns = [
        "physician_profile_id" if c == "physician_profile_id" else f"cms_total_{int(c)}"
        for c in yearly.columns
    ]
    for yr in YEARS:
        if f"cms_total_{yr}" not in yearly.columns:
            yearly[f"cms_total_{yr}"] = 0.0

    totals = meta.merge(yearly, on="physician_profile_id", how="left")
    totals["cms_total_all_years"] = sum(
        totals[f"cms_total_{yr}"].fillna(0) for yr in YEARS
    )

    os.makedirs(os.path.dirname(CMS_TOTALS_CACHE), exist_ok=True)
    totals.to_parquet(CMS_TOTALS_CACHE, index=False)
    logger.info(f"Cache saved to {CMS_TOTALS_CACHE}")
    logger.info(f"HCP universe: {len(totals):,} unique HCPs")
    return totals


# ── Step 1b: Load CMS speaker fee totals (v2) ─────────────────────────────────

def load_cms_speaker_totals() -> "pd.DataFrame | None":
    """
    Query Athena for CMS-reported speaker fee totals per HCP per year.

    Filters to Takeda payments where nature_of_payment contains 'faculty'/'speaker',
    grouping by covered_recipient_profile_id and program_year.

    Returns a DataFrame with columns:
        covered_recipient_profile_id (str), program_year (int), speaker_total (float)

    Returns None if awswrangler is unavailable or Athena is unreachable.
    Caches result to CMS_SPEAKER_TOTALS_CACHE on success; reads cache on subsequent calls.
    """
    if os.path.exists(CMS_SPEAKER_TOTALS_CACHE):
        logger.info(f"Loading CMS speaker totals from cache: {CMS_SPEAKER_TOTALS_CACHE}")
        df = pd.read_parquet(CMS_SPEAKER_TOTALS_CACHE)
        logger.info(f"CMS speaker totals: {len(df):,} rows (cached)")
        return df

    if not _WR_AVAILABLE:
        logger.warning(
            "awswrangler not installed — cannot load CMS speaker totals from Athena. "
            "Install with: pip install awswrangler"
        )
        return None

    athena_db     = os.environ.get("ATHENA_DATABASE",    "compliance_risk_raw")
    athena_bucket = os.environ.get("ATHENA_S3_BUCKET",   "s3://compliance-risk-investigator/athena-query-output/")

    try:
        logger.info("Querying Athena: CMS speaker fee totals (nature_of_payment LIKE '%faculty%speaker%')")
        df = wr.athena.read_sql_query(
            sql="""
                SELECT
                    covered_recipient_profile_id,
                    program_year,
                    SUM(total_amount_of_payment_usdollars) AS speaker_total
                FROM compliance_risk_raw.cms_open_payments
                WHERE LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
                          LIKE '%takeda%'
                  AND covered_recipient_type IN (
                          'Covered Recipient Physician',
                          'Covered Recipient Non-Physician Practitioner'
                      )
                  AND covered_recipient_profile_id IS NOT NULL
                  AND nature_of_payment_or_transfer_of_value LIKE '%faculty%speaker%'
                GROUP BY covered_recipient_profile_id, program_year
            """,
            database=athena_db,
            s3_output=athena_bucket,
            boto3_session=None,
        )
        df["covered_recipient_profile_id"] = df["covered_recipient_profile_id"].astype(str)
        df["program_year"]  = pd.to_numeric(df["program_year"],  errors="coerce").astype("Int64")
        df["speaker_total"] = pd.to_numeric(df["speaker_total"], errors="coerce").fillna(0.0)

        os.makedirs(os.path.dirname(CMS_SPEAKER_TOTALS_CACHE), exist_ok=True)
        df.to_parquet(CMS_SPEAKER_TOTALS_CACHE, index=False)
        logger.info(f"CMS speaker totals: {len(df):,} rows — cached to {CMS_SPEAKER_TOTALS_CACHE}")
        return df

    except Exception as exc:
        logger.warning(
            f"Athena speaker totals query failed: {exc} — "
            "v2 speaker generation will return an empty DataFrame"
        )
        return None


# ── Step 2: Generate HCP Master ───────────────────────────────────────────────

def generate_hcp_master(cms_totals: pd.DataFrame) -> pd.DataFrame:
    """
    One row per HCP. Assigns synthetic attributes and violation profile.
    hcp_violation_profile controls downstream distributions — it is an
    internal generation parameter and is NOT included in pipeline data.
    """
    logger.info("Generating HCP master...")
    rng = np.random.default_rng(RANDOM_SEED)
    n = len(cms_totals)

    specialties = list(SPECIALTY_DISTRIBUTION.keys())
    spec_probs = list(SPECIALTY_DISTRIBUTION.values())
    tiers = ["local", "regional", "national"]
    tier_probs = [0.60, 0.30, 0.10]
    profiles = list(HCP_VIOLATION_PROFILES.keys())
    profile_probs = list(HCP_VIOLATION_PROFILES.values())

    assigned_specs = rng.choice(specialties, size=n, p=spec_probs)
    assigned_tiers = rng.choice(tiers, size=n, p=tier_probs)
    assigned_profiles = rng.choice(profiles, size=n, p=profile_probs)
    is_kol_arr = rng.random(n) < 0.05

    state_counters: dict[str, int] = {}
    records = []

    for i, row in enumerate(cms_totals.itertuples(index=False)):
        pid = str(row.physician_profile_id)

        raw_spec = str(row.specialty) if pd.notna(row.specialty) else ""
        specialty = next(
            (s for s in specialties if s.lower() in raw_spec.lower()),
            assigned_specs[i],
        )

        state = str(row.state) if pd.notna(row.state) else fake.state_abbr()
        city = str(row.city) if pd.notna(row.city) else fake.city()
        state_counters[state] = state_counters.get(state, 0) + 1
        practice_id = f"PRAC_{state}_{state_counters[state]:04d}"

        hcp_type = rng.choice(
            ["physician", "nurse_practitioner", "physician_assistant"],
            p=[0.75, 0.15, 0.10],
        )

        records.append({
            "hcp_id": f"HCP_{pid}",
            "physician_profile_id": pid,
            "specialty": specialty,
            "sub_specialty": fake.job(),
            "state": state,
            "practice_id": practice_id,
            "practice_city": city,
            "fmv_tier": assigned_tiers[i],
            "hcp_type": hcp_type,
            "is_kol": bool(is_kol_arr[i]),
            "hcp_violation_profile": assigned_profiles[i],  # internal only
            "cms_total_2022": float(getattr(row, "cms_total_2022", 0) or 0),
            "cms_total_2023": float(getattr(row, "cms_total_2023", 0) or 0),
            "cms_total_2024": float(getattr(row, "cms_total_2024", 0) or 0),
            "cms_total_all_years": float(row.cms_total_all_years or 0),
            "synthetic_data_flag": True,
        })

    df = pd.DataFrame(records)
    profile_counts = df["hcp_violation_profile"].value_counts()
    logger.info(f"HCP master: {len(df):,} records")
    for p in profiles:
        logger.info(f"  {p}: {profile_counts.get(p, 0):,} ({100*profile_counts.get(p,0)/len(df):.1f}%)")
    return df


# ── Step 3: Generate HCP Interactions ─────────────────────────────────────────

def generate_hcp_interactions(
    hcp_master: pd.DataFrame, cms_totals: pd.DataFrame
) -> pd.DataFrame:
    """
    30,000+ records. Payment amounts reconcile to CMS totals per HCP per year
    within ±5% tolerance. 5% of serious-profile HCPs get a deliberate >10% gap
    which becomes a TYPE 11 (CMS_RECONCILIATION_GAP) violation.
    """
    logger.info("Generating HCP interactions...")
    rng = np.random.default_rng(RANDOM_SEED + 1)

    interactions_range = {
        "clean":    (3, 8),
        "minor":    (4, 10),
        "moderate": (6, 14),
        "serious":  (8, 18),
    }
    itypes = list(INTERACTION_TYPE_WEIGHTS.keys())
    iprobs = list(INTERACTION_TYPE_WEIGHTS.values())
    primary_rep, rep_territory_map, state_panels, anomaly_hcps = _build_rep_panel_and_assignments(hcp_master, rng)
    rep_ids = [f"REP_{i:04d}" for i in range(1, 501)]
    rep_territory = rep_territory_map

    records = []
    seq = 0

    for _, hcp in hcp_master.iterrows():
        profile = hcp["hcp_violation_profile"]
        specialty = hcp["specialty"]
        lo, hi = interactions_range[profile]

        for year in YEARS:
            cms_total = float(hcp[f"cms_total_{year}"] or 0)
            if cms_total == 0:
                continue  # no CMS payments this year — generate nothing
            n = int(rng.integers(lo, hi + 1))
            if n == 0:
                continue

            is_recon_anomaly = (profile == "serious" and rng.random() < 0.05)
            if is_recon_anomaly:
                target = cms_total * rng.uniform(0.80, 0.88)
            else:
                target = cms_total * rng.uniform(
                    1 - RECONCILIATION_TOLERANCE,
                    1 + RECONCILIATION_TOLERANCE,
                )

            if target > 0:
                splits = rng.dirichlet(np.ones(n))
                amounts = (splits * target).round(2)
            else:
                amounts = np.zeros(n)

            for idx in range(n):
                seq += 1
                itype = rng.choice(itypes, p=iprobs)
                idate = _random_business_date(year, rng)
                rep = rng.choice(rep_ids)
                product = rng.choice(NOVA_PHARMA_PRODUCTS)
                condition = rng.choice(CONDITIONS.get(specialty, CONDITIONS["Other"]))

                if itype == "meal":
                    meal_type = str(rng.choice(
                        MEAL_TYPES,
                        p=[MEAL_TYPE_DISTRIBUTION[m] for m in MEAL_TYPES],
                    ))
                    limit = MEAL_LIMITS[meal_type]
                    if profile == "clean":
                        meal_cost = round(float(rng.uniform(limit * 0.30, limit * 0.75)), 2)
                    elif profile == "minor":
                        if rng.random() < 0.80:
                            meal_cost = round(float(rng.uniform(limit * 0.50, limit * 0.95)), 2)
                        else:
                            meal_cost = round(float(rng.uniform(limit * 1.05, limit * 1.40)), 2)
                    elif profile == "moderate":
                        if rng.random() < 0.50:
                            meal_cost = round(float(rng.uniform(limit * 0.60, limit * 0.95)), 2)
                        else:
                            meal_cost = round(float(rng.uniform(limit * 1.10, limit * 1.80)), 2)
                    else:  # serious
                        if rng.random() < 0.15:
                            meal_cost = round(float(rng.uniform(limit * 0.50, limit * 0.95)), 2)
                        else:
                            meal_cost = round(float(rng.uniform(limit * 1.20, limit * 2.50)), 2)
                else:
                    meal_type = None
                    meal_cost = None

                fmv_bench = _get_fmv_benchmark(specialty, hcp["fmv_tier"])
                if itype in ("consulting", "advisory_board"):
                    multiplier = {
                        "clean":    rng.uniform(0.80, 1.00),
                        "minor":    rng.uniform(0.90, 1.10),
                        "moderate": rng.uniform(1.10, 1.30),
                        "serious":  rng.uniform(1.50, 2.00),
                    }[profile]
                    fmv_rate_used = round(fmv_bench * float(multiplier), 2)
                    fmv_approved = fmv_rate_used <= fmv_bench
                else:
                    fmv_rate_used = None
                    fmv_approved = True

                alcohol = {
                    "clean":    False,
                    "minor":    False,
                    "moderate": bool(rng.random() < 0.05),
                    "serious":  bool(rng.random() < 0.20),
                }[profile]

                vague_prob = {"clean": 0.0, "minor": 0.05, "moderate": 0.30, "serious": 0.60}[profile]
                if rng.random() < vague_prob:
                    rationale = str(rng.choice(VAGUE_RATIONALE_VALUES))
                else:
                    tmpl = rng.choice(NORMAL_RATIONALE_TEMPLATES)
                    rationale = tmpl.format(
                        specialty=specialty, product=product, condition=condition
                    )

                amount = round(float(amounts[idx]), 2)

                records.append({
                    "interaction_id": f"INT-{year}-{seq:07d}",
                    "hcp_id": hcp["hcp_id"],
                    "interaction_date": str(idate),
                    "interaction_type": itype,
                    "rep_id": rep,
                    "rep_territory": str(rep_territory[rep]),
                    "product_discussed": product,
                    "interaction_city": hcp["practice_city"],
                    "interaction_state": hcp["state"],
                    "practice_id": hcp["practice_id"],
                    "practice_city": hcp["practice_city"],
                    "meal_type": meal_type,
                    "meal_cost": meal_cost,
                    "attendee_count": int(rng.integers(1, 16)),
                    "fmv_rate_used": fmv_rate_used,
                    "fmv_tier": hcp["fmv_tier"],
                    "fmv_benchmark": fmv_bench,
                    "fmv_approved": fmv_approved,
                    "alcohol_provided": alcohol,
                    "payment_amount": amount,
                    "annual_total_ytd": 0.0,   # calculated post-build below
                    "compliance_reviewed": bool(rng.random() > 0.10),
                    "compliance_flag": "none",
                    "business_rationale": rationale,
                    "cms_total_this_year": cms_total,
                    "is_reconciliation_anomaly": False,  # recalculated post-build below
                    "program_year": year,
                    "synthetic_data_flag": True,
                    "violation_types": "",
                    "violation_severity": "none",
                    "is_violation": False,
                })

    df = pd.DataFrame(records)

    # FIX 3: annual_total_ytd — cumulative sum per HCP per year, ordered by date
    df = df.sort_values(["hcp_id", "program_year", "interaction_date"]).reset_index(drop=True)
    df["annual_total_ytd"] = (
        df.groupby(["hcp_id", "program_year"])["payment_amount"].cumsum().round(2)
    )

    # FIX 5: is_reconciliation_anomaly — actual payment sums vs CMS total (>10% gap)
    hcp_year_sums = (
        df.groupby(["hcp_id", "program_year"])["payment_amount"]
        .sum()
        .reset_index(name="_sum")
    )
    df = df.merge(hcp_year_sums, on=["hcp_id", "program_year"], how="left")
    df["is_reconciliation_anomaly"] = df.apply(
        lambda r: (
            abs(r["_sum"] - r["cms_total_this_year"]) / r["cms_total_this_year"] > 0.10
            if r["cms_total_this_year"] > 0 else False
        ),
        axis=1,
    )
    df = df.drop(columns=["_sum"])

    logger.info(f"HCP interactions: {len(df):,} records")
    return df


# ── Step 3b: Load CMS per-category totals + Generate Interactions v2 ──────────

def load_cms_category_totals() -> "pd.DataFrame | None":
    """
    Query Athena for CMS payment totals broken down by nature_of_payment category
    per HCP per year.

    Filters to Takeda payments in the four categories that map to internal
    interaction types: Food and Beverage, Consulting Fee, Education, Travel and
    Lodging.

    Returns a DataFrame with columns:
        covered_recipient_profile_id (str), program_year (int),
        cms_category (str), category_total (float)

    Returns None if awswrangler is unavailable or Athena is unreachable.
    Caches result to CMS_CATEGORY_TOTALS_CACHE on success.
    """
    if os.path.exists(CMS_CATEGORY_TOTALS_CACHE):
        logger.info(f"Loading CMS category totals from cache: {CMS_CATEGORY_TOTALS_CACHE}")
        df = pd.read_parquet(CMS_CATEGORY_TOTALS_CACHE)
        logger.info(f"CMS category totals: {len(df):,} rows (cached)")
        return df

    if not _WR_AVAILABLE:
        logger.warning(
            "awswrangler not installed — cannot load CMS category totals from Athena. "
            "Install with: pip install awswrangler"
        )
        return None

    athena_db     = os.environ.get("ATHENA_DATABASE",    "compliance_risk_raw")
    athena_bucket = os.environ.get("ATHENA_S3_BUCKET",   "s3://compliance-risk-investigator/athena-query-output/")

    try:
        logger.info("Querying Athena: CMS per-category totals (Food/Consulting/Education/Travel)")
        df = wr.athena.read_sql_query(
            sql="""
                SELECT
                    CAST(covered_recipient_profile_id AS VARCHAR) AS covered_recipient_profile_id,
                    program_year,
                    nature_of_payment_or_transfer_of_value AS cms_category,
                    SUM(total_amount_of_payment_usdollars) AS category_total
                FROM compliance_risk_raw.cms_open_payments
                WHERE LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
                          LIKE '%takeda%'
                  AND covered_recipient_type IN (
                          'Covered Recipient Physician',
                          'Covered Recipient Non-Physician Practitioner'
                      )
                  AND covered_recipient_profile_id IS NOT NULL
                  AND nature_of_payment_or_transfer_of_value IN (
                          'Food and Beverage',
                          'Consulting Fee',
                          'Education',
                          'Travel and Lodging'
                      )
                GROUP BY covered_recipient_profile_id, program_year,
                         nature_of_payment_or_transfer_of_value
            """,
            database=athena_db,
            s3_output=athena_bucket,
            boto3_session=None,
        )
        df["covered_recipient_profile_id"] = df["covered_recipient_profile_id"].astype(str)
        df["program_year"]    = pd.to_numeric(df["program_year"],    errors="coerce").astype("Int64")
        df["category_total"]  = pd.to_numeric(df["category_total"],  errors="coerce").fillna(0.0)

        os.makedirs(os.path.dirname(CMS_CATEGORY_TOTALS_CACHE), exist_ok=True)
        df.to_parquet(CMS_CATEGORY_TOTALS_CACHE, index=False)
        logger.info(f"CMS category totals: {len(df):,} rows — cached to {CMS_CATEGORY_TOTALS_CACHE}")
        return df

    except Exception as exc:
        logger.warning(
            f"Athena category totals query failed: {exc} — "
            "v2 interactions generation will return an empty DataFrame"
        )
        return None



def _build_rep_panel_and_assignments(hcp_master, rng):
    """Build state-aware rep panels and primary rep per HCP.

    Design:
      - 500 reps assigned randomly to 5 territories
      - Each state has a rep panel sized to HCP density (min 2, ~1 per 200 HCPs)
      - Each HCP gets a deterministic primary rep from their state panel
      - 2% of HCPs flagged as rep-hopping anomaly candidates

    Returns (primary_rep_dict, rep_territory_dict, state_panels_dict, anomaly_hcps_set)
    """
    rep_ids = [f"REP_{i:04d}" for i in range(1, 501)]
    rep_territory = {r: str(rng.choice(TERRITORIES)) for r in rep_ids}

    state_counts = hcp_master.groupby("state").size()
    state_panels = {}
    rep_cursor = 0
    for state, n_hcps in state_counts.items():
        panel_size = max(2, (int(n_hcps) + 199) // 200)
        panel = []
        for _ in range(panel_size):
            panel.append(rep_ids[rep_cursor % len(rep_ids)])
            rep_cursor += 1
        state_panels[state] = panel

    primary_rep = {}
    for _, row in hcp_master.iterrows():
        hcp_id = row["hcp_id"]
        state = row["state"]
        panel = state_panels.get(state, rep_ids[:2])
        idx = abs(hash(hcp_id)) % len(panel)
        primary_rep[hcp_id] = panel[idx]

    n_anomaly = max(10, int(len(hcp_master) * 0.02))
    anomaly_hcps = set(rng.choice(hcp_master["hcp_id"].values, size=n_anomaly, replace=False))

    return primary_rep, rep_territory, state_panels, anomaly_hcps


def _select_rep_for_interaction(
    hcp_id, hcp_state, total_hcp_interactions,
    primary_rep, state_panels, rep_ids, anomaly_hcps, rng,
):
    """Select rep per interaction using touch-tier mix.

    Tiers:
      - Anomaly HCPs: 40% primary, 60% random (5+ unique reps pattern)
      - Low-touch (<=4 interactions): 100% primary
      - Regular (5-20): 85% primary, 15% state panel
      - KOL (>20): 70% primary, 20% state panel, 10% any rep
    """
    primary = primary_rep.get(hcp_id)
    if primary is None:
        return str(rng.choice(rep_ids))

    if hcp_id in anomaly_hcps:
        if rng.random() < 0.4:
            return primary
        return str(rng.choice(rep_ids))

    if total_hcp_interactions <= 4:
        return primary

    panel = state_panels.get(hcp_state, [primary])
    r = rng.random()
    if total_hcp_interactions <= 20:
        if r < 0.85:
            return primary
        return str(rng.choice(panel))
    else:
        if r < 0.70:
            return primary
        elif r < 0.90:
            return str(rng.choice(panel))
        return str(rng.choice(rep_ids))


def generate_hcp_interactions_v2(hcp_master: pd.DataFrame) -> pd.DataFrame:
    """
    V2 CMS per-category interaction generator — consulting + education only.

    Meals and travel are intentionally excluded here; they are distributed across
    all v2 events (speaker + consulting + education) by allocate_meals_and_travel().

    Algorithm per (hcp_id, program_year, cms_category ∈ {Consulting Fee, Education}):
      1. Skip if category_total < MIN_PLAUSIBLE_CATEGORY_CMS.
      2. Map cms_category → interaction_type via CMS_TO_INTERACTION_TYPE_V2.
      3. n_events = clamp(round(total / PER_EVENT_TARGETS_BY_CATEGORY[type]),
                          1, MAX_EVENTS_BY_CATEGORY[type])
      4. fees = Dirichlet([alpha]*n_events) * category_total
      5. venue_state: events are local (practice_state) unless CMS travel total > 0,
         in which case round(travel_total / 500) events are assigned a remote state.
      6. travel_reimbursement=0.0 and meal_cost=None — filled by allocate_meals_and_travel.

    Schema extends generate_hcp_interactions() with two new columns:
        venue_state (str, nullable) — state where interaction took place
        travel_reimbursement (float) — filled post-generation by allocate_meals_and_travel
    """
    logger.info("Generating HCP interactions (v2 — consulting + education only)...")
    rng = np.random.default_rng(RANDOM_SEED + 5)

    _EMPTY_COLS = [
        "interaction_id", "hcp_id", "interaction_date", "interaction_type",
        "rep_id", "rep_territory", "product_discussed",
        "interaction_city", "interaction_state", "venue_state",
        "practice_id", "practice_city",
        "meal_type", "meal_cost", "attendee_count",
        "fmv_rate_used", "fmv_tier", "fmv_benchmark", "fmv_approved",
        "alcohol_provided", "payment_amount", "travel_reimbursement",
        "annual_total_ytd", "compliance_reviewed", "compliance_flag",
        "business_rationale", "cms_total_this_year", "is_reconciliation_anomaly",
        "program_year", "synthetic_data_flag",
        "violation_types", "violation_severity", "is_violation",
    ]

    # ── Load CMS category totals (all 4 categories) ───────────────────────────
    cms_cat = load_cms_category_totals()
    if cms_cat is None or cms_cat.empty:
        logger.warning(
            "CMS category totals unavailable — returning empty DataFrame. "
            "Set USE_V2_INTERACTIONS_GENERATOR=false to fall back to v1."
        )
        return pd.DataFrame(columns=_EMPTY_COLS)

    # ── Build travel lookup by profile_id for venue_state assignment ──────────
    # Key: (covered_recipient_profile_id str, program_year int) → travel_total float
    travel_cat = cms_cat[cms_cat["cms_category"] == "Travel and Lodging"]
    travel_lookup_pid: dict = {}
    for _, r in travel_cat.iterrows():
        k = (str(r["covered_recipient_profile_id"]), int(r["program_year"]))
        travel_lookup_pid[k] = float(r["category_total"])

    # ── Filter to consulting + education and join to hcp_master ───────────────
    consult_edu_cat = cms_cat[cms_cat["cms_category"].isin(CMS_TO_INTERACTION_TYPE_V2)]
    hcp_keyed = hcp_master.copy()
    hcp_keyed["_pid"] = hcp_keyed["physician_profile_id"].astype(str)

    joined = hcp_keyed.merge(
        consult_edu_cat.rename(columns={"covered_recipient_profile_id": "_pid"}),
        on="_pid",
        how="inner",
    ).drop(columns=["_pid"])

    logger.info(f"  CMS category join (consulting+education): {len(joined):,} rows")

    # ── Rep pool (same pattern as v1) ─────────────────────────────────────────
    primary_rep, rep_territory_map, state_panels, anomaly_hcps = _build_rep_panel_and_assignments(hcp_master, rng)
    rep_ids       = [f"REP_{i:04d}" for i in range(1, 501)]
    rep_territory = rep_territory_map
    _hcp_state_lookup = dict(zip(hcp_master["hcp_id"].astype(str), hcp_master["state"].astype(str)))

    # ── CMS total per (hcp_id, year) for cms_total_this_year field ────────────
    cms_hcp_yr_total = (
        joined.groupby(["hcp_id", "program_year"])["category_total"]
        .sum()
        .to_dict()
    )

    records = []
    seq = 0

    for _, row in joined.iterrows():
        hcp_id         = str(row["hcp_id"])
        year           = int(row["program_year"])
        cms_category   = str(row["cms_category"])
        category_total = float(row["category_total"])
        profile        = str(row["hcp_violation_profile"])
        specialty      = str(row["specialty"])
        fmv_tier       = str(row["fmv_tier"])
        practice_state = str(row["state"])
        pid            = str(row["physician_profile_id"])

        if category_total < MIN_PLAUSIBLE_CATEGORY_CMS:
            continue

        interaction_type = CMS_TO_INTERACTION_TYPE_V2.get(cms_category)
        if interaction_type is None:
            continue

        target   = PER_EVENT_TARGETS_BY_CATEGORY[interaction_type]
        cap      = MAX_EVENTS_BY_CATEGORY[interaction_type]
        n_events = max(1, round(category_total / target))
        n_events = min(n_events, cap)

        alpha  = INTERACTION_ALPHA_BY_PROFILE[profile]
        splits = rng.dirichlet([alpha] * n_events)
        fees   = (splits * category_total).round(2)

        # ── Pre-compute remote event slots from CMS travel ────────────────────
        travel_total = travel_lookup_pid.get((pid, year), 0.0)
        n_remote = 0
        if travel_total > 0:
            n_remote = min(n_events, max(0, round(travel_total / 500.0)))
        remote_idx_set: set = (
            set(map(int, rng.choice(n_events, size=n_remote, replace=False)))
            if n_remote > 0 else set()
        )

        cms_total_this_year = cms_hcp_yr_total.get((hcp_id, year), category_total)
        fmv_bench = _get_fmv_benchmark(specialty, fmv_tier)
        vague_prob = {
            "clean": 0.0, "minor": 0.05, "moderate": 0.30, "serious": 0.60
        }[profile]

        for event_i, fee in enumerate(fees):
            seq += 1
            idate   = _random_business_date(year, rng)
            _est_interactions = int(PROFILE_MONTHLY_FREQUENCY.get(profile, 1.0) * 12 * 3)
            rep     = _select_rep_for_interaction(
                hcp_id, _hcp_state_lookup.get(hcp_id, "NY"), _est_interactions,
                primary_rep, state_panels, rep_ids, anomaly_hcps, rng
            )
            product = str(rng.choice(NOVA_PHARMA_PRODUCTS))

            # ── Venue state: local or remote ──────────────────────────────────
            if event_i in remote_idx_set:
                other_states = [s for s in _US_STATE_ABBRS if s != practice_state]
                venue_state  = str(rng.choice(other_states))
            else:
                venue_state = practice_state

            # ── FMV (consulting only, same multiplier logic as v1) ─────────────
            if interaction_type == "consulting":
                fee_mult = {
                    "clean":    rng.uniform(0.80, 1.00),
                    "minor":    rng.uniform(0.90, 1.10),
                    "moderate": rng.uniform(1.10, 1.30),
                    "serious":  rng.uniform(1.50, 2.00),
                }[profile]
                fmv_rate_used = round(fmv_bench * float(fee_mult), 2)
                fmv_approved  = fmv_rate_used <= fmv_bench
            else:
                fmv_rate_used = None
                fmv_approved  = True

            # ── Alcohol (same as v1) ──────────────────────────────────────────
            alcohol = {
                "clean":    False,
                "minor":    False,
                "moderate": bool(rng.random() < 0.05),
                "serious":  bool(rng.random() < 0.20),
            }[profile]

            # ── Business rationale (same vague_prob logic as v1) ──────────────
            condition = str(rng.choice(CONDITIONS.get(specialty, CONDITIONS["Other"])))
            if rng.random() < vague_prob:
                rationale = str(rng.choice(VAGUE_RATIONALE_VALUES))
            else:
                tmpl      = str(rng.choice(NORMAL_RATIONALE_TEMPLATES))
                rationale = tmpl.format(
                    specialty=specialty, product=product, condition=condition
                )

            records.append({
                "interaction_id":            f"INT-{year}-{seq:07d}",
                "hcp_id":                    hcp_id,
                "interaction_date":          str(idate),
                "interaction_type":          interaction_type,
                "rep_id":                    rep,
                "rep_territory":             str(rep_territory[rep]),
                "product_discussed":         product,
                "interaction_city":          str(row["practice_city"]),
                "interaction_state":         practice_state,
                "venue_state":               venue_state,
                "practice_id":               str(row["practice_id"]),
                "practice_city":             str(row["practice_city"]),
                "meal_type":                 None,   # allocated by allocate_meals_and_travel
                "meal_cost":                 None,   # allocated by allocate_meals_and_travel
                "attendee_count":            int(rng.integers(1, 16)),
                "fmv_rate_used":             fmv_rate_used,
                "fmv_tier":                  fmv_tier,
                "fmv_benchmark":             fmv_bench,
                "fmv_approved":              fmv_approved,
                "alcohol_provided":          alcohol,
                "payment_amount":            round(float(fee), 2),
                "travel_reimbursement":      0.0,    # allocated by allocate_meals_and_travel
                "annual_total_ytd":          0.0,    # recalculated below
                "compliance_reviewed":       bool(rng.random() > 0.10),
                "compliance_flag":           "none",
                "business_rationale":        rationale,
                "cms_total_this_year":       cms_total_this_year,
                "is_reconciliation_anomaly": False,  # recalculated below
                "program_year":              year,
                "synthetic_data_flag":       True,
                "violation_types":           "",
                "violation_severity":        "none",
                "is_violation":              False,
            })

    if not records:
        logger.warning("generate_hcp_interactions_v2: no records generated")
        return pd.DataFrame(columns=_EMPTY_COLS)

    df = pd.DataFrame(records)

    # ── annual_total_ytd — cumulative sum per HCP per year (same as v1) ───────
    df = df.sort_values(["hcp_id", "program_year", "interaction_date"]).reset_index(drop=True)
    df["annual_total_ytd"] = (
        df.groupby(["hcp_id", "program_year"])["payment_amount"].cumsum().round(2)
    )

    # ── is_reconciliation_anomaly — payment sum vs CMS total (>10% gap) ───────
    hcp_year_sums = (
        df.groupby(["hcp_id", "program_year"])["payment_amount"]
        .sum()
        .reset_index(name="_sum")
    )
    df = df.merge(hcp_year_sums, on=["hcp_id", "program_year"], how="left")
    df["is_reconciliation_anomaly"] = df.apply(
        lambda r: (
            abs(r["_sum"] - r["cms_total_this_year"]) / r["cms_total_this_year"] > 0.10
            if r["cms_total_this_year"] > 0 else False
        ),
        axis=1,
    )
    df = df.drop(columns=["_sum"])

    logger.info(f"HCP interactions (v2): {len(df):,} records")
    return df


# ── Step 4: Generate Speaker Events ──────────────────────────────────────────

def generate_speaker_events(hcp_master: pd.DataFrame) -> pd.DataFrame:
    """
    5,000+ records. Speaker selection, venue, fee, and attendance
    distributions all controlled by hcp_violation_profile.
    """
    logger.info("Generating speaker program events...")
    rng = np.random.default_rng(RANDOM_SEED + 2)

    speakers = hcp_master[
        hcp_master["is_kol"] |
        hcp_master["fmv_tier"].isin(["regional", "national"]) |
        (hcp_master["hcp_violation_profile"] != "clean")
    ].copy()

    times_spoke_range = {
        "clean":    (1, 3),
        "minor":    (2, 5),
        "moderate": (4, 7),
        "serious":  (6, 12),
    }

    records = []
    seq = 0

    PARTICIPATION_RATE = {
        "clean":    0.01,
        "minor":    0.04,
        "moderate": 0.07,
        "serious":  0.12,
    }

    for _, spk in speakers.iterrows():
        profile = spk["hcp_violation_profile"]

        # KOLs get elevated participation; others use profile-based rate.
        rate = 0.10 if bool(spk["is_kol"]) else PARTICIPATION_RATE[profile]
        if rng.random() > rate:
            continue  # this HCP does not participate as a speaker

        lo, hi = times_spoke_range[profile]
        n_total = int(rng.integers(lo, hi + 1))

        year_events: dict[int, int] = {yr: 0 for yr in YEARS}
        for _ in range(n_total):
            year_events[int(rng.choice(YEARS))] += 1

        topic_year_counts: dict[tuple, int] = {}
        annual_comp: dict[int, float] = {yr: 0.0 for yr in YEARS}
        times_yr: dict[int, int] = {yr: 0 for yr in YEARS}

        is_priority_speaker = bool(spk["is_kol"]) or spk["fmv_tier"] in ("regional", "national")

        for year in YEARS:
            cms_total_yr = float(spk.get(f"cms_total_{year}", 0) or 0)

            # KOLs and regional/national tier HCPs speak regardless of CMS total —
            # they may deliver programs without CMS-reported payments in that year.
            # Ordinary HCPs require a minimum CMS total to be realistic speaker candidates.
            if not is_priority_speaker:
                if cms_total_yr == 0:
                    continue
                if cms_total_yr < 500:
                    continue

            # Fee cap: for CMS-anchored HCPs, cap at 40% of CMS total.
            # For priority speakers with zero/small CMS total, use FMV rate card directly.
            n_events_this_year = max(1, year_events[year])
            if cms_total_yr >= 500:
                speaker_share = cms_total_yr * 0.40
                max_fee_per_event = max(50.0, speaker_share / n_events_this_year)
                max_travel = max(0.0, cms_total_yr * 0.10)
            else:
                max_fee_per_event = float("inf")  # FMV rate card is the only ceiling
                max_travel = 1500.0

            for _ in range(year_events[year]):
                seq += 1
                topic = str(rng.choice(PROGRAM_TOPICS))
                key = (topic, year)
                topic_year_counts[key] = topic_year_counts.get(key, 0) + 1
                times_yr[year] += 1

                vdist = VENUE_DISTRIBUTIONS[profile]
                venue_type = str(rng.choice(list(vdist.keys()), p=list(vdist.values())))

                venue_cost = {
                    "clean":    round(float(rng.uniform(500, 1500)), 2),
                    "minor":    round(float(rng.uniform(500, 2500)), 2),
                    "moderate": round(float(rng.uniform(1000, 4000)), 2),
                    "serious":  round(float(rng.uniform(3000, 8000)), 2),
                }[profile]

                attendee_count = {
                    "clean":    int(rng.integers(8, 26)),
                    "minor":    int(rng.integers(5, 21)),
                    "moderate": int(rng.integers(3, 16)),
                    "serious":  int(rng.integers(1, 6)),
                }[profile]

                fmv_bench = _get_fmv_benchmark(spk["specialty"], spk["fmv_tier"])
                fee_mult = {
                    "clean":    rng.uniform(0.70, 1.00),
                    "minor":    rng.uniform(0.90, 1.05),
                    "moderate": rng.uniform(1.10, 1.30),
                    "serious":  rng.uniform(1.50, 2.00),
                }[profile]
                speaker_fee = round(min(fmv_bench * float(fee_mult), max_fee_per_event), 2)
                annual_comp[year] = round(annual_comp[year] + speaker_fee, 2)
                travel = round(min(float(rng.uniform(0, 1500)), max_travel), 2)

                alcohol = {
                    "clean":    False,
                    "minor":    bool(rng.random() < 0.02),
                    "moderate": bool(rng.random() < 0.10),
                    "serious":  bool(rng.random() < 0.30),
                }[profile]

                records.append({
                    "event_id": f"EVT-{year}-{seq:06d}",
                    "event_date": str(_random_business_date(year, rng)),
                    "speaker_hcp_id": spk["hcp_id"],
                    "speaker_practice_id": spk["practice_id"],
                    "speaker_practice_city": spk["practice_city"],
                    "speaker_specialty": spk["specialty"],
                    "speaker_tier": spk["fmv_tier"],
                    "program_topic": topic,
                    "venue_name": fake.company() + " " + str(rng.choice(["Hall", "Center", "Suite", "Room"])),
                    "venue_type": venue_type,
                    "venue_city": fake.city(),
                    "venue_state": spk["state"],
                    "venue_cost": venue_cost,
                    "attendee_count": attendee_count,
                    "speaker_fee": speaker_fee,
                    "fmv_benchmark": fmv_bench,
                    "fmv_exceeded": speaker_fee > fmv_bench,
                    "travel_reimbursement": travel,
                    "total_program_cost": round(venue_cost + speaker_fee + travel, 2),
                    "product_featured": str(rng.choice(NOVA_PHARMA_PRODUCTS)),
                    "alcohol_provided": alcohol,
                    "compliance_approved": bool(rng.random() > 0.05),
                    "repeat_speaker": times_yr[year] > 1,
                    "times_spoke_this_year": times_yr[year],
                    "times_spoke_same_topic": topic_year_counts[key],
                    "program_topic_repeat_count": topic_year_counts[key],
                    "annual_speaker_compensation": annual_comp[year],
                    "program_year": year,
                    "synthetic_data_flag": True,
                    "violation_types": "",
                    "violation_severity": "none",
                    "is_violation": False,
                })

    df = pd.DataFrame(records)
    logger.info(f"Speaker events: {len(df):,} records")
    return df


# ── Step 4b: Generate Speaker Events (v2 — CMS-reconciled) ───────────────────

def generate_speaker_events_v2(hcp_master: pd.DataFrame) -> pd.DataFrame:
    """
    V2 CMS-reconciled speaker event generator.

    Fixes the v1 reconciliation invariant violation: Dirichlet-splits each HCP's
    ACTUAL CMS speaker-fee total into per-event fees so sum(fees) == cms_speaker_total.

    Changes from initial v2:
      - venue_state: events near the HCP's practice state by default; if CMS travel
        total > 0, round(travel_total / 500) events are assigned a remote state.
      - travel_reimbursement: initialised to 0.0; allocate_meals_and_travel() fills
        this from the CMS Travel and Lodging total for remote events.
      - total_program_cost: venue_cost + speaker_fee only at generation time;
        allocate_meals_and_travel() adds travel after allocation.

    Schema: identical to generate_speaker_events() plus venue_state remote logic.
    """
    logger.info("Generating speaker program events (v2 — CMS-reconciled)...")
    rng = np.random.default_rng(RANDOM_SEED + 4)

    _EMPTY_COLS = [
        "event_id", "event_date", "speaker_hcp_id", "speaker_practice_id",
        "speaker_practice_city", "speaker_specialty", "speaker_tier",
        "program_topic", "venue_name", "venue_type", "venue_city", "venue_state",
        "venue_cost", "attendee_count", "speaker_fee", "fmv_benchmark",
        "fmv_exceeded", "travel_reimbursement", "total_program_cost",
        "product_featured", "alcohol_provided", "compliance_approved",
        "repeat_speaker", "times_spoke_this_year", "times_spoke_same_topic",
        "program_topic_repeat_count", "annual_speaker_compensation",
        "program_year", "synthetic_data_flag", "violation_types",
        "violation_severity", "is_violation",
    ]

    # ── Load CMS speaker totals ───────────────────────────────────────────────
    cms_spk = load_cms_speaker_totals()
    if cms_spk is None or cms_spk.empty:
        logger.warning(
            "CMS speaker totals unavailable — returning empty DataFrame. "
            "Set USE_V2_SPEAKER_GENERATOR=false to fall back to v1."
        )
        return pd.DataFrame(columns=_EMPTY_COLS)

    # ── Load CMS travel totals for remote venue_state assignment ─────────────
    # Key: (hcp_id str, program_year int) → travel_total float
    travel_lookup_spk: dict = {}
    cms_cat_travel = load_cms_category_totals()
    if cms_cat_travel is not None and not cms_cat_travel.empty:
        trav = cms_cat_travel[cms_cat_travel["cms_category"] == "Travel and Lodging"]
        pid_to_hcp = {
            str(k): str(v)
            for k, v in hcp_master.set_index("physician_profile_id")["hcp_id"].items()
        }
        for _, r in trav.iterrows():
            hid = pid_to_hcp.get(str(r["covered_recipient_profile_id"]))
            if hid:
                key = (hid, int(r["program_year"]))
                travel_lookup_spk[key] = float(r["category_total"])

    # ── Inner-join hcp_master ↔ CMS speaker totals ────────────────────────────
    hcp_keyed = hcp_master.copy()
    hcp_keyed["_pid"] = hcp_keyed["physician_profile_id"].astype(str)

    joined = hcp_keyed.merge(
        cms_spk.rename(columns={"covered_recipient_profile_id": "_pid"}),
        on="_pid",
        how="inner",
    ).drop(columns=["_pid"])

    logger.info(f"  CMS speaker join: {len(joined):,} (hcp_id, year) pairs")

    # ── Per-HCP-year running state ────────────────────────────────────────────
    times_yr:         dict = {}   # (hcp_id, year)        → event count
    annual_comp:      dict = {}   # (hcp_id, year)        → running fee sum
    topic_yr_counts:  dict = {}   # (hcp_id, year, topic) → count
    topic_all_counts: dict = {}   # (hcp_id, topic)       → count across years
    preferred_topic:  dict = {}   # hcp_id → locked topic for serious profiles

    records = []
    seq = 0

    for _, row in joined.iterrows():
        hcp_id        = str(row["hcp_id"])
        year          = int(row["program_year"])
        cms_total     = float(row["speaker_total"])
        profile       = str(row["hcp_violation_profile"])
        specialty     = str(row["specialty"])
        fmv_tier      = str(row["fmv_tier"])
        practice_state = str(row["state"])

        if cms_total < MIN_PLAUSIBLE_SPEAKER_CMS:
            continue

        n_events = max(1, round(cms_total / REALISTIC_PER_EVENT_TARGET))
        n_events = min(n_events, MAX_PLAUSIBLE_EVENTS)

        alpha  = SPEAKER_ALPHA_BY_PROFILE[profile]
        splits = rng.dirichlet([alpha] * n_events)
        fees   = (splits * cms_total).round(2)

        # ── Pre-compute remote event slots from CMS travel ────────────────────
        travel_total = travel_lookup_spk.get((hcp_id, year), 0.0)
        n_remote = 0
        if travel_total > 0:
            n_remote = min(n_events, max(0, round(travel_total / 500.0)))
        remote_idx_set: set = (
            set(map(int, rng.choice(n_events, size=n_remote, replace=False)))
            if n_remote > 0 else set()
        )

        key_yr = (hcp_id, year)
        if key_yr not in times_yr:
            times_yr[key_yr]    = 0
            annual_comp[key_yr] = 0.0

        for event_i, fee in enumerate(fees):
            seq += 1
            times_yr[key_yr] += 1
            t_spoke = times_yr[key_yr]

            # Topic: serious profiles repeat same topic (REPEAT_SAME_TOPIC_PROGRAMS)
            if profile == "serious":
                if hcp_id not in preferred_topic:
                    preferred_topic[hcp_id] = str(rng.choice(PROGRAM_TOPICS))
                topic = preferred_topic[hcp_id]
            else:
                topic = str(rng.choice(PROGRAM_TOPICS))

            tk_yr  = (hcp_id, year, topic)
            tk_all = (hcp_id, topic)
            topic_yr_counts[tk_yr]   = topic_yr_counts.get(tk_yr, 0) + 1
            topic_all_counts[tk_all] = topic_all_counts.get(tk_all, 0) + 1

            # Venue state: remote or practice state
            if event_i in remote_idx_set:
                other_states = [s for s in _US_STATE_ABBRS if s != practice_state]
                venue_state  = str(rng.choice(other_states))
            else:
                venue_state = practice_state

            # Venue (same profile-controlled distributions as v1)
            vdist      = VENUE_DISTRIBUTIONS[profile]
            venue_type = str(rng.choice(list(vdist.keys()), p=list(vdist.values())))
            venue_cost = {
                "clean":    round(float(rng.uniform(500,  1500)), 2),
                "minor":    round(float(rng.uniform(500,  2500)), 2),
                "moderate": round(float(rng.uniform(1000, 4000)), 2),
                "serious":  round(float(rng.uniform(3000, 8000)), 2),
            }[profile]

            # Attendee count (same profile-controlled distributions as v1)
            attendee_count = {
                "clean":    int(rng.integers(8, 26)),
                "minor":    int(rng.integers(5, 21)),
                "moderate": int(rng.integers(3, 16)),
                "serious":  int(rng.integers(1,  6)),
            }[profile]

            speaker_fee = float(fee)
            fmv_bench   = _get_fmv_benchmark(specialty, fmv_tier)
            annual_comp[key_yr] = round(annual_comp[key_yr] + speaker_fee, 2)

            # Alcohol (same as v1)
            alcohol = {
                "clean":    False,
                "minor":    bool(rng.random() < 0.02),
                "moderate": bool(rng.random() < 0.10),
                "serious":  bool(rng.random() < 0.30),
            }[profile]

            # Compliance approval (profile-controlled)
            compliance_approved = {
                "clean":    True,
                "minor":    bool(rng.random() > 0.02),
                "moderate": bool(rng.random() > 0.15),
                "serious":  bool(rng.random() > 0.40),
            }[profile]

            records.append({
                "event_id":                    f"EVT-{year}-{seq:06d}",
                "event_date":                  str(_random_business_date(year, rng)),
                "speaker_hcp_id":              hcp_id,
                "speaker_practice_id":         str(row["practice_id"]),
                "speaker_practice_city":       str(row["practice_city"]),
                "speaker_specialty":           specialty,
                "speaker_tier":                fmv_tier,
                "program_topic":               topic,
                "venue_name":                  fake.company() + " " + str(rng.choice(["Hall", "Center", "Suite", "Room"])),
                "venue_type":                  venue_type,
                "venue_city":                  fake.city(),
                "venue_state":                 venue_state,
                "venue_cost":                  venue_cost,
                "attendee_count":              attendee_count,
                "speaker_fee":                 speaker_fee,
                "fmv_benchmark":               fmv_bench,
                "fmv_exceeded":                speaker_fee > fmv_bench,
                "travel_reimbursement":        0.0,   # allocated by allocate_meals_and_travel
                "total_program_cost":          round(venue_cost + speaker_fee, 2),  # travel added later
                "product_featured":            str(rng.choice(NOVA_PHARMA_PRODUCTS)),
                "alcohol_provided":            alcohol,
                "compliance_approved":         compliance_approved,
                "repeat_speaker":              t_spoke > REPEAT_SPEAKER_THRESHOLD,
                "times_spoke_this_year":       t_spoke,
                "times_spoke_same_topic":      topic_yr_counts[tk_yr],
                "program_topic_repeat_count":  topic_all_counts[tk_all],
                "annual_speaker_compensation": annual_comp[key_yr],
                "program_year":                year,
                "synthetic_data_flag":         True,
                "violation_types":             "",
                "violation_severity":          "none",
                "is_violation":                False,
            })

    df = pd.DataFrame(records) if records else pd.DataFrame(columns=_EMPTY_COLS)
    logger.info(f"Speaker events (v2): {len(df):,} records")
    return df


# ── Step 4b: Allocate meals and travel (v2 post-generation pass) ──────────────

def allocate_meals_and_travel(
    hcp_master: pd.DataFrame,
    interactions_df: pd.DataFrame,
    speaker_events_df: pd.DataFrame,
) -> tuple:
    """
    Distribute CMS Food-and-Beverage and Travel-and-Lodging totals across
    the events produced by generate_hcp_interactions_v2 and
    generate_speaker_events_v2.

    Called once after both v2 generators finish.  Both generators leave
    meal_cost=None, meal_type=None, and travel_reimbursement=0.0 as
    placeholders — this function fills those fields.

    Meal allocation (per HCP × year):
        n_anchor_events = len(interaction events) + len(speaker events) for this HCP-year
        If n_anchor_events > 0:
            event_pool = min(cms_meal_total, n_int * $50)   # only interactions get meal_cost
            Dirichlet-split event_pool across interaction events → meal_cost + meal_type
            standalone_pool = cms_meal_total − event_pool
            standalone_pool → new standalone "meal" interaction records
        If n_anchor_events == 0 and cms_meal > 0:
            All CMS meal → standalone "meal" records (n = max(1, round(cms_meal / 50)))

    Travel allocation (per HCP × year):
        remote events = events where venue_state != practice_state (interactions + speaker)
        If remote_events > 0:
            Dirichlet-split cms_travel across remote events → travel_reimbursement
            speaker_events.total_program_cost updated to include travel amount
        If remote_events == 0 AND cms_travel > 0 (orphaned travel):
            Generate one synthetic "meeting" interaction with
            payment_amount=0.0, travel_reimbursement=cms_travel_total.
            This covers HCPs whose CMS record shows travel but all generated
            events happened to be local.

    Returns (interactions_df, speaker_events_df) where interactions_df includes
    any appended standalone meal records and orphaned-travel meeting records.
    """
    logger.info("Allocating meals and travel (v2 post-generation pass)...")
    rng = np.random.default_rng(RANDOM_SEED + 6)
    primary_rep, _rep_territory, _state_panels, _anomaly_hcps = _build_rep_panel_and_assignments(hcp_master, rng)
    _rep_ids = [f"REP_{i:04d}" for i in range(1, 501)]

    # ── Load CMS category totals ──────────────────────────────────────────────
    cms_cat = load_cms_category_totals()
    if cms_cat is None or cms_cat.empty:
        logger.warning(
            "CMS category totals unavailable — skipping allocate_meals_and_travel. "
            "Meal and travel fields remain at default values."
        )
        return interactions_df, speaker_events_df

    # ── Build per-HCP lookups: (pid_str, year_int) → float ───────────────────
    meal_lookup: dict = {}
    travel_lookup: dict = {}
    for _, r in cms_cat.iterrows():
        k = (str(r["covered_recipient_profile_id"]), int(r["program_year"]))
        if r["cms_category"] == "Food and Beverage":
            meal_lookup[k] = float(r["category_total"])
        elif r["cms_category"] == "Travel and Lodging":
            travel_lookup[k] = float(r["category_total"])

    # ── HCP master lookup: hcp_id → (pid, practice_state, practice_city, profile) ──
    hcp_info_by_id: dict = {}
    for _, h in hcp_master.iterrows():
        hcp_info_by_id[str(h["hcp_id"])] = {
            "pid":            str(h["physician_profile_id"]),
            "practice_state": str(h["state"]),
            "practice_city":  str(h["practice_city"]),
            "profile":        str(h["hcp_violation_profile"]),
        }

    # ── Work on copies to avoid SettingWithCopyWarning ────────────────────────
    interactions_df   = interactions_df.copy()
    speaker_events_df = speaker_events_df.copy()

    # Ensure meal columns accept mixed types (None and float)
    interactions_df["meal_cost"] = interactions_df["meal_cost"].astype(object)
    interactions_df["meal_type"] = interactions_df["meal_type"].astype(object)

    new_records: list = []    # accumulates both standalone meals and orphaned-travel meetings
    meeting_seq: int  = 0     # global counter for INT-MEETING-* IDs

    # ── Collect all (hcp_id, year) pairs that appear in either df ────────────
    int_pairs: set = set(
        zip(interactions_df["hcp_id"].astype(str),
            interactions_df["program_year"].astype(int))
    )
    spk_pairs: set = set(
        zip(speaker_events_df["speaker_hcp_id"].astype(str),
            speaker_events_df["program_year"].astype(int))
    )
    # Also include HCPs that have CMS meal or travel but zero generated events
    cms_hcp_pairs: set = set()
    for (p, y) in list(meal_lookup.keys()) + list(travel_lookup.keys()):
        # map pid → hcp_id using hcp_info_by_id inverse
        pass  # built below after hcp_info_by_id is populated
    pid_to_hcp: dict = {v["pid"]: k for k, v in hcp_info_by_id.items()}
    for (pid, yr) in list(meal_lookup.keys()) + list(travel_lookup.keys()):
        hid = pid_to_hcp.get(pid)
        if hid:
            cms_hcp_pairs.add((hid, yr))

    all_pairs = sorted(int_pairs | spk_pairs | cms_hcp_pairs)

    for hcp_id, year in all_pairs:
        info = hcp_info_by_id.get(hcp_id)
        if info is None:
            continue

        pid            = info["pid"]
        practice_state = info["practice_state"]
        practice_city  = info["practice_city"]
        profile        = info["profile"]
        k              = (pid, year)

        # ── Count anchor events (interactions + speaker events) ───────────────
        int_mask = (
            (interactions_df["hcp_id"] == hcp_id) &
            (interactions_df["program_year"] == year)
        )
        int_idx       = interactions_df.index[int_mask].tolist()
        n_int         = len(int_idx)

        spk_mask = (
            (speaker_events_df["speaker_hcp_id"] == hcp_id) &
            (speaker_events_df["program_year"] == year)
        )
        n_spk         = int(spk_mask.sum())
        n_anchor      = n_int + n_spk
        # Determine rep for standalone meals (reps take HCPs to meals)
        _meal_rep_id = primary_rep.get(hcp_id, _rep_ids[abs(hash(hcp_id)) % len(_rep_ids)])
        _meal_rep_territory = _rep_territory.get(_meal_rep_id)

        # ── Meal allocation ───────────────────────────────────────────────────
        cms_meal_total = meal_lookup.get(k, 0.0)
        if cms_meal_total > 0:
            alpha = INTERACTION_ALPHA_BY_PROFILE.get(profile, 1.0)

            if n_anchor > 0:
                # Attach meals to existing interaction events; residual → standalone
                event_pool      = min(cms_meal_total, n_int * 50.0) if n_int > 0 else 0.0
                standalone_pool = cms_meal_total - event_pool

                if event_pool > 0:
                    splits    = rng.dirichlet([alpha] * n_int)
                    m_amounts = (splits * event_pool).round(2)
                    for idx, amount in zip(int_idx, m_amounts):
                        meal_type = str(rng.choice(
                            MEAL_TYPES,
                            p=[MEAL_TYPE_DISTRIBUTION[m] for m in MEAL_TYPES],
                        ))
                        interactions_df.at[idx, "meal_type"] = meal_type
                        interactions_df.at[idx, "meal_cost"] = float(amount)

                if standalone_pool > 0.01:
                    n_standalone = max(1, min(10, round(standalone_pool / 50.0)))
                    s_splits     = rng.dirichlet([alpha] * n_standalone)
                    s_amounts    = (s_splits * standalone_pool).round(2)
                    for j, s_amount in enumerate(s_amounts):
                        meal_type = str(rng.choice(
                            MEAL_TYPES,
                            p=[MEAL_TYPE_DISTRIBUTION[m] for m in MEAL_TYPES],
                        ))
                        new_records.append(_standalone_meal_record(
                            hcp_id, year, j, practice_city, practice_state,
                            meal_type, float(s_amount), rng,
                            rep_id=_meal_rep_id, rep_territory=_meal_rep_territory,
                        ))

            else:
                # No events at all — generate all meals as pure standalone records
                n_standalone = max(1, min(10, round(cms_meal_total / 50.0)))
                s_splits     = rng.dirichlet([alpha] * n_standalone)
                s_amounts    = (s_splits * cms_meal_total).round(2)
                for j, s_amount in enumerate(s_amounts):
                    meal_type = str(rng.choice(
                        MEAL_TYPES,
                        p=[MEAL_TYPE_DISTRIBUTION[m] for m in MEAL_TYPES],
                    ))
                    new_records.append(_standalone_meal_record(
                        hcp_id, year, j, practice_city, practice_state,
                        meal_type, float(s_amount), rng,
                        rep_id=_meal_rep_id, rep_territory=_meal_rep_territory,
                    ))

        # ── Travel allocation ─────────────────────────────────────────────────
        cms_travel_total = travel_lookup.get(k, 0.0)
        if cms_travel_total > 0:
            # Remote interaction events: venue_state differs from practice_state
            remote_int_mask = int_mask & (interactions_df["venue_state"] != practice_state)
            remote_int_idx  = interactions_df.index[remote_int_mask].tolist()

            # Remote speaker events: venue_state differs from practice_state
            remote_spk_mask = spk_mask & (speaker_events_df["venue_state"] != practice_state)
            remote_spk_idx  = speaker_events_df.index[remote_spk_mask].tolist()

            total_remote = len(remote_int_idx) + len(remote_spk_idx)

            if total_remote > 0:
                # Distribute CMS travel across existing remote events
                alpha          = INTERACTION_ALPHA_BY_PROFILE.get(profile, 1.0)
                splits         = rng.dirichlet([alpha] * total_remote)
                travel_amounts = (splits * cms_travel_total).round(2)

                for i, idx in enumerate(remote_int_idx):
                    interactions_df.at[idx, "travel_reimbursement"] = float(travel_amounts[i])

                offset = len(remote_int_idx)
                for j, idx in enumerate(remote_spk_idx):
                    t_amount  = float(travel_amounts[offset + j])
                    old_cost  = float(speaker_events_df.at[idx, "total_program_cost"])
                    speaker_events_df.at[idx, "travel_reimbursement"] = t_amount
                    speaker_events_df.at[idx, "total_program_cost"]   = round(old_cost + t_amount, 2)

            else:
                # Orphaned travel: no remote events — generate a synthetic meeting record
                # Use primary rep for consistency with other interactions
                rep = primary_rep.get(hcp_id, str(rng.choice(_rep_ids)))
                new_records.append({
                    "interaction_id":            f"INT-MEETING-{year}-{meeting_seq:07d}",
                    "hcp_id":                    hcp_id,
                    "interaction_date":          str(_random_business_date(year, rng)),
                    "interaction_type":          "meeting",
                    "rep_id":                    rep,
                    "rep_territory":             _rep_territory[rep],
                    "product_discussed":         str(rng.choice(NOVA_PHARMA_PRODUCTS)),
                    "interaction_city":          practice_city,
                    "interaction_state":         practice_state,
                    "venue_state":               practice_state,
                    "practice_id":               None,
                    "practice_city":             practice_city,
                    "meal_type":                 None,
                    "meal_cost":                 None,
                    "attendee_count":            1,
                    "fmv_rate_used":             None,
                    "fmv_tier":                  None,
                    "fmv_benchmark":             None,
                    "fmv_approved":              True,
                    "alcohol_provided":          False,
                    "payment_amount":            0.0,
                    "travel_reimbursement":      round(float(cms_travel_total), 2),
                    "annual_total_ytd":          0.0,
                    "compliance_reviewed":       True,
                    "compliance_flag":           "none",
                    "business_rationale":        "Orphaned travel — CMS Travel and Lodging allocation",
                    "cms_total_this_year":       0.0,
                    "is_reconciliation_anomaly": False,
                    "program_year":              year,
                    "synthetic_data_flag":       True,
                    "violation_types":           "",
                    "violation_severity":        "none",
                    "is_violation":              False,
                })
                meeting_seq += 1

    # ── Append new records (standalone meals + orphaned-travel meetings) ───────
    if new_records:
        new_df = pd.DataFrame(new_records)
        n_meals    = (new_df["interaction_type"] == "meal").sum()
        n_meetings = (new_df["interaction_type"] == "meeting").sum()
        interactions_df = pd.concat([interactions_df, new_df], ignore_index=True)
        logger.info(
            f"  New records appended: {n_meals:,} standalone meals, "
            f"{n_meetings:,} orphaned-travel meetings"
        )

    logger.info(
        f"  allocate_meals_and_travel complete — "
        f"{len(interactions_df):,} interactions (incl. standalone meals + meetings), "
        f"{len(speaker_events_df):,} speaker events"
    )
    return interactions_df, speaker_events_df


def _standalone_meal_record(
    hcp_id: str,
    year: int,
    seq: int,
    practice_city: str,
    practice_state: str,
    meal_type: str,
    amount: float,
    rng: np.random.Generator,
    rep_id: str | None = None,
    rep_territory: str | None = None,
) -> dict:
    """Build a single standalone meal interaction record dict.

    Reps take HCPs out to meals — so rep_id should not be None. Callers pass
    the HCPs primary rep (or a random rep if none is known) so downstream
    rep-HCP network analysis works.
    """
    return {
        "interaction_id":            f"MEAL-{year}-{hcp_id}-{seq:04d}",
        "hcp_id":                    hcp_id,
        "interaction_date":          str(_random_business_date(year, rng)),
        "interaction_type":          "meal",
        "rep_id":                    rep_id,
        "rep_territory":             rep_territory,
        "product_discussed":         None,
        "interaction_city":          practice_city,
        "interaction_state":         practice_state,
        "venue_state":               practice_state,
        "practice_id":               None,
        "practice_city":             practice_city,
        "meal_type":                 meal_type,
        "meal_cost":                 amount,
        "attendee_count":            int(rng.integers(1, 12)),
        "fmv_rate_used":             None,
        "fmv_tier":                  None,
        "fmv_benchmark":             None,
        "fmv_approved":              True,
        "alcohol_provided":          False,
        "payment_amount":            amount,
        "travel_reimbursement":      0.0,
        "annual_total_ytd":          0.0,
        "compliance_reviewed":       True,
        "compliance_flag":           "none",
        "business_rationale":        "Standalone meal — CMS Food and Beverage allocation",
        "cms_total_this_year":       0.0,
        "is_reconciliation_anomaly": False,
        "program_year":              year,
        "synthetic_data_flag":       True,
        "violation_types":           "",
        "violation_severity":        "none",
        "is_violation":              False,
    }


# ── Step 5: Generate Speaker Attendees ────────────────────────────────────────

def generate_speaker_attendees(
    events_df: pd.DataFrame, hcp_master: pd.DataFrame
) -> pd.DataFrame:
    """
    15,000+ records. Attendee type, same-office flags, and repeat attendance
    controlled by the speaker's violation profile.
    """
    logger.info("Generating speaker program attendees...")
    rng = np.random.default_rng(RANDOM_SEED + 3)

    hcp_pool = hcp_master[
        ["hcp_id", "practice_id", "practice_city", "specialty", "state"]
    ].copy().reset_index(drop=True)
    all_hcp_ids = hcp_pool["hcp_id"].tolist()
    hcp_lookup = hcp_pool.set_index("hcp_id")
    speaker_profiles = hcp_master.set_index("hcp_id")["hcp_violation_profile"].to_dict()
    topic_attendance: dict[tuple, int] = {}

    records = []
    seq = 0

    for _, event in events_df.iterrows():
        profile = speaker_profiles.get(event["speaker_hcp_id"], "clean")
        n = max(1, int(event["attendee_count"]))

        type_dist = {
            "clean":    {"hcp": 1.00},
            "minor":    {"hcp": 0.95, "staff": 0.05},
            "moderate": {"hcp": 0.85, "staff": 0.10, "unknown": 0.05},
            "serious":  {"hcp": 0.70, "staff": 0.15, "family": 0.10, "unknown": 0.05},
        }[profile]

        same_office_prob = {
            "clean": 0.00, "minor": 0.02, "moderate": 0.10, "serious": 0.25
        }[profile]

        sample_size = min(n, len(all_hcp_ids))
        sampled = rng.choice(all_hcp_ids, size=sample_size, replace=False)

        for att_hcp_id in sampled:
            seq += 1
            try:
                att = hcp_lookup.loc[att_hcp_id]
            except KeyError:
                continue

            att_type = str(rng.choice(list(type_dist.keys()), p=list(type_dist.values())))
            same_office = (
                att["practice_id"] == event["speaker_practice_id"]
                and att["specialty"] == event.get("speaker_specialty", "")
            ) or bool(rng.random() < same_office_prob)

            tk = (att_hcp_id, event["program_topic"], int(event["program_year"]))
            topic_attendance[tk] = topic_attendance.get(tk, 0) + 1
            times_same = topic_attendance[tk]

            meal = bool(rng.random() > 0.20)
            meal_val = round(float(rng.uniform(25, 100)), 2) if meal else 0.0
            attested = (
                True if profile in ("clean", "minor")
                else bool(rng.random() > 0.20)
            )

            records.append({
                "attendee_id": f"ATT-{int(event['program_year'])}-{seq:07d}",
                "event_id": event["event_id"],
                "attendee_hcp_id": att_hcp_id,
                "attendee_type": att_type,
                "attendee_practice_id": att["practice_id"],
                "attendee_practice_city": att["practice_city"],
                "same_office_as_speaker": same_office,
                "attendee_specialty": att["specialty"],
                "attendee_state": att["state"],
                "meal_provided": meal,
                "meal_value": meal_val,
                "signed_attestation": attested,
                "repeat_attendee_same_topic": times_same > 1,
                "times_attended_same_topic": times_same,
                "program_year": event["program_year"],
                "synthetic_data_flag": True,
                "violation_types": "",
                "violation_severity": "none",
                "is_violation": False,
            })

    df = pd.DataFrame(records)
    logger.info(f"Speaker attendees: {len(df):,} records")
    return df


# ── Step 6: Apply violation flags ─────────────────────────────────────────────

def apply_violation_flags(df: pd.DataFrame, dataset_type: str) -> pd.DataFrame:
    """
    Evaluate each record against applicable violation rules.
    Populates violation_types, violation_severity, is_violation.

    These columns ARE stored in raw parquet output.
    They ARE EXCLUDED from the detection pipeline via dbt feature mart models.
    They ARE ONLY used during Phase 2 model validation.
    """
    logger.info(f"Applying violation flags: {dataset_type} ({len(df):,} rows)...")
    df = df.copy()
    vt: dict[int, list[str]] = {i: [] for i in range(len(df))}

    if dataset_type == "interactions":
        # TYPE 1: MEAL_COST_EXCESSIVE — PhRMA Code 2022 §2
        # Limit is per meal_type: breakfast $30, lunch $75, dinner $125
        meal_mask = df["meal_type"].notna() & df["meal_cost"].notna()
        for i in df.index[meal_mask]:
            limit = MEAL_LIMITS.get(str(df.at[i, "meal_type"]), 125)
            if float(df.at[i, "meal_cost"]) > limit:
                vt[i].append("MEAL_COST_EXCESSIVE")

        # TYPE 9: VAGUE_BUSINESS_RATIONALE — OIG Special Fraud Alert 2020
        for i, val in df["business_rationale"].items():
            v = str(val).strip()
            if not v or v in ("N/A", "Other", "Misc", "Meeting", "Discussion") or len(v) < 15:
                vt[i].append("VAGUE_BUSINESS_RATIONALE")

        # TYPE 10: RAPID_INTERACTION_PATTERN — AKS/OIG
        df["_week"] = pd.to_datetime(df["interaction_date"]).dt.isocalendar().week.astype(int)
        df["_yr"] = pd.to_datetime(df["interaction_date"]).dt.year
        weekly = (
            df.groupby(["hcp_id", "rep_id", "_yr", "_week"])
            .apply(lambda g: list(g.index))
            .reset_index(name="_idxs")
        )
        for _, row in weekly[weekly["_idxs"].apply(len) > 3].iterrows():
            for idx in row["_idxs"]:
                vt[idx].append("RAPID_INTERACTION_PATTERN")
        df.drop(columns=["_week", "_yr"], inplace=True)

        # TYPE 11: CMS_RECONCILIATION_GAP — Sunshine Act
        for i in df.index[df["is_reconciliation_anomaly"] == True]:
            vt[i].append("CMS_RECONCILIATION_GAP")

        # TYPE 13: ANNUAL_COMPENSATION_CAP_EXCEEDED — PhRMA §7 + Nova policy
        for i in df.index[df["annual_total_ytd"] > ANNUAL_COMPENSATION_CAP]:
            vt[i].append("ANNUAL_COMPENSATION_CAP_EXCEEDED")

    elif dataset_type == "speaker_events":
        BAD_VENUES = {"entertainment_venue", "luxury_resort", "sports_venue"}

        # TYPE 2: SPEAKER_VENUE_INAPPROPRIATE — OIG 2020
        for i in df.index[df["venue_type"].isin(BAD_VENUES)]:
            vt[i].append("SPEAKER_VENUE_INAPPROPRIATE")

        # TYPE 4: SPEAKER_FEE_EXCEEDS_FMV — OIG 2020
        for i in df.index[df["fmv_exceeded"] == True]:
            vt[i].append("SPEAKER_FEE_EXCEEDS_FMV")

        # TYPE 5: SPEAKER_SELECTED_BY_PRESCRIBING — PhRMA §7
        for i in df.index[
            (df["times_spoke_this_year"] > 4)
            & (df["annual_speaker_compensation"] > df["fmv_benchmark"] * 4)
        ]:
            vt[i].append("SPEAKER_SELECTED_BY_PRESCRIBING")

        # TYPE 6: LOW_ATTENDEE_COUNT — OIG 2020
        for i in df.index[df["attendee_count"] < 3]:
            vt[i].append("LOW_ATTENDEE_COUNT")

        # TYPE 7: REPEAT_SAME_TOPIC_PROGRAMS — OIG Special Fraud Alert Nov 2020
        # Same HCP spoke on same program_topic > 3 times in a year
        for i in df.index[df["times_spoke_same_topic"] > 3]:
            vt[i].append("REPEAT_SAME_TOPIC_PROGRAMS")

        # TYPE 7b: SPEAKER_RAPID_REPEAT — OIG Special Fraud Alert
        # Two events by same speaker within 30 days (only for speaker_events schema)
        if "speaker_hcp_id" in df.columns:
            df_sorted = df.copy()
            df_sorted["_edt"] = pd.to_datetime(df_sorted["event_date"])
            df_sorted = df_sorted.sort_values(["speaker_hcp_id", "_edt"])
            df_sorted["_days_prev"] = (
                df_sorted.groupby("speaker_hcp_id")["_edt"].diff().dt.days
            )

            rapid_window = int(get_rule("SPEAKER_005")["effective_threshold"])  # 30 days
            rapid_mask = (df_sorted["_days_prev"] > 0) & (df_sorted["_days_prev"] < rapid_window)

            for i in df_sorted.index[rapid_mask]:
                vt[i].append("SPEAKER_RAPID_REPEAT")

            # Also flag the previous event in each rapid pair
            for idx in df_sorted.index[rapid_mask]:
                pos = df_sorted.index.get_loc(idx)
                if pos > 0:
                    prev_idx = df_sorted.index[pos - 1]
                    if (
                        df_sorted.at[prev_idx, "speaker_hcp_id"]
                        == df_sorted.at[idx, "speaker_hcp_id"]
                        and "SPEAKER_RAPID_REPEAT" not in vt[prev_idx]
                    ):
                        vt[prev_idx].append("SPEAKER_RAPID_REPEAT")

            df_sorted.drop(columns=["_edt", "_days_prev"], inplace=True, errors="ignore")

        # TYPE 8: ALCOHOL_PROVIDED — OIG 2020 + PhRMA
        for i in df.index[df["alcohol_provided"] == True]:
            vt[i].append("ALCOHOL_PROVIDED")

        # TYPE 13: ANNUAL_COMPENSATION_CAP_EXCEEDED
        for i in df.index[df["annual_speaker_compensation"] > ANNUAL_COMPENSATION_CAP]:
            vt[i].append("ANNUAL_COMPENSATION_CAP_EXCEEDED")

    elif dataset_type == "attendees":
        NON_HCP = {"family", "staff", "non_prescriber", "unknown"}

        # TYPE 3: REPEAT_PROGRAM_ATTENDANCE — PhRMA §7
        for i in df.index[df["times_attended_same_topic"] >= 3]:
            vt[i].append("REPEAT_PROGRAM_ATTENDANCE")

        # TYPE 12: NON_HCP_ATTENDEES — OIG 2020
        for i in df.index[df["attendee_type"].isin(NON_HCP)]:
            vt[i].append("NON_HCP_ATTENDEES")

        # TYPE 14: ATTENDEE_SAME_OFFICE_AS_SPEAKER — OIG 2020 + PhRMA
        for i in df.index[df["same_office_as_speaker"] == True]:
            vt[i].append("ATTENDEE_SAME_OFFICE_AS_SPEAKER")

    df["violation_types"] = [",".join(vt[i]) for i in range(len(df))]
    df["violation_severity"] = df["violation_types"].apply(
        lambda v: _severity_from_types(v.split(",") if v else [])
    )
    df["is_violation"] = df["violation_types"].apply(lambda v: bool(v))

    n_viol = df["is_violation"].sum()
    logger.info(f"  {n_viol:,} violations ({100 * n_viol / len(df):.1f}%)")
    return df


# ── Step 7b: Verify reconciliation (v2) ──────────────────────────────────────

def verify_reconciliation_v2(
    hcp_master: pd.DataFrame,
    interactions_df: pd.DataFrame,
    speaker_events_df: pd.DataFrame,
) -> dict:
    """
    Cross-validate v2 synthetic totals against CMS category totals.

    Internal totals per (hcp_id, year) — all interaction types included:

        payment amounts:  sum(interactions.payment_amount) across ALL types
                          (consulting/education fees + standalone meal amounts;
                           "meeting" records have payment_amount=0.0)
        attached meals:   sum(interactions.meal_cost) for NON-"meal" types only
                          (avoids double-counting standalone meals whose
                           payment_amount already equals their meal_cost)
        travel:           sum(interactions.travel_reimbursement) — ALL types,
                          so orphaned-travel "meeting" records are included
                        + sum(speaker_events.travel_reimbursement)
        speaker fees:     sum(speaker_events.speaker_fee)

    CMS totals per HCP per year: sum of all category_total rows from
    cms_category_totals_cache.parquet, plus speaker_total from
    cms_speaker_totals_cache.parquet.

    Returns a dict with:
        total_hcp_years    — int
        perfect_pct        — % within $0.01
        minor_gap_pct      — % gap ≤ 5%
        major_gap_pct      — % gap > 10%  (TYPE 11 — CMS_RECONCILIATION_GAP)
        mean_gap_pct       — float, average |gap| / cms_total
    """
    logger.info("Verifying v2 CMS reconciliation...")

    # ── Internal totals ───────────────────────────────────────────────────────
    # ALL interaction payment_amounts (consulting fees + standalone meal amounts;
    # "meeting" records contribute 0.0 — their value is in travel_reimbursement)
    pay_grp = (
        interactions_df.groupby(["hcp_id", "program_year"])["payment_amount"]
        .sum()
        .reset_index(name="internal_pay")
    )

    # Meal costs attached to non-"meal" interactions (consulting/education with meals).
    # Standalone "meal" rows are already captured by payment_amount above, so
    # we exclude them here to avoid double-counting.
    attached_meal_df = interactions_df[
        interactions_df["interaction_type"] != "meal"
    ].copy()
    attached_meal_df["_mc"] = pd.to_numeric(
        attached_meal_df["meal_cost"], errors="coerce"
    ).fillna(0.0)
    meal_grp = (
        attached_meal_df.groupby(["hcp_id", "program_year"])["_mc"]
        .sum()
        .reset_index(name="internal_meals")
    )

    # Travel reimbursements from interactions
    int_travel = (
        interactions_df.groupby(["hcp_id", "program_year"])["travel_reimbursement"]
        .sum()
        .reset_index(name="int_travel")
    )

    # Travel reimbursements from speaker events
    spk_events_copy = speaker_events_df.rename(
        columns={"speaker_hcp_id": "hcp_id"}
    )
    spk_travel = (
        spk_events_copy.groupby(["hcp_id", "program_year"])["travel_reimbursement"]
        .sum()
        .reset_index(name="spk_travel")
    )

    # Speaker fees
    spk_fees = (
        spk_events_copy.groupby(["hcp_id", "program_year"])["speaker_fee"]
        .sum()
        .reset_index(name="internal_spk_fees")
    )

    # Merge all internal components
    internal = (
        pay_grp
        .merge(meal_grp,   on=["hcp_id", "program_year"], how="outer")
        .merge(int_travel,  on=["hcp_id", "program_year"], how="outer")
        .merge(spk_travel,  on=["hcp_id", "program_year"], how="outer")
        .merge(spk_fees,    on=["hcp_id", "program_year"], how="outer")
        .fillna(0.0)
    )
    internal["internal_total"] = (
        internal["internal_pay"]
        + internal["internal_meals"]
        + internal["int_travel"]
        + internal["spk_travel"]
        + internal["internal_spk_fees"]
    ).round(2)
    internal = internal[["hcp_id", "program_year", "internal_total"]]

    # ── CMS totals (category totals + speaker totals) ─────────────────────────
    cms_cat = load_cms_category_totals()
    cms_spk = load_cms_speaker_totals()

    if cms_cat is None or cms_cat.empty:
        logger.warning("CMS category totals unavailable — cannot run verify_reconciliation_v2")
        return {}

    # Map covered_recipient_profile_id → hcp_id
    cat_merged = cms_cat.merge(
        hcp_master[["hcp_id", "physician_profile_id"]].assign(
            covered_recipient_profile_id=lambda d: d["physician_profile_id"].astype(str)
        ),
        on="covered_recipient_profile_id",
        how="inner",
    )
    cms_cat_grp = (
        cat_merged.groupby(["hcp_id", "program_year"])["category_total"]
        .sum()
        .reset_index(name="cms_category_total")
    )

    cms_grp = cms_cat_grp.copy()
    if cms_spk is not None and not cms_spk.empty:
        spk_merged = cms_spk.merge(
            hcp_master[["hcp_id", "physician_profile_id"]].assign(
                covered_recipient_profile_id=lambda d: d["physician_profile_id"].astype(str)
            ),
            on="covered_recipient_profile_id",
            how="inner",
        )
        spk_grp = (
            spk_merged.groupby(["hcp_id", "program_year"])["speaker_total"]
            .sum()
            .reset_index(name="cms_speaker_total")
        )
        cms_grp = cms_grp.merge(spk_grp, on=["hcp_id", "program_year"], how="outer").fillna(0.0)
        cms_grp["cms_total"] = cms_grp["cms_category_total"] + cms_grp["cms_speaker_total"]
    else:
        cms_grp["cms_total"] = cms_grp["cms_category_total"]

    # ── Reconciliation comparison ─────────────────────────────────────────────
    merged = internal.merge(
        cms_grp[["hcp_id", "program_year", "cms_total"]],
        on=["hcp_id", "program_year"],
        how="inner",
    )
    merged = merged[merged["cms_total"] > 0]
    merged["gap_pct"] = (
        (merged["internal_total"] - merged["cms_total"]).abs()
        / merged["cms_total"]
    )

    total = len(merged)
    result: dict = {
        "total_hcp_years": total,
        "perfect_pct":     round(100 * (merged["gap_pct"] < 0.001).sum() / total, 1) if total else 0,
        "minor_gap_pct":   round(100 * ((merged["gap_pct"] >= 0.001) & (merged["gap_pct"] <= 0.05)).sum() / total, 1) if total else 0,
        "major_gap_pct":   round(100 * (merged["gap_pct"] > 0.10).sum() / total, 1) if total else 0,
        "mean_gap_pct":    round(float(merged["gap_pct"].mean()) * 100, 2) if total else 0.0,
    }
    logger.info(
        f"  v2 Reconciliation: {result['perfect_pct']}% perfect | "
        f"{result['minor_gap_pct']}% minor | "
        f"{result['major_gap_pct']}% major gap (TYPE 11) | "
        f"mean gap {result['mean_gap_pct']}%"
    )
    return result


# ── Step 7: Verify reconciliation ─────────────────────────────────────────────

def verify_reconciliation(
    interactions_df: pd.DataFrame, cms_totals: pd.DataFrame
) -> dict:
    logger.info("Verifying CMS reconciliation...")
    internal = (
        interactions_df.groupby(["hcp_id", "program_year"])["payment_amount"]
        .sum()
        .reset_index()
        .rename(columns={"payment_amount": "internal_total"})
    )

    cms_long = []
    for yr in YEARS:
        col = f"cms_total_{yr}"
        if col not in cms_totals.columns:
            continue
        tmp = cms_totals[["physician_profile_id", col]].copy()
        tmp["hcp_id"] = "HCP_" + tmp["physician_profile_id"].astype(str)
        tmp["program_year"] = yr
        tmp.rename(columns={col: "cms_total"}, inplace=True)
        cms_long.append(tmp[["hcp_id", "program_year", "cms_total"]])

    cms_df = pd.concat(cms_long, ignore_index=True)
    merged = internal.merge(cms_df, on=["hcp_id", "program_year"], how="inner")
    merged["gap_pct"] = (
        (merged["internal_total"] - merged["cms_total"]).abs()
        / merged["cms_total"].replace(0, np.nan)
    ).fillna(0)

    total = len(merged)
    result = {
        "total_hcp_years": total,
        "perfect_pct":   round(100 * (merged["gap_pct"] == 0).sum() / total, 1) if total else 0,
        "minor_gap_pct": round(100 * ((merged["gap_pct"] > 0) & (merged["gap_pct"] <= 0.05)).sum() / total, 1) if total else 0,
        "major_gap_pct": round(100 * (merged["gap_pct"] > 0.10).sum() / total, 1) if total else 0,
    }
    logger.info(
        f"  Reconciliation: {result['perfect_pct']}% perfect | "
        f"{result['minor_gap_pct']}% minor | "
        f"{result['major_gap_pct']}% major gap (TYPE 11)"
    )
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info(f"Synthetic data generator — {TARGET_COMPANY}")
    logger.info(f"Random seed: {RANDOM_SEED}")
    logger.info("=" * 60)

    USE_V2_SPEAKER_GENERATOR = (
        os.environ.get("USE_V2_SPEAKER_GENERATOR", "false").lower() == "true"
    )
    USE_V2_INTERACTIONS_GENERATOR = (
        os.environ.get("USE_V2_INTERACTIONS_GENERATOR", "false").lower() == "true"
    )

    cms_totals = load_cms_hcp_totals()
    hcp_master = generate_hcp_master(cms_totals)

    if USE_V2_INTERACTIONS_GENERATOR:
        logger.info("Using generate_hcp_interactions_v2 (CMS-seeded per-category)")
        interactions = generate_hcp_interactions_v2(hcp_master)
    else:
        interactions = generate_hcp_interactions(hcp_master, cms_totals)

    if USE_V2_SPEAKER_GENERATOR:
        logger.info("Using generate_speaker_events_v2 (CMS-seeded, reconciliation-correct)")
        spk_events = generate_speaker_events_v2(hcp_master)
    else:
        spk_events = generate_speaker_events(hcp_master)

    # Allocate meals and travel only when both v2 generators are active
    if USE_V2_INTERACTIONS_GENERATOR and USE_V2_SPEAKER_GENERATOR:
        logger.info("Running allocate_meals_and_travel (v2 post-generation pass)...")
        interactions, spk_events = allocate_meals_and_travel(
            hcp_master, interactions, spk_events
        )

    attendees    = generate_speaker_attendees(spk_events, hcp_master)

    interactions = apply_violation_flags(interactions, "interactions")
    spk_events   = apply_violation_flags(spk_events,   "speaker_events")
    attendees    = apply_violation_flags(attendees,     "attendees")

    if USE_V2_INTERACTIONS_GENERATOR and USE_V2_SPEAKER_GENERATOR:
        recon = verify_reconciliation_v2(hcp_master, interactions, spk_events)
    else:
        recon = verify_reconciliation(interactions, cms_totals)

    logger.info("Saving to S3...")
    save_to_s3(hcp_master,   S3_BUCKET, f"{S3_SYNTHETIC_PREFIX}/hcp_master/hcp_master.parquet")
    save_to_s3(interactions, S3_BUCKET, f"{S3_SYNTHETIC_PREFIX}/hcp_interactions/hcp_interactions.parquet")
    save_to_s3(spk_events,   S3_BUCKET, f"{S3_SYNTHETIC_PREFIX}/speaker_programs/speaker_program_events.parquet")
    save_to_s3(attendees,    S3_BUCKET, f"{S3_SYNTHETIC_PREFIX}/speaker_programs/speaker_program_attendees.parquet")

    elapsed = time.time() - start
    total_records = len(hcp_master) + len(interactions) + len(spk_events) + len(attendees)

    logger.info("\n" + "=" * 60)
    logger.info("GENERATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  hcp_master:                  {len(hcp_master):>8,}")
    logger.info(f"  hcp_interactions:            {len(interactions):>8,}")
    logger.info(f"  speaker_program_events:      {len(spk_events):>8,}")
    logger.info(f"  speaker_program_attendees:   {len(attendees):>8,}")
    logger.info(f"  TOTAL:                       {total_records:>8,}")

    logger.info("\n  Violation distribution (interactions):")
    for sev in ["none", "low", "medium", "high"]:
        n = (interactions["violation_severity"] == sev).sum()
        logger.info(f"    {sev:<8}: {n:>7,}  ({100*n/len(interactions):.1f}%)")

    all_types = [t for row in interactions["violation_types"] if row for t in row.split(",")]
    if all_types:
        from collections import Counter
        logger.info("\n  Top violation types (interactions):")
        for vtype, cnt in Counter(all_types).most_common(5):
            logger.info(f"    {vtype:<40}: {cnt:,}")

    logger.info(f"\n  Reconciliation:")
    logger.info(f"    Perfect:       {recon.get('perfect_pct', 'n/a')}%")
    logger.info(f"    Minor ≤5%:     {recon.get('minor_gap_pct', 'n/a')}%")
    logger.info(f"    Major >10%:    {recon.get('major_gap_pct', 'n/a')}%  ← TYPE 11 violations")
    if "mean_gap_pct" in recon:
        logger.info(f"    Mean gap:      {recon['mean_gap_pct']}%")
    logger.info(f"\n  Total time: {elapsed:.0f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
