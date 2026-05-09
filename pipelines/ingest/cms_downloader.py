# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
cms_downloader.py
-----------------
Streams raw CMS Open Payments CSV files directly to S3.
No filtering, no transformation — raw data only.

Outputs one file per year:
  s3://{bucket}/raw/cms_open_payments/{year}/OP_DTL_GNRL_PGYR{year}.csv

Usage:
  python pipelines/ingest/cms_downloader.py
"""

import os
import time

import boto3
import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

YEARS = [2022, 2023, 2024]

CMS_URLS = {
    2022: (
        "https://download.cms.gov/openpayments/PGYR2022_P06302025_06162025/"
        "OP_DTL_GNRL_PGYR2022_P06302025_06162025.csv"
    ),
    2023: (
        "https://download.cms.gov/openpayments/PGYR2023_P01302025_01212025/"
        "OP_DTL_GNRL_PGYR2023_P01302025_01212025.csv"
    ),
    2024: (
        "https://download.cms.gov/openpayments/PGYR2024_P06302025_06162025/"
        "OP_DTL_GNRL_PGYR2024_P06302025_06162025.csv"
    ),
}

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_RAW_PREFIX = "raw/cms_open_payments"

# Multipart upload requires each part to be at least 5 MB (AWS minimum).
# 10 MB chunks balance memory usage and upload efficiency.
CHUNK_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# ── S3 client ─────────────────────────────────────────────────────────────────

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


# ── Core upload ───────────────────────────────────────────────────────────────

def stream_csv_to_s3(year: int, url: str, bucket: str, key: str) -> dict:
    """
    Stream a remote CSV directly to S3 using multipart upload.
    Data flows: CMS server → memory buffer (10 MB at a time) → S3.
    Nothing is written to local disk.

    Returns a summary dict with size and duration.
    """
    logger.info(f"[{year}] Starting download from CMS...")
    logger.info(f"  URL: {url}")
    logger.info(f"  Destination: s3://{bucket}/{key}")

    # Open an HTTP streaming connection — response body is not read yet
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()

    # Content-Length lets us show % progress (not always present)
    total_bytes = int(response.headers.get("content-length", 0))
    if total_bytes:
        logger.info(f"  File size: {total_bytes / 1024**3:.2f} GB")
    else:
        logger.info("  File size: unknown (no content-length header)")

    # ── Initiate multipart upload ─────────────────────────────────────────────
    # Multipart upload works in three steps:
    #   1. create_multipart_upload  → get an upload_id
    #   2. upload_part (repeated)   → upload chunks, collect ETags
    #   3. complete_multipart_upload → tell S3 to assemble the parts
    mpu = s3.create_multipart_upload(Bucket=bucket, Key=key, ContentType="text/csv")
    upload_id = mpu["UploadId"]
    parts = []
    part_number = 1
    uploaded_bytes = 0
    buffer = b""
    start_time = time.time()

    try:
        for raw_chunk in response.iter_content(chunk_size=CHUNK_SIZE_BYTES):
            buffer += raw_chunk
            # Only upload once we have a full CHUNK_SIZE_BYTES part
            # (AWS requires each part except the last to be >= 5 MB)
            if len(buffer) >= CHUNK_SIZE_BYTES:
                part = s3.upload_part(
                    Bucket=bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=buffer,
                )
                parts.append({"PartNumber": part_number, "ETag": part["ETag"]})
                uploaded_bytes += len(buffer)
                buffer = b""

                # Progress logging
                if total_bytes:
                    pct = uploaded_bytes / total_bytes * 100
                    logger.info(
                        f"  [{year}] {uploaded_bytes / 1024**2:.0f} MB"
                        f" / {total_bytes / 1024**2:.0f} MB  ({pct:.1f}%)"
                    )
                else:
                    logger.info(f"  [{year}] {uploaded_bytes / 1024**2:.0f} MB uploaded")

                part_number += 1

        # Upload any remaining bytes as the final part
        if buffer:
            part = s3.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=buffer,
            )
            parts.append({"PartNumber": part_number, "ETag": part["ETag"]})
            uploaded_bytes += len(buffer)

        # ── Complete the multipart upload ─────────────────────────────────────
        s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    except Exception as exc:
        # If anything goes wrong, abort the multipart upload to avoid
        # leaving incomplete parts in S3 (which incur storage charges)
        logger.error(f"Upload failed: {exc}. Aborting multipart upload.")
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise

    elapsed = time.time() - start_time
    logger.info(
        f"[{year}] Upload complete: {uploaded_bytes / 1024**2:.1f} MB"
        f" in {elapsed:.0f}s  ({uploaded_bytes / 1024**2 / elapsed:.1f} MB/s)"
    )

    return {
        "year": year,
        "s3_path": f"s3://{bucket}/{key}",
        "size_mb": round(uploaded_bytes / 1024**2, 1),
        "elapsed_s": round(elapsed, 1),
    }


# ── Orchestration ─────────────────────────────────────────────────────────────

def upload_all_years() -> None:
    results = []

    for year in YEARS:
        url = CMS_URLS[year]
        key = f"{S3_RAW_PREFIX}/{year}/OP_DTL_GNRL_PGYR{year}.csv"
        result = stream_csv_to_s3(year, url, S3_BUCKET, key)
        results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("UPLOAD SUMMARY")
    logger.info("=" * 60)
    for r in results:
        logger.info(
            f"  {r['year']} | {r['size_mb']:>8.1f} MB"
            f" | {r['elapsed_s']:>6.0f}s"
            f" | {r['s3_path']}"
        )
    logger.info("=" * 60)


def main() -> None:
    logger.info("CMS Open Payments — raw ingest to S3")
    logger.info(f"Bucket: {S3_BUCKET}")
    logger.info(f"Years:  {YEARS}")
    upload_all_years()
    logger.info("Done.")


if __name__ == "__main__":
    main()
