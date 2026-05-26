import os
import json
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from shared.dynamodb_client import get_table

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json",
}

# Status ordering for timeline sorting
STATUS_ORDER = {
    "STARTED": 0,
    "VALIDATED": 1,
    "TRANSFORMED": 2,
    "ENRICHED": 3,
    "LOADED": 4,
    "FAILED": 5,
}


def handler(event, context):
    print(f"Status API received: {event.get('httpMethod')} {event.get('path')}")

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    path_params = event.get("pathParameters") or {}
    job_id = path_params.get("jobId")

    try:
        if job_id:
            return _get_job(job_id)
        else:
            query_params = event.get("queryStringParameters") or {}
            return _list_jobs(query_params)
    except Exception as e:
        print(f"Error handling request: {e}")
        return _response(500, {"status": "error", "message": "Internal server error"})


def _get_job(job_id):
    table = get_table(DYNAMODB_TABLE)
    response = table.query(
        KeyConditionExpression=Key("jobId").eq(job_id)
    )
    items = response.get("Items", [])

    if not items:
        return _response(404, {"status": "error", "message": f"Job '{job_id}' not found"})

    items.sort(key=lambda x: STATUS_ORDER.get(x.get("status", ""), 99))

    latest = items[-1]
    current_status = latest.get("status")
    is_terminal = current_status in ("LOADED", "FAILED")

    started_at = items[0].get("startedAt") or items[0].get("updatedAt")
    finished_at = latest.get("updatedAt") if is_terminal else None

    return _response(200, {
        "status": "ok",
        "data": {
            "jobId": job_id,
            "currentStatus": current_status,
            "sourceKey": _find_field(items, "sourceKey"),
            "outputKey": _find_field(items, "outputKey"),
            "recordCount": _find_field(items, "recordCount"),
            "errorMessage": _find_field(items, "errorMessage"),
            "startedAt": started_at,
            "finishedAt": finished_at,
            "timeline": [
                {
                    "status": item.get("status"),
                    "updatedAt": item.get("updatedAt"),
                    "recordCount": item.get("recordCount"),
                }
                for item in items
            ],
        },
    })


def _list_jobs(query_params):
    limit = min(int(query_params.get("limit", 20)), 100)
    status_filter = query_params.get("status")

    table = get_table(DYNAMODB_TABLE)

    if status_filter:
        from boto3.dynamodb.conditions import Attr
        response = table.scan(
            FilterExpression=Attr("status").eq(status_filter),
            Limit=limit * 5,
        )
    else:
        response = table.scan(Limit=limit * 5)

    items = response.get("Items", [])

    # Deduplicate: keep only the latest-status item per jobId
    by_job = {}
    for item in items:
        jid = item.get("jobId")
        existing = by_job.get(jid)
        if not existing or STATUS_ORDER.get(item["status"], 99) > STATUS_ORDER.get(existing["status"], 99):
            by_job[jid] = item

    jobs = sorted(
        by_job.values(),
        key=lambda x: x.get("updatedAt", ""),
        reverse=True,
    )[:limit]

    return _response(200, {
        "status": "ok",
        "data": {
            "jobs": [
                {
                    "jobId": j.get("jobId"),
                    "currentStatus": j.get("status"),
                    "sourceKey": j.get("sourceKey"),
                    "recordCount": j.get("recordCount"),
                    "updatedAt": j.get("updatedAt"),
                }
                for j in jobs
            ],
            "count": len(jobs),
        },
    })


def _find_field(items, field):
    """Return the first non-None value for a field across all status items."""
    for item in reversed(items):
        val = item.get(field)
        if val is not None:
            return val
    return None


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
