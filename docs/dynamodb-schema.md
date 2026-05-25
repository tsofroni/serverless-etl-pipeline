# DynamoDB Schema

## Table: `etl_jobs`

### Key Schema

| Attribute | Type | Role | Description |
|-----------|------|------|-------------|
| `jobId` | String | Partition Key (PK) | Unique job identifier — format: `{YYYYMMDDTHHMMSS}#{uuid4}` |
| `status` | String | Sort Key (SK) | Pipeline status at this milestone — one of: `STARTED`, `VALIDATED`, `TRANSFORMED`, `ENRICHED`, `LOADED`, `FAILED` |

**Design note**: Using `status` as the sort key means each status transition creates a new item. Querying by `jobId` alone returns the full processing history of a job — an event-sourcing-style audit trail.

### All Attributes

| Attribute | Type | Present in statuses | Description |
|-----------|------|---------------------|-------------|
| `jobId` | String | All | Partition key — job identifier |
| `status` | String | All | Sort key — current status milestone |
| `startedAt` | String (ISO 8601) | STARTED only | Timestamp when the job was created |
| `updatedAt` | String (ISO 8601) | All | Timestamp when this item was written |
| `sourceKey` | String | STARTED, VALIDATED, LOADED | S3 key of the input file |
| `inputFormat` | String | STARTED | `csv` or `json` |
| `recordCount` | Number | VALIDATED, TRANSFORMED, ENRICHED, LOADED | Number of records at this stage |
| `outputKey` | String | LOADED | S3 key of the final output file |
| `errorMessage` | String | FAILED | Error description from the failed step |
| `executionArn` | String | (optional, if stored) | Step Functions execution ARN |

---

## Example Items

### Status: STARTED

```json
{
  "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "STARTED",
  "sourceKey": "transactions_jan2024.csv",
  "inputFormat": "csv",
  "startedAt": "2024-01-15T14:30:22.451Z",
  "updatedAt": "2024-01-15T14:30:22.451Z"
}
```

### Status: VALIDATED

```json
{
  "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "VALIDATED",
  "sourceKey": "transactions_jan2024.csv",
  "recordCount": 150,
  "updatedAt": "2024-01-15T14:30:24.103Z"
}
```

### Status: TRANSFORMED

```json
{
  "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "TRANSFORMED",
  "sourceKey": "transactions_jan2024.csv",
  "recordCount": 148,
  "updatedAt": "2024-01-15T14:30:26.887Z"
}
```

Note: `recordCount` may decrease after deduplication.

### Status: ENRICHED

```json
{
  "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "ENRICHED",
  "recordCount": 148,
  "updatedAt": "2024-01-15T14:30:28.312Z"
}
```

### Status: LOADED

```json
{
  "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "LOADED",
  "sourceKey": "transactions_jan2024.csv",
  "outputKey": "output/20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890/result.json",
  "recordCount": 148,
  "updatedAt": "2024-01-15T14:30:30.045Z"
}
```

### Status: FAILED

```json
{
  "jobId": "20240115T150011#b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "status": "FAILED",
  "errorMessage": "ValueError: File size 67.2 MB exceeds the limit of 50 MB",
  "updatedAt": "2024-01-15T15:00:14.772Z"
}
```

---

## Useful Queries

### Get all events for a job (full audit trail)

```python
import boto3
table = boto3.resource("dynamodb").Table("etl_jobs")

response = table.query(
    KeyConditionExpression="jobId = :id",
    ExpressionAttributeValues={":id": "20240115T143022#a1b2c3d4-..."}
)
items = response["Items"]
```

### Check if a job completed successfully

Query by `jobId` and look for an item where `status = "LOADED"`:

```python
response = table.get_item(
    Key={
        "jobId": "20240115T143022#a1b2c3d4-...",
        "status": "LOADED"
    }
)
item = response.get("Item")
completed = item is not None
```

### Scan for all failed jobs (use sparingly — Scan reads entire table)

```python
from boto3.dynamodb.conditions import Attr

response = table.scan(
    FilterExpression=Attr("status").eq("FAILED")
)
failed_jobs = response["Items"]
```

---

## AWS Console Setup

When creating the table in the AWS Console:
- **Table name**: `etl_jobs`
- **Partition key**: `jobId` (String)
- **Sort key**: `status` (String)
- **Table class**: DynamoDB Standard
- **Capacity mode**: On-Demand (recommended for variable workloads)
- **Encryption**: AWS owned key (default)

No GSI is required for the basic pipeline. Add a GSI on `status` if you need to query all jobs by status efficiently.
