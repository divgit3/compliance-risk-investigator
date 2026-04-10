"""
Task 4.17b — Compute true Transfer of Value from CMS Athena data.
Aggregates Nova Pharma + all-company spend per HCP per year.
Outputs: data/processed/hcp_tov_summary.parquet
"""
import boto3
import time
import pandas as pd
import os

REGION = "us-east-1"
DATABASE = "compliance_risk_raw"
OUTPUT_BUCKET = "s3://compliance-risk-investigator/athena-results/"
OUTPUT_FILE = "data/processed/hcp_tov_summary.parquet"

QUERY = """
SELECT
    CONCAT('HCP_', CAST(covered_recipient_profile_id AS VARCHAR)) AS hcp_id,
    CAST(partition_0 AS BIGINT) AS program_year,

    -- Nova Pharma ToV (target company)
    SUM(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
             LIKE '%takeda%'
             THEN total_amount_of_payment_usdollars ELSE 0 END) AS nova_tov,

    -- Nova Pharma by category
    SUM(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
             LIKE '%takeda%'
             AND LOWER(nature_of_payment_or_transfer_of_value) LIKE '%food%'
             THEN total_amount_of_payment_usdollars ELSE 0 END) AS nova_food_beverage,

    SUM(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
             LIKE '%takeda%'
             AND LOWER(nature_of_payment_or_transfer_of_value) LIKE '%speaking%'
             THEN total_amount_of_payment_usdollars ELSE 0 END) AS nova_speaking_fee,

    SUM(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
             LIKE '%takeda%'
             AND LOWER(nature_of_payment_or_transfer_of_value) LIKE '%consult%'
             THEN total_amount_of_payment_usdollars ELSE 0 END) AS nova_consulting,

    SUM(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
             LIKE '%takeda%'
             AND LOWER(nature_of_payment_or_transfer_of_value) LIKE '%travel%'
             THEN total_amount_of_payment_usdollars ELSE 0 END) AS nova_travel,

    -- All companies total ToV (for SOW)
    SUM(total_amount_of_payment_usdollars) AS total_tov_all_companies,

    -- Payment counts
    COUNT(CASE WHEN LOWER(applicable_manufacturer_or_applicable_gpo_making_payment_name)
               LIKE '%takeda%' THEN 1 END) AS nova_payment_count

FROM cms_open_payments
WHERE covered_recipient_type IN (
    'Covered Recipient Physician',
    'Covered Recipient Non-Physician Practitioner'
)
AND covered_recipient_profile_id IS NOT NULL
AND partition_0 IN ('2022', '2023', '2024')
GROUP BY
    covered_recipient_profile_id,
    partition_0
"""


def run_athena_query(query: str) -> str:
    client = boto3.client('athena', region_name=REGION)
    response = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={'Database': DATABASE},
        ResultConfiguration={'OutputLocation': OUTPUT_BUCKET},
    )
    execution_id = response['QueryExecutionId']
    print(f"Query started: {execution_id}")

    # Poll until complete
    while True:
        result = client.get_query_execution(QueryExecutionId=execution_id)
        state = result['QueryExecution']['Status']['State']
        print(f"  Status: {state}")
        if state in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            break
        time.sleep(5)

    if state != 'SUCCEEDED':
        reason = result['QueryExecution']['Status'].get(
            'StateChangeReason', 'Unknown')
        raise RuntimeError(f"Query {state}: {reason}")

    result_location = result['QueryExecution']['ResultConfiguration']['OutputLocation']
    print(f"Results at: {result_location}")
    return result_location


def download_results(s3_path: str) -> pd.DataFrame:
    s3 = boto3.client('s3', region_name=REGION)
    # Parse s3://bucket/key
    parts = s3_path.replace("s3://", "").split("/", 1)
    bucket, key = parts[0], parts[1]
    print(f"Downloading from s3://{bucket}/{key}")
    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_csv(obj['Body'])
    return df


def main():
    os.makedirs("data/processed", exist_ok=True)
    print("=" * 60)
    print("Computing true ToV from CMS Athena data")
    print("=" * 60)

    print("\nRunning Athena aggregation query...")
    print("(This may take 2-5 minutes for 43M rows)")
    result_location = run_athena_query(QUERY)

    print("\nDownloading results...")
    df = download_results(result_location)
    print(f"  Rows: {len(df):,}")
    print(f"  Columns: {list(df.columns)}")

    # Compute SOW
    df['nova_sow'] = df['nova_tov'] / df['total_tov_all_companies'].replace(0, float('nan'))

    # Pivot to wide format (one row per HCP)
    df_wide = df.pivot_table(
        index='hcp_id',
        columns='program_year',
        values=[
            'nova_tov', 'nova_food_beverage',
            'nova_speaking_fee', 'nova_consulting',
            'nova_travel', 'total_tov_all_companies',
            'nova_sow', 'nova_payment_count',
        ],
        aggfunc='sum',
    ).reset_index()

    # Flatten column names
    df_wide.columns = [
        f"{col[0]}_{col[1]}" if col[1] != '' else col[0]
        for col in df_wide.columns
    ]

    print(f"\nWide format: {df_wide.shape}")
    print("Sample columns:", list(df_wide.columns[:8]))

    df_wide.to_parquet(OUTPUT_FILE, index=False)
    print(f"\nSaved to {OUTPUT_FILE}")
    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
