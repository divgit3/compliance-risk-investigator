"""
policy_doc_loader.py
--------------------
Downloads 4 real public policy PDFs, generates 1 synthetic Nova Pharma
internal policy PDF, extracts and chunks text (~500 tokens, 50-token overlap),
tags each chunk with violation types, and uploads everything to S3.

S3 layout:
  s3://compliance-risk-investigator/raw/policy_docs/
    pdfs/
      phrma_code_2022.pdf
      oig_speaker_fraud_alert_2020.pdf
      oig_cpg_pharmaceutical.pdf
      cms_open_payments_data_dictionary.pdf
      nova_pharma_internal_policy_SYNTHETIC.pdf
    chunks/
      phrma_code_2022.json
      oig_speaker_fraud_alert_2020.json
      oig_cpg_pharmaceutical.json
      cms_open_payments_data_dictionary.json
      nova_pharma_internal_policy_SYNTHETIC.json

Usage:
  python pipelines/ingest/policy_doc_loader.py
"""

import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import List

import boto3
import pdfplumber
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from fpdf import FPDF
from loguru import logger

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

S3_BUCKET    = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_PREFIX    = "raw/policy_docs"
AWS_REGION   = os.getenv("AWS_REGION", "us-east-1")

CHUNK_TOKENS    = 500   # target tokens per chunk
OVERLAP_TOKENS  = 50    # overlap between consecutive chunks
WORDS_PER_TOKEN = 0.75  # rough approximation: 1 token ≈ 0.75 words

CHUNK_SIZE = int(CHUNK_TOKENS  * WORDS_PER_TOKEN)   # ~375 words
OVERLAP    = int(OVERLAP_TOKENS * WORDS_PER_TOKEN)  # ~37 words

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; compliance-research-bot/1.0)"
}

# ── Document registry ─────────────────────────────────────────────────────────

@dataclass
class PolicyDoc:
    slug:   str   # used as filename stem
    title:  str   # human-readable name
    source: str   # "public" | "synthetic"
    url:    str = ""  # download URL (public only)


POLICY_DOCS = [
    PolicyDoc(
        slug="phrma_code_2022",
        title="PhRMA Code on Interactions with Healthcare Professionals (2022)",
        source="public",
        url="https://cdn.aglty.io/phrma/global/resources/PhRMA%20Code%20-%20Final.pdf",
    ),
    PolicyDoc(
        slug="oig_speaker_fraud_alert_2020",
        title="OIG Special Fraud Alert: Speaker Programs (November 2020)",
        source="public",
        url="https://oig.hhs.gov/documents/special-fraud-alerts/865/SpecialFraudAlertSpeakerPrograms.pdf",
    ),
    PolicyDoc(
        slug="oig_cpg_pharmaceutical",
        title="OIG Compliance Program Guidance for Pharmaceutical Manufacturers (2003)",
        source="public",
        url="https://oig.hhs.gov/documents/compliance-guidance/799/050503FRCPGPharmac.pdf",
    ),
    PolicyDoc(
        slug="cms_open_payments_data_dictionary",
        title="CMS Open Payments Data Dictionary",
        source="public",
        url="https://www.cms.gov/OpenPayments/Downloads/OpenPaymentsDataDictionary.pdf",
    ),
    PolicyDoc(
        slug="nova_pharma_internal_policy_SYNTHETIC",
        title="Nova Pharma Inc. - HCP Engagement and Compliance Policy (SYNTHETIC)",
        source="synthetic",
    ),
]

# ── Violation type taxonomy ───────────────────────────────────────────────────

VIOLATION_TYPES = {
    "excessive_meal": [
        "meal", "food", "beverage", "restaurant", "catering", "dining",
    ],
    "excessive_speaker_fee": [
        "speaker fee", "honoraria", "honorarium", "speaking fee",
        "fair market value", "fmv", "consulting fee",
    ],
    "sham_speaker_program": [
        "speaker program", "speaker bureau", "educational program",
        "sham", "attendance", "attendee",
    ],
    "inappropriate_venue": [
        "venue", "resort", "entertainment", "luxury", "sports",
        "recreational", "lavish",
    ],
    "frequency_violation": [
        "frequency", "repeat", "multiple", "excessive number",
        "too many", "times attended",
    ],
    "fmv_violation": [
        "fair market value", "fmv", "rate card", "market rate",
        "reasonable compensation",
    ],
    "annual_cap": [
        "annual", "aggregate", "cap", "limit", "total compensation",
        "75,000", "$75",
    ],
    "off_label": [
        "off-label", "off label", "unapproved", "indication", "promotional",
    ],
    "kickback": [
        "kickback", "anti-kickback", "remuneration", "inducement",
        "bribe", "corrupt",
    ],
    "disclosure_failure": [
        "disclosure", "report", "open payments", "sunshine act",
        "transparency", "cms reporting",
    ],
    "formulary_influence": [
        "formulary", "prescribing", "prescriber", "formulary placement",
        "market access",
    ],
    "false_claims": [
        "false claim", "false claims act", "fca", "fraudulent claim",
        "medicare", "medicaid",
    ],
    "stark_violation": [
        "stark", "self-referral", "referral", "financial relationship",
    ],
    "documentation_failure": [
        "documentation", "record", "log", "attestation", "contract",
        "written agreement",
    ],
}


def tag_chunk(text: str) -> List[str]:
    """Return list of violation type keys whose keywords appear in text."""
    text_lower = text.lower()
    return [
        vtype
        for vtype, keywords in VIOLATION_TYPES.items()
        if any(kw in text_lower for kw in keywords)
    ]


# ── PDF download ──────────────────────────────────────────────────────────────

def download_pdf(doc: PolicyDoc) -> bytes:
    """Download PDF bytes from doc.url with up to 3 retries."""
    logger.info(f"Downloading: {doc.title}")
    for attempt in range(3):
        try:
            resp = requests.get(
                doc.url,
                headers=DOWNLOAD_HEADERS,
                timeout=60,
                stream=True,
            )
            resp.raise_for_status()
            data = resp.content
            logger.info(f"  Downloaded {len(data):,} bytes")
            return data
        except requests.RequestException as e:
            logger.warning(f"  Attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(5)
    raise RuntimeError(f"Failed to download {doc.slug} after 3 attempts")


# ── Synthetic PDF generation ──────────────────────────────────────────────────

NOVA_PHARMA_POLICY_TEXT = """
NOVA PHARMA INC.
HCP ENGAGEMENT AND COMPLIANCE POLICY

SYNTHETIC DOCUMENT - FOR DEMONSTRATION PURPOSES ONLY
NOT A REAL COMPANY OR REAL POLICY

Effective Date: January 1, 2022
Policy Number: NP-COMPLIANCE-001
Version: 3.2

SECTION 1 - PURPOSE AND SCOPE

Nova Pharma Inc. is committed to ethical interactions with healthcare professionals
(HCPs). This policy governs all engagements between Nova Pharma field personnel and
HCPs, including meals, speaker programs, consulting arrangements, and advisory board
participation. All employees, contractors, and agents acting on behalf of Nova Pharma
must comply with this policy, applicable law (including the federal Anti-Kickback
Statute, the False Claims Act, and the Physician Payments Sunshine Act), and industry
codes including the PhRMA Code on Interactions with Healthcare Professionals.

SECTION 2 - MEAL AND HOSPITALITY LIMITS

2.1 Per-Meal Limits by Meal Type

Nova Pharma limits meal expenditure per HCP per occasion as follows:

  Breakfast:  $25 per person (including tax and tip)
  Lunch:      $50 per person (including tax and tip)
  Dinner:     $100 per person (including tax and tip)

Meals must be modest, occur in a setting conducive to educational discussion, and
must not be provided at entertainment venues, luxury resorts, or sporting events.
Alcoholic beverages are included in the per-meal cap. Meals may only be provided
in connection with a legitimate educational interaction.

2.2 Annual Meal Cap

No HCP may receive meals from Nova Pharma field personnel exceeding $500 in any
rolling 12-month period.

2.3 Prohibited Venues

Meals may not be provided at the following venue types:
  - Entertainment venues (theaters, arenas, concert halls)
  - Luxury resorts or hotels rated 5-star
  - Sporting events or sports venues
  - Recreational facilities (golf clubs, spas)

SECTION 3 - SPEAKER PROGRAM POLICY

3.1 Speaker Selection and Fair Market Value

Nova Pharma engages qualified HCPs as speakers for educational programs on approved
product indications. Speaker fees must reflect fair market value (FMV) for the service
rendered and must not be influenced by the HCP's prescribing history.

FMV Rate Card (speaker fees per program, by specialty and tier):

  National Tier (Top 10% recognized experts):
    Physician - Primary Care:          $1,500 per program
    Physician - Specialist:            $2,500 per program
    Physician - Sub-Specialist:        $3,500 per program
    Nurse Practitioner / PA:           $1,000 per program

  Regional Tier (Regional thought leaders):
    Physician - Primary Care:          $750 per program
    Physician - Specialist:            $1,250 per program
    Physician - Sub-Specialist:        $1,750 per program
    Nurse Practitioner / PA:           $500 per program

  Local Tier (Local clinical educators):
    Physician - Primary Care:          $500 per program
    Physician - Specialist:            $750 per program
    Physician - Sub-Specialist:        $1,000 per program
    Nurse Practitioner / PA:           $300 per program

3.2 Annual Speaker Fee Cap

No HCP may receive total speaker fees from Nova Pharma exceeding $75,000 in any
calendar year. This cap applies across all product lines and therapeutic areas.
Any engagement that would cause an HCP to exceed this cap requires written approval
from the Chief Compliance Officer.

3.3 Speaker Program Attendance Rules

  - Minimum attendees: 3 HCPs per program (excluding the speaker)
  - Maximum meal value at speaker programs: $100 per attendee
  - Programs must have a defined educational agenda
  - No HCP may attend more than 3 programs on the same topic in a rolling 12 months
    unless they can demonstrate a new clinical need for the information
  - Attendees must sign an attestation confirming educational value received

3.4 Repeat Speaker Restrictions

No HCP may present as a speaker more than 6 times per approved topic per calendar
year. If utilization falls below a minimum threshold of 3 programs per year, the
speaker contract must be reviewed for continuation.

SECTION 4 - CONSULTING AND ADVISORY BOARD ARRANGEMENTS

4.1 Legitimate Need Requirement

Nova Pharma may engage HCPs as consultants or advisory board members only when
there is a documented legitimate business need for the HCP's expertise. Consulting
arrangements may not be used to reward past prescribing or induce future prescribing.

4.2 Consulting Fee Limits

Consulting fees must be consistent with the FMV rate card in Section 3.1. Total
consulting compensation (excluding speaker fees) may not exceed $50,000 per HCP
per calendar year without CCO approval.

4.3 Advisory Board Composition

Advisory boards must comprise no more than 20 members. Meetings must have defined
agendas and documented outcomes. Nova Pharma must retain advisory board meeting
minutes for a minimum of 7 years.

SECTION 5 - INTERACTION FREQUENCY RULES

5.1 Office Visit Frequency

Field personnel may visit an HCP's office for educational detailing no more than:
  - 12 times per calendar year per HCP for primary care physicians
  - 8 times per calendar year per HCP for specialists
  - 6 times per calendar year per HCP for sub-specialists

5.2 Combined Engagement Cap

The total number of paid engagements (speaker programs, consulting, advisory board)
for any single HCP in a calendar year may not exceed 20. When combined meal interactions
and paid engagements are considered together, the aggregate engagement frequency limit
is 30 interactions per HCP per year.

SECTION 6 - OPEN PAYMENTS / SUNSHINE ACT REPORTING

Nova Pharma is required under the Physician Payments Sunshine Act (42 U.S.C. 1320a-7h)
to report all transfers of value to covered recipients to CMS annually. This includes
meals, speaker fees, consulting fees, travel, and educational materials exceeding the
de minimis threshold.

Reportable categories include:
  - Food and beverage
  - Honoraria / speaker fees
  - Consulting fees
  - Travel and lodging
  - Education
  - Grant
  - Charitable contribution
  - Royalty or license
  - Current or prospective ownership or investment interest
  - Direct compensation for serving as faculty or as a speaker for a
    non-accredited and non-certified continuing education program
  - Gift

The de minimis threshold is $10 per transfer and $100 aggregate per calendar year.
All reportable transfers of value must be logged in the Nova Pharma CRM system
within 30 days of the interaction.

SECTION 7 - ANTI-KICKBACK STATUTE COMPLIANCE

All HCP engagements must be structured to comply with the federal Anti-Kickback
Statute (42 U.S.C. 1320a-7b(b)). Payments to HCPs must:
  - Reflect fair market value for legitimate services
  - Not be conditioned on prescribing, referral, or formulary placement decisions
  - Be supported by a written agreement signed before services are rendered
  - Be for services that are actually rendered and documented

Nova Pharma maintains a formal FMV assessment process, documented annually, to
ensure all compensation rates remain within market norms.

SECTION 8 - DOCUMENTATION REQUIREMENTS

8.1 Required Documentation

For every HCP engagement, Nova Pharma personnel must document:
  - Date, time, and location of the interaction
  - Names and credentials of all HCPs present
  - Educational topic discussed
  - Product(s) discussed (must be within approved indications)
  - Meal cost (if applicable), including itemized receipt
  - Attendee attestation (for speaker programs)

8.2 Retention

All HCP engagement records must be retained for a minimum of 7 years from the
date of the interaction.

SECTION 9 - VIOLATIONS AND CONSEQUENCES

Violations of this policy may result in:
  - Written warning
  - Mandatory retraining
  - Suspension or termination of employment
  - Referral to the Office of Inspector General
  - Civil or criminal penalties under applicable law

Any employee who suspects a violation of this policy must report it to the
Compliance Hotline (1-800-NP-COMPLY) or via the anonymous web portal.
Nova Pharma prohibits retaliation against good-faith reporters.

SECTION 10 - POLICY GOVERNANCE

This policy is reviewed annually by the Chief Compliance Officer and approved by
the Board of Directors Audit and Compliance Committee. Questions regarding
interpretation of this policy should be directed to the compliance department.

SYNTHETIC DOCUMENT - FOR DEMONSTRATION PURPOSES ONLY
NOT A REAL COMPANY OR REAL POLICY
"""


def generate_synthetic_pdf(doc: PolicyDoc) -> bytes:
    """Generate the Nova Pharma synthetic policy as a PDF using fpdf2."""
    logger.info(f"Generating synthetic PDF: {doc.title}")

    class PolicyPDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(180, 0, 0)
            self.cell(
                0, 6,
                "SYNTHETIC DOCUMENT - NOT REAL - FOR DEMONSTRATION PURPOSES ONLY",
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
            self.set_text_color(0, 0, 0)
            self.ln(2)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            self.cell(
                0, 6,
                f"SYNTHETIC  |  Nova Pharma Inc. Compliance Policy  |  Page {self.page_no()}",
                align="C",
            )

    pdf = PolicyPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)

    for para in NOVA_PHARMA_POLICY_TEXT.strip().split("\n"):
        para = para.rstrip()
        # Helvetica (Latin-1) does not support em-dash - replace with ASCII equivalent
        para = para.replace("\u2014", " - ").replace("\u2013", "-")
        if para == "":
            pdf.ln(3)
            continue

        # Always reset X to left margin before any multi_cell to avoid cursor drift
        pdf.set_x(pdf.l_margin)
        w = pdf.w - pdf.l_margin - pdf.r_margin

        if re.match(r"^SECTION \d+", para) or re.match(r"^\d+\.\d+ ", para):
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(w, 6, para)
            pdf.set_font("Helvetica", size=10)
        elif re.match(
            r"^(NOVA PHARMA|HCP ENGAGEMENT|SYNTHETIC DOCUMENT|NOT A REAL|"
            r"Effective|Policy Number|Version)",
            para,
        ):
            pdf.set_font("Helvetica", "B", 12 if "NOVA PHARMA" in para else 10)
            pdf.set_text_color(0, 51, 102)
            pdf.multi_cell(w, 7, para, align="C")
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", size=10)
        else:
            pdf.multi_cell(w, 5, para)

    buf = io.BytesIO()
    pdf.output(buf)
    data = buf.getvalue()
    logger.info(f"  Generated {len(data):,} bytes")
    return data


# ── Text extraction and chunking ──────────────────────────────────────────────

def extract_text(pdf_bytes: bytes) -> str:
    """Extract full text from PDF bytes using pdfplumber."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() for page in pdf.pages]
    return "\n".join(p for p in pages if p)


def chunk_text(text: str, doc: PolicyDoc) -> List[dict]:
    """
    Split text into overlapping word-based chunks (~500 tokens each).
    Returns list of chunk dicts ready for JSON serialization.
    """
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()

    chunks = []
    start = 0
    chunk_idx = 0

    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk_words = words[start:end]
        chunk_text_str = " ".join(chunk_words)

        chunks.append({
            "doc_slug":       doc.slug,
            "doc_title":      doc.title,
            "doc_source":     doc.source,
            "chunk_id":       f"{doc.slug}_{chunk_idx:04d}",
            "chunk_idx":      chunk_idx,
            "text":           chunk_text_str,
            "word_count":     len(chunk_words),
            "violation_tags": tag_chunk(chunk_text_str),
        })

        chunk_idx += 1
        if end == len(words):
            break
        start = end - OVERLAP

    logger.info(f"  Chunked into {len(chunks)} chunks")
    return chunks


# ── S3 upload ─────────────────────────────────────────────────────────────────

s3 = boto3.client("s3", region_name=AWS_REGION)


def upload_to_s3(data: bytes, key: str, content_type: str) -> None:
    """Upload bytes to S3."""
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        logger.info(f"  Uploaded s3://{S3_BUCKET}/{key}  ({len(data):,} bytes)")
    except ClientError as e:
        logger.error(f"  S3 upload failed for {key}: {e}")
        raise


# ── Per-document pipeline ─────────────────────────────────────────────────────

def process_doc(doc: PolicyDoc) -> None:
    """Download/generate, extract, chunk, tag, and upload one document."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing: {doc.slug}")

    # 1. Get PDF bytes
    pdf_bytes = (
        generate_synthetic_pdf(doc) if doc.source == "synthetic"
        else download_pdf(doc)
    )

    # 2. Upload raw PDF
    upload_to_s3(pdf_bytes, f"{S3_PREFIX}/pdfs/{doc.slug}.pdf", "application/pdf")

    # 3. Extract text
    logger.info("  Extracting text...")
    text = extract_text(pdf_bytes)
    word_count = len(text.split())
    logger.info(f"  Extracted {word_count:,} words")
    if word_count < 50:
        logger.warning("  Very little text - PDF may be image-only")

    # 4. Chunk + tag
    chunks = chunk_text(text, doc)
    unique_tags = sorted({t for c in chunks for t in c["violation_tags"]})
    logger.info(f"  Violation tags found: {unique_tags}")

    # 5. Upload chunks JSON
    payload = json.dumps(chunks, indent=2, ensure_ascii=False).encode("utf-8")
    upload_to_s3(payload, f"{S3_PREFIX}/chunks/{doc.slug}.json", "application/json")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Starting policy_doc_loader")
    logger.info(f"  S3 bucket:  {S3_BUCKET}")
    logger.info(f"  S3 prefix:  {S3_PREFIX}")
    logger.info(f"  Chunk size: ~{CHUNK_TOKENS} tokens ({CHUNK_SIZE} words)")
    logger.info(f"  Overlap:    ~{OVERLAP_TOKENS} tokens ({OVERLAP} words)")
    logger.info(f"  Documents:  {len(POLICY_DOCS)}")

    success, failed = 0, []
    for doc in POLICY_DOCS:
        try:
            process_doc(doc)
            success += 1
        except Exception as e:
            logger.error(f"Failed to process {doc.slug}: {e}")
            failed.append(doc.slug)

    logger.info(f"\n{'='*60}")
    logger.info(f"Done. {success}/{len(POLICY_DOCS)} documents processed.")
    if failed:
        logger.error(f"Failed: {failed}")
    else:
        logger.info("All documents processed successfully.")


if __name__ == "__main__":
    main()
