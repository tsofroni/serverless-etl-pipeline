import os
import json

from shared.constants import JOB_STATUSES
from shared.dynamodb_client import update_job_status
from shared.response_helper import error

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]


def handler(event, context):
    print(f"ErrorHandler received event: {json.dumps(event)}")

    job_id = event.get("jobId", "UNKNOWN")
    sf_error = event.get("Error", "UnknownError")
    sf_cause_raw = event.get("Cause", "{}")

    try:
        cause_obj = json.loads(sf_cause_raw)
        error_message = cause_obj.get("errorMessage", sf_cause_raw)
    except (json.JSONDecodeError, TypeError):
        error_message = sf_cause_raw

    print(f"Pipeline failed: jobId={job_id}, error={sf_error}, message={error_message}")

    if job_id != "UNKNOWN":
        update_job_status(
            DYNAMODB_TABLE,
            job_id,
            JOB_STATUSES.FAILED,
            extra_fields={"errorMessage": f"{sf_error}: {error_message}"},
        )
        print(f"DynamoDB updated: jobId={job_id}, status=FAILED")
    else:
        print("WARNING: jobId not found in error event — DynamoDB not updated")

    summary = {
        "jobId": job_id,
        "failedWith": sf_error,
        "errorMessage": error_message,
    }
    print(f"Error summary: {json.dumps(summary)}")

    return error(f"Pipeline failed: {sf_error}", details=summary)
