# Copyright (c) 2026 Divya Rajaraman
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""
glue_crawler.py
---------------
Creates a Glue Data Catalog database and crawler that scans the
raw CMS Open Payments CSVs in S3, infers their schema, and
registers them as queryable tables.

After this runs, Athena (and dbt-athena) can query:
  compliance_risk_raw.op_dtl_gnrl_pgyr2022
  compliance_risk_raw.op_dtl_gnrl_pgyr2023
  compliance_risk_raw.op_dtl_gnrl_pgyr2024

Prerequisites:
  - cms_downloader.py must have run successfully
  - GLUE_ROLE_ARN must be set in .env

Usage:
  python pipelines/ingest/glue_crawler.py
"""

import os
import time

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

GLUE_DATABASE   = "compliance_risk_raw"
CRAWLER_NAME    = "compliance-cms-raw-crawler"
CLASSIFIER_NAME = "cms-csv-classifier"
S3_BUCKET       = os.getenv("S3_BUCKET_NAME", "compliance-risk-investigator")
S3_TARGET_PATH  = f"s3://{S3_BUCKET}/raw/cms_open_payments/"
GLUE_ROLE_ARN   = os.getenv("GLUE_ROLE_ARN")
AWS_REGION      = os.getenv("AWS_REGION", "us-east-1")

POLL_INTERVAL   = 30  # seconds between crawler status checks

EXPECTED_TABLES = [
    # Glue treats year subfolders as partitions and creates one unified table.
    # Table name is derived from the parent S3 prefix (cms_open_payments).
    "cms_open_payments",
]

# ── Glue client ───────────────────────────────────────────────────────────────

glue = boto3.client("glue", region_name=AWS_REGION)


# ── Setup ─────────────────────────────────────────────────────────────────────

def create_glue_database(db_name: str) -> None:
    """Create the Glue database if it doesn't already exist."""
    try:
        glue.create_database(
            DatabaseInput={
                "Name": db_name,
                "Description": (
                    "Raw CMS Open Payments data catalog. "
                    "Populated by Glue crawler from S3 raw layer."
                ),
            }
        )
        logger.info(f"Created Glue database: {db_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            logger.info(f"Glue database already exists: {db_name}")
        else:
            raise


def create_csv_classifier(classifier_name: str) -> None:
    """
    Register a Glue CSV classifier with explicit quote/delimiter settings.
    Without this, Glue's auto-detection misparses quoted fields containing
    commas (e.g. "Takeda Pharmaceuticals U.S.A., Inc."), shifting all
    subsequent columns by 1.
    """
    try:
        glue.create_classifier(
            CsvClassifier={
                "Name": classifier_name,
                "Delimiter": ",",
                "QuoteSymbol": '"',
                "ContainsHeader": "PRESENT",
                "DisableValueTrimming": False,
                "AllowSingleColumn": False,
            }
        )
        logger.info(f"Created CSV classifier: {classifier_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            glue.update_classifier(
                CsvClassifier={
                    "Name": classifier_name,
                    "Delimiter": ",",
                    "QuoteSymbol": '"',
                    "ContainsHeader": "PRESENT",
                    "DisableValueTrimming": False,
                    "AllowSingleColumn": False,
                }
            )
            logger.info(f"CSV classifier already exists — updated: {classifier_name}")
        else:
            raise


def create_crawler(
    crawler_name: str, db_name: str, s3_path: str, role_arn: str
) -> None:
    """
    Create the Glue crawler if it doesn't exist; update it if it does.
    The crawler scans s3_path, infers CSV schemas, and writes table
    definitions into the Glue Data Catalog under db_name.
    """
    crawler_config = {
        "Name": crawler_name,
        "Role": role_arn,
        "DatabaseName": db_name,
        "Description": "Crawls raw CMS Open Payments CSVs and registers schemas.",
        "Classifiers": [CLASSIFIER_NAME],
        "Targets": {
            "S3Targets": [
                {
                    "Path": s3_path,
                    # Recurse into year subfolders (2022/, 2023/, 2024/)
                    "Exclusions": [],
                }
            ]
        },
        "SchemaChangePolicy": {
            # ADD new columns if CMS adds them; LOG but don't delete removed ones
            "UpdateBehavior": "UPDATE_IN_DATABASE",
            "DeleteBehavior": "LOG",
        },
        "RecrawlPolicy": {
            # Only crawl new/changed files on subsequent runs
            "RecrawlBehavior": "CRAWL_EVERYTHING",
        },
        "Configuration": (
            '{"Version":1.0,'
            '"CrawlerOutput":{"Partitions":{"AddOrUpdateBehavior":"InheritFromTable"}}}'
        ),
    }

    try:
        glue.create_crawler(**crawler_config)
        logger.info(f"Created crawler: {crawler_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            logger.info(f"Crawler already exists — updating: {crawler_name}")
            # Remove keys that UpdateCrawler doesn't accept
            crawler_config.pop("Name")
            glue.update_crawler(Name=crawler_name, **crawler_config)
            logger.info(f"Crawler updated: {crawler_name}")
        else:
            raise


OPEN_CSV_SERDE = {
    "SerializationLibrary": "org.apache.hadoop.hive.serde2.OpenCSVSerde",
    "Parameters": {
        "separatorChar": ",",
        "quoteChar":     '"',
        "escapeChar":    "\\",
    },
}


def patch_table_serde(db_name: str, table_name: str) -> None:
    """
    After the crawler runs, patch the Glue table and all its partitions to use
    OpenCSVSerde instead of the default LazySimpleSerDe.

    Background: Glue's custom CSV classifier with QuoteSymbol='"' fixes schema
    inference (column names/types) but does NOT control the SerDe used at query
    time. Glue always writes LazySimpleSerDe into the StorageDescriptor after a
    crawl. LazySimpleSerDe does not handle RFC 4180 quoted fields, so company
    names like "Takeda Pharmaceuticals U.S.A., Inc." (containing a comma) cause
    all subsequent columns in that row to shift by 1.

    OpenCSVSerde handles quoted fields correctly. This patch must be applied to
    both the table AND every partition — Athena uses the partition-level SerDe
    for actual reads, ignoring the table-level setting when they differ.
    """
    import copy

    # ── Patch table ────────────────────────────────────────────────────────────
    resp = glue.get_table(DatabaseName=db_name, Name=table_name)
    table = resp["Table"]
    sd = copy.deepcopy(table["StorageDescriptor"])
    sd["SerdeInfo"] = OPEN_CSV_SERDE
    params = dict(table.get("Parameters", {}))
    # Treat unparseable values (e.g. empty string in a bigint column) as NULL
    # instead of raising BAD_DATA in Athena.
    params["use.null.for.invalid.data"] = "true"
    glue.update_table(
        DatabaseName=db_name,
        TableInput={
            "Name":              table["Name"],
            "Description":       table.get("Description", ""),
            "StorageDescriptor": sd,
            "PartitionKeys":     table.get("PartitionKeys", []),
            "TableType":         table.get("TableType", ""),
            "Parameters":        params,
        },
    )
    logger.info(f"Patched table SerDe to OpenCSVSerde: {db_name}.{table_name}")

    # ── Patch all partitions ───────────────────────────────────────────────────
    parts = glue.get_partitions(DatabaseName=db_name, TableName=table_name)
    if not parts["Partitions"]:
        logger.info("No partitions found — skipping partition SerDe patch.")
        return

    entries = []
    for p in parts["Partitions"]:
        psd = copy.deepcopy(p["StorageDescriptor"])
        psd["SerdeInfo"] = OPEN_CSV_SERDE
        pparams = dict(p.get("Parameters", {}))
        pparams["use.null.for.invalid.data"] = "true"
        entries.append({
            "PartitionValueList": p["Values"],
            "PartitionInput": {
                "Values":              p["Values"],
                "StorageDescriptor":   psd,
                "Parameters":          pparams,
            },
        })

    resp = glue.batch_update_partition(
        DatabaseName=db_name,
        TableName=table_name,
        Entries=entries,
    )
    errors = resp.get("Errors", [])
    if errors:
        logger.error(f"Partition SerDe patch errors: {errors}")
        raise RuntimeError(f"Failed to patch {len(errors)} partition(s).")

    logger.info(
        f"Patched {len(entries)} partition(s) SerDe to OpenCSVSerde: {db_name}.{table_name}"
    )


def delete_glue_table(db_name: str, table_name: str) -> None:
    """
    Drop the existing Glue table so the crawler recreates it cleanly
    with corrected column alignment after classifier fix.
    """
    try:
        glue.delete_table(DatabaseName=db_name, Name=table_name)
        logger.info(f"Deleted Glue table: {db_name}.{table_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityNotFoundException":
            logger.info(f"Table {table_name} does not exist — nothing to delete.")
        else:
            raise


def run_crawler(crawler_name: str) -> None:
    """Start the crawler. Handles case where it is already running."""
    try:
        glue.start_crawler(Name=crawler_name)
        logger.info(f"Crawler started: {crawler_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "CrawlerRunningException":
            logger.info("Crawler is already running — will wait for it to finish.")
        else:
            raise


def wait_for_crawler(crawler_name: str) -> None:
    """
    Poll the crawler status every POLL_INTERVAL seconds until it finishes.
    Raises on FAILED status.
    """
    logger.info(f"Waiting for crawler to finish (polling every {POLL_INTERVAL}s)...")
    while True:
        response = glue.get_crawler(Name=crawler_name)
        state = response["Crawler"]["State"]
        last_crawl = response["Crawler"].get("LastCrawl", {})
        status = last_crawl.get("Status", "—")

        logger.info(f"  Crawler state: {state}  |  Last crawl status: {status}")

        if state == "READY":
            if status == "FAILED":
                error_msg = last_crawl.get("ErrorMessage", "unknown error")
                raise RuntimeError(f"Crawler failed: {error_msg}")
            logger.info("Crawler finished successfully.")
            break

        time.sleep(POLL_INTERVAL)


def verify_tables(db_name: str) -> None:
    """Confirm that all expected tables were created in the Glue catalog."""
    logger.info(f"Verifying tables in database: {db_name}")
    response = glue.get_tables(DatabaseName=db_name)
    found = {t["Name"] for t in response["TableList"]}

    all_ok = True
    for table in EXPECTED_TABLES:
        if table in found:
            logger.info(f"  [OK] {table}")
        else:
            logger.warning(f"  [MISSING] {table}")
            all_ok = False

    if all_ok:
        logger.info("All expected tables confirmed in Glue Data Catalog.")
    else:
        logger.warning(
            "Some tables are missing. Check that cms_downloader.py ran "
            "successfully and that all three year folders exist in S3."
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not GLUE_ROLE_ARN:
        raise EnvironmentError(
            "GLUE_ROLE_ARN is not set. "
            "Add it to your .env file before running this script."
        )

    logger.info("Starting Glue catalog setup")
    logger.info(f"  Database:    {GLUE_DATABASE}")
    logger.info(f"  Crawler:     {CRAWLER_NAME}")
    logger.info(f"  Classifier:  {CLASSIFIER_NAME}")
    logger.info(f"  S3 target:   {S3_TARGET_PATH}")
    logger.info(f"  Role ARN:    {GLUE_ROLE_ARN}")

    create_glue_database(GLUE_DATABASE)
    create_csv_classifier(CLASSIFIER_NAME)
    create_crawler(CRAWLER_NAME, GLUE_DATABASE, S3_TARGET_PATH, GLUE_ROLE_ARN)
    delete_glue_table(GLUE_DATABASE, "cms_open_payments")
    run_crawler(CRAWLER_NAME)
    wait_for_crawler(CRAWLER_NAME)
    patch_table_serde(GLUE_DATABASE, "cms_open_payments")
    verify_tables(GLUE_DATABASE)

    logger.info("Glue setup complete. Tables are ready for Athena queries.")


if __name__ == "__main__":
    main()
