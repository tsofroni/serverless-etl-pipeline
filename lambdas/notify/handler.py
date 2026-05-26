import os
import json
import boto3
from datetime import datetime, timezone, timedelta

from shared.dynamodb_client import get_table

SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]

_sns_client = None


def _get_sns():
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns")
    return _sns_client


def handler(event, context):
    print(f"Notify received EventBridge event: {json.dumps(event)}")

    detail = event.get("detail", {})
    status = detail.get("status")
    execution_arn = detail.get("executionArn", "")
    start_ms = detail.get("startDate", 0)
    stop_ms = detail.get("stopDate", 0)

    raw_input = detail.get("input", "{}")
    try:
        execution_input = json.loads(raw_input)
    except (json.JSONDecodeError, TypeError):
        execution_input = {}

    job_id = execution_input.get("jobId", "UNKNOWN")
    source_key = execution_input.get("sourceKey", "unknown")
    input_format = execution_input.get("inputFormat", "unknown")

    error_message = _fetch_error_message(job_id)

    duration_s = round((stop_ms - start_ms) / 1000, 1) if stop_ms and start_ms else None

    started_at = _ms_to_iso(start_ms)
    failed_at = _ms_to_iso(stop_ms)

    subject = f"ETL Pipeline FAILED — {source_key}"

    body = f"""ETL Pipeline Execution Failed

Job ID:       {job_id}
File:         {source_key} ({input_format.upper()})
Started:      {started_at}
Failed at:    {failed_at}
Duration:     {f"{duration_s}s" if duration_s is not None else "n/a"}

Error:
  {error_message or "See CloudWatch Logs for details"}

Step Functions Execution:
  {execution_arn}

---
To investigate:
  1. DynamoDB etl_jobs — query jobId = "{job_id}"
  2. CloudWatch Logs — /aws/lambda/etl-*
  3. Step Functions console — search for execution above
"""

    _get_sns().publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=body,
    )

    print(f"SNS notification sent for jobId={job_id}, error={error_message!r}")
    return {"status": "ok", "jobId": job_id, "notified": True}


def _fetch_error_message(job_id):
    if job_id == "UNKNOWN":
        return None
    try:
        table = get_table(DYNAMODB_TABLE)
        response = table.get_item(Key={"jobId": job_id, "status": "FAILED"})
        item = response.get("Item", {})
        return item.get("errorMessage")
    except Exception as e:
        print(f"Could not fetch error message from DynamoDB: {e}")
        return None


def _ms_to_iso(ms):
    if not ms:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
