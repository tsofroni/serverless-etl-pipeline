import os
import json
import csv
import io

from shared.constants import MAX_FILE_SIZE_MB, JOB_STATUSES
from shared.dynamodb_client import update_job_status
from shared.s3_client import get_file_size_mb, read_object
from shared.response_helper import success

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]


def handler(event, context):
    print(f"Validate received event: {json.dumps(event)}")

    job_id = event["jobId"]
    source_bucket = event["sourceBucket"]
    source_key = event["sourceKey"]
    input_format = event["inputFormat"]

    print(f"Validating jobId={job_id}, file=s3://{source_bucket}/{source_key}")

    size_mb = get_file_size_mb(source_bucket, source_key)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(
            f"File size {size_mb:.2f} MB exceeds the limit of {MAX_FILE_SIZE_MB} MB"
        )
    print(f"File size OK: {size_mb:.2f} MB")

    content = read_object(source_bucket, source_key)
    print("File decoded successfully")

    record_count = _validate_format(content, input_format)
    print(f"Validation passed: {record_count} records found")

    update_job_status(
        DYNAMODB_TABLE,
        job_id,
        JOB_STATUSES.VALIDATED,
        extra_fields={"recordCount": record_count, "sourceKey": source_key},
    )
    print(f"DynamoDB updated: jobId={job_id}, status=VALIDATED")

    return {
        **event,
        "recordCount": record_count,
    }


def _validate_format(content, input_format):
    if input_format == "csv":
        return _validate_csv(content)
    elif input_format == "json":
        return _validate_json(content)
    else:
        raise ValueError(f"Unsupported input format: {input_format}")


def _validate_csv(content):
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("CSV file has no header row")
    rows = list(reader)
    if len(rows) == 0:
        raise ValueError("CSV file has a header but no data rows")
    return len(rows)


def _validate_json(content):
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"File is not valid JSON: {e}")
    if not isinstance(data, list):
        raise ValueError("JSON file must contain an array of records at the top level")
    if len(data) == 0:
        raise ValueError("JSON array is empty")
    return len(data)
