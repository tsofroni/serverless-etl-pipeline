import os
import json
from datetime import datetime, timezone

from shared.constants import JOB_STATUSES
from shared.dynamodb_client import update_job_status
from shared.s3_client import read_object, write_object
from shared.response_helper import success

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]


def handler(event, context):
    print(f"Enrich received event: {json.dumps(event)}")

    job_id = event["jobId"]
    source_bucket = event["sourceBucket"]
    transformed_key = event["transformedKey"]

    print(f"Enriching jobId={job_id}, source=s3://{source_bucket}/{transformed_key}")

    raw = read_object(source_bucket, transformed_key)
    records = json.loads(raw)
    print(f"Loaded {len(records)} records for enrichment")

    processed_at = datetime.now(timezone.utc).isoformat()
    enriched_records = []
    for index, record in enumerate(records):
        enriched_record = {
            **record,
            "processed_at": processed_at,
            "job_id": job_id,
            "record_index": index,
        }
        enriched_records.append(enriched_record)

    output = {
        "records": enriched_records,
        "metadata": {
            "total_records": len(enriched_records),
            "processed_at": processed_at,
            "job_id": job_id,
        },
    }

    enriched_key = f"processed/{job_id}/enriched.json"
    write_object(source_bucket, enriched_key, output)
    print(f"Wrote enriched data to s3://{source_bucket}/{enriched_key}")

    update_job_status(
        DYNAMODB_TABLE,
        job_id,
        JOB_STATUSES.ENRICHED,
        extra_fields={"recordCount": len(enriched_records)},
    )
    print(f"DynamoDB updated: jobId={job_id}, status=ENRICHED")

    return {
        **event,
        "enrichedKey": enriched_key,
    }
