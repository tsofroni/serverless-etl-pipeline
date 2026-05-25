import os
import json
import csv
import io
from datetime import datetime, timezone

from shared.constants import JOB_STATUSES
from shared.dynamodb_client import update_job_status
from shared.s3_client import read_object, write_object
from shared.response_helper import success

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]


def handler(event, context):
    print(f"Transform received event: {json.dumps(event)}")

    job_id = event["jobId"]
    source_bucket = event["sourceBucket"]
    source_key = event["sourceKey"]
    input_format = event["inputFormat"]
    record_count = event["recordCount"]

    print(f"Transforming jobId={job_id}, format={input_format}")

    content = read_object(source_bucket, source_key)
    records = _parse(content, input_format)
    print(f"Parsed {len(records)} records")

    transformed = _transform(records)
    print(f"Transformation complete: {len(transformed)} records after deduplication")

    transformed_key = f"processed/{job_id}/transformed.json"
    write_object(source_bucket, transformed_key, transformed)
    print(f"Wrote transformed data to s3://{source_bucket}/{transformed_key}")

    update_job_status(
        DYNAMODB_TABLE,
        job_id,
        JOB_STATUSES.TRANSFORMED,
        extra_fields={"sourceKey": source_key, "recordCount": len(transformed)},
    )
    print(f"DynamoDB updated: jobId={job_id}, status=TRANSFORMED")

    return {
        **event,
        "transformedKey": transformed_key,
        "recordCount": len(transformed),
    }


def _parse(content, input_format):
    if input_format == "csv":
        reader = csv.DictReader(io.StringIO(content))
        return [dict(row) for row in reader]
    else:
        return json.loads(content)


def _transform(records):
    transformed = []
    seen = set()

    for record in records:
        normalized = _normalize_record(record)
        fingerprint = frozenset(
            (k, str(v)) for k, v in normalized.items() if v is not None
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        transformed.append(normalized)

    return transformed


def _normalize_record(record):
    result = {}
    for raw_key, raw_value in record.items():
        key = raw_key.strip().lower().replace(" ", "_")

        if raw_value == "" or raw_value is None:
            result[key] = None
            continue

        value = _try_cast_numeric(raw_value)
        if value is None:
            value = _try_cast_date(raw_value)
        if value is None:
            value = raw_value

        result[key] = value

    return result


def _try_cast_numeric(value):
    if not isinstance(value, str):
        return None
    try:
        int_val = int(value)
        return int_val
    except ValueError:
        pass
    try:
        float_val = float(value)
        return float_val
    except ValueError:
        pass
    return None


def _try_cast_date(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.isoformat()
    except ValueError:
        return None
