# API Documentation

The Job Status API exposes two endpoints for querying ETL job state and history. It is backed by the `etl-status` Lambda and served through API Gateway (REST API).

**Base URL**: `https://{api-id}.execute-api.{region}.amazonaws.com/prod`

---

## Endpoints

### GET /jobs/{jobId}

Returns the full processing history for a single job — one entry per status milestone.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `jobId` | String | Yes | Job identifier in the format `{YYYYMMDDTHHMMSS}#{uuid4}` |

#### Example Request

```
GET /jobs/20240115T143022%23a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Note: `#` must be URL-encoded as `%23` in the path.

#### Example Response — LOADED (success)

```json
{
  "status": "ok",
  "data": {
    "jobId": "20240115T143022#a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "currentStatus": "LOADED",
    "sourceKey": "valid_transactions.csv",
    "outputKey": "output/20240115T143022#a1b2c3d4-.../result.json",
    "recordCount": 10,
    "errorMessage": null,
    "startedAt": "2024-01-15T14:30:22.451Z",
    "finishedAt": "2024-01-15T14:30:30.045Z",
    "timeline": [
      { "status": "STARTED",     "updatedAt": "2024-01-15T14:30:22.451Z", "recordCount": null },
      { "status": "VALIDATED",   "updatedAt": "2024-01-15T14:30:24.103Z", "recordCount": 12 },
      { "status": "TRANSFORMED", "updatedAt": "2024-01-15T14:30:26.887Z", "recordCount": 10 },
      { "status": "ENRICHED",    "updatedAt": "2024-01-15T14:30:28.312Z", "recordCount": 10 },
      { "status": "LOADED",      "updatedAt": "2024-01-15T14:30:30.045Z", "recordCount": 10 }
    ]
  }
}
```

#### Example Response — FAILED

```json
{
  "status": "ok",
  "data": {
    "jobId": "20240115T150011#b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "currentStatus": "FAILED",
    "sourceKey": "malformed_no_header.csv",
    "outputKey": null,
    "recordCount": null,
    "errorMessage": "ValueError: CSV file has no header row",
    "startedAt": "2024-01-15T15:00:11.200Z",
    "finishedAt": "2024-01-15T15:00:14.772Z",
    "timeline": [
      { "status": "STARTED", "updatedAt": "2024-01-15T15:00:11.200Z", "recordCount": null },
      { "status": "FAILED",  "updatedAt": "2024-01-15T15:00:14.772Z", "recordCount": null }
    ]
  }
}
```

#### Error Responses

| Status code | Condition |
|-------------|-----------|
| `200` | Job found — returns full history |
| `404` | No DynamoDB items found for this `jobId` |
| `500` | Internal Lambda error — check CloudWatch Logs |

---

### GET /jobs

Returns a paginated list of recent jobs, deduplicated to show only the latest status per job.

#### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | Integer | `20` | Number of jobs to return (max 100) |
| `status` | String | — | Filter by status: `STARTED`, `VALIDATED`, `TRANSFORMED`, `ENRICHED`, `LOADED`, `FAILED` |

#### Example Requests

```
GET /jobs
GET /jobs?limit=10
GET /jobs?status=FAILED
GET /jobs?status=LOADED&limit=50
```

#### Example Response

```json
{
  "status": "ok",
  "data": {
    "jobs": [
      {
        "jobId": "20240115T143022#a1b2c3d4-...",
        "currentStatus": "LOADED",
        "sourceKey": "valid_transactions.csv",
        "recordCount": 10,
        "updatedAt": "2024-01-15T14:30:30.045Z"
      },
      {
        "jobId": "20240115T150011#b2c3d4e5-...",
        "currentStatus": "FAILED",
        "sourceKey": "malformed_no_header.csv",
        "recordCount": null,
        "updatedAt": "2024-01-15T15:00:14.772Z"
      }
    ],
    "count": 2
  }
}
```

> **Note**: `GET /jobs` uses a DynamoDB Scan internally. This is acceptable for low-volume portfolio use but should be replaced with a GSI on `status` or `updatedAt` for production workloads with large tables.

---

## CORS

All endpoints return the following CORS headers, allowing the API to be called from any browser origin:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type
Access-Control-Allow-Methods: GET, OPTIONS
```

---

## Error Format

All error responses use this shape:

```json
{
  "status": "error",
  "message": "Human-readable error description"
}
```

---

## Testing with curl

```bash
# Single job
curl "https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/prod/jobs/20240115T143022%23a1b2c3d4-..."

# List recent FAILED jobs
curl "https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/prod/jobs?status=FAILED"

# List last 5 jobs
curl "https://YOUR_API_ID.execute-api.YOUR_REGION.amazonaws.com/prod/jobs?limit=5"
```
