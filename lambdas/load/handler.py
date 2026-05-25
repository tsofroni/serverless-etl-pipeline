import os
import json

from shared.constants import JOB_STATUSES
from shared.dynamodb_client import update_job_status
from shared.s3_client import read_object, write_object, list_objects, delete_object
from shared.response_helper import success

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]


def handler(event, context):
    print(f"Load received event: {json.dumps(event)}")

    job_id = event["jobId"]
    source_bucket = event["sourceBucket"]
    enriched_key = event["enrichedKey"]
    record_count = event["recordCount"]

    print(f"Loading jobId={job_id}, source=s3://{source_bucket}/{enriched_key}")

    raw = read_object(source_bucket, enriched_key)
    enriched_data = json.loads(raw)

    output_key = f"output/{job_id}/result.json"
    write_object(OUTPUT_BUCKET, output_key, enriched_data)
    print(f"Wrote final output to s3://{OUTPUT_BUCKET}/{output_key}")

    temp_prefix = f"processed/{job_id}/"
    temp_keys = list_objects(source_bucket, temp_prefix)
    for temp_key in temp_keys:
        delete_object(source_bucket, temp_key)
        print(f"Deleted temp file: s3://{source_bucket}/{temp_key}")
    print(f"Cleaned up {len(temp_keys)} temp file(s)")

    update_job_status(
        DYNAMODB_TABLE,
        job_id,
        JOB_STATUSES.LOADED,
        extra_fields={
            "outputKey": output_key,
            "recordCount": record_count,
            "sourceKey": event.get("sourceKey"),
        },
    )
    print(f"DynamoDB updated: jobId={job_id}, status=LOADED")

    return success(
        {
            "jobId": job_id,
            "outputBucket": OUTPUT_BUCKET,
            "outputKey": output_key,
            "recordCount": record_count,
        }
    )
