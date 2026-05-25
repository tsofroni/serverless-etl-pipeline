import os
import json
import uuid
import boto3
from datetime import datetime, timezone
from urllib.parse import unquote_plus

from shared.constants import INPUT_FORMATS, JOB_STATUSES
from shared.dynamodb_client import write_job
from shared.response_helper import success, error

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

_sf_client = None


def _get_sf():
    global _sf_client
    if _sf_client is None:
        _sf_client = boto3.client("stepfunctions")
    return _sf_client


def handler(event, context):
    print(f"Trigger received event: {json.dumps(event)}")

    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])

    print(f"Processing file: s3://{bucket}/{key}")

    extension = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    if extension not in INPUT_FORMATS:
        raise ValueError(
            f"Unsupported file format '.{extension}'. Supported formats: {INPUT_FORMATS}"
        )

    input_format = extension
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    job_id = f"{timestamp}#{uuid.uuid4()}"
    started_at = datetime.now(timezone.utc).isoformat()

    job_item = {
        "jobId": job_id,
        "status": JOB_STATUSES.STARTED,
        "sourceKey": key,
        "inputFormat": input_format,
        "startedAt": started_at,
        "updatedAt": started_at,
    }
    write_job(DYNAMODB_TABLE, job_item)
    print(f"Job created in DynamoDB: jobId={job_id}, status=STARTED")

    execution_input = {
        "jobId": job_id,
        "sourceBucket": bucket,
        "sourceKey": key,
        "inputFormat": input_format,
    }

    sf_response = _get_sf().start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=job_id.replace("#", "-").replace(":", "-"),
        input=json.dumps(execution_input),
    )

    execution_arn = sf_response["executionArn"]
    print(f"Step Functions execution started: {execution_arn}")

    return success({"jobId": job_id, "executionArn": execution_arn})
