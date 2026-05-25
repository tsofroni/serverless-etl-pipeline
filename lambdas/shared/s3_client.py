import boto3
import json
from shared.constants import SUPPORTED_ENCODINGS

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def read_object(bucket, key):
    response = _get_s3().get_object(Bucket=bucket, Key=key)
    raw_bytes = response["Body"].read()
    for encoding in SUPPORTED_ENCODINGS:
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Unable to decode file with supported encodings: {SUPPORTED_ENCODINGS}")


def write_object(bucket, key, body):
    if not isinstance(body, (str, bytes)):
        body = json.dumps(body, ensure_ascii=False)
    if isinstance(body, str):
        body = body.encode("utf-8")
    _get_s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def get_file_size_mb(bucket, key):
    response = _get_s3().head_object(Bucket=bucket, Key=key)
    size_bytes = response["ContentLength"]
    return size_bytes / (1024 * 1024)


def delete_object(bucket, key):
    _get_s3().delete_object(Bucket=bucket, Key=key)


def list_objects(bucket, prefix):
    paginator = _get_s3().get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys
