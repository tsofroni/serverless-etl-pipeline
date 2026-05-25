import boto3
from datetime import datetime, timezone

_dynamodb_resource = None


def _get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def get_table(table_name):
    return _get_dynamodb().Table(table_name)


def write_job(table_name, job_item):
    """Write a complete job item (used for initial STARTED entry)."""
    table = get_table(table_name)
    table.put_item(Item=job_item)


def update_job_status(table_name, job_id, status, extra_fields=None):
    """Create a new item for each status transition (PK=jobId, SK=status)."""
    table = get_table(table_name)
    item = {
        "jobId": job_id,
        "status": status,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    if extra_fields:
        item.update(extra_fields)
    table.put_item(Item=item)
