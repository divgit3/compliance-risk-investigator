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
    "Primary Care":     {"local":  750, "regional": 1500, "national": 2500},
    "Other":            {"local":  750, "regional": 1500, "national": 2500},
}

SPECIALTY_DISTRIBUTION = {
    "Gastroenterology": 0.30,
    "Oncology":         0.25,
    "Neurology":        0.20,
    "Rare Disease":     0.15,
    "Primary Care":     0.10,
}

MEAL_TYPES = ["breakfast", "lunch", "dinner"]
MEAL_TYPE_DISTRIBUTION = {"breakfast": 0.10, "lunch": 0.70, "dinner": 0.20}
MEAL_LIMITS = {"breakfast": 30, "lunch": 75, "dinner": 125}

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
    "Primary Care":     ["hypertension", "type 2 diabetes", "hyperlipidemia"],
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
    rep_ids = [f"REP_{i:04d}" for i in range(1, 201)]
    rep_territory = {r: rng.choice(TERRITORIES) for r in rep_ids}

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
        BAD_VENUES = {"restaurant", "entertainment_venue", "luxury_resort", "sports_venue"}

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

    cms_totals   = load_cms_hcp_totals()
    hcp_master   = generate_hcp_master(cms_totals)
    interactions = generate_hcp_interactions(hcp_master, cms_totals)
    spk_events   = generate_speaker_events(hcp_master)
    attendees    = generate_speaker_attendees(spk_events, hcp_master)

    interactions = apply_violation_flags(interactions, "interactions")
    spk_events   = apply_violation_flags(spk_events,   "speaker_events")
    attendees    = apply_violation_flags(attendees,     "attendees")

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
    logger.info(f"    Perfect:       {recon['perfect_pct']}%")
    logger.info(f"    Minor ≤5%:     {recon['minor_gap_pct']}%")
    logger.info(f"    Major >10%:    {recon['major_gap_pct']}%  ← TYPE 11 violations")
    logger.info(f"\n  Total time: {elapsed:.0f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
