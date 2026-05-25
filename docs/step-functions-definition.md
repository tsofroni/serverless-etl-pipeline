# Step Functions Workflow Definition

## Overview

The ETL pipeline uses an **Express Workflow** in Amazon States Language (ASL). Express Workflows are optimised for high-throughput, short-duration executions (under 5 minutes) and are significantly cheaper than Standard Workflows for file-processing workloads.

The full definition is in [`step-functions/pipeline_definition.json`](../step-functions/pipeline_definition.json).

---

## Workflow Design

```
ValidateFile → TransformData → EnrichData → LoadOutput → PipelineSuccess
     ↓               ↓              ↓             ↓
  ErrorHandler ←─────────────────────────────────┘
     ↓
PipelineFailed
```

Each processing Task state:
- Invokes its Lambda synchronously (`.sync:2` resource pattern not required — Lambda Task uses the default synchronous invocation)
- Uses `Catch: [{ ErrorEquals: ["States.ALL"] }]` to route any exception to the ErrorHandler
- Passes the full state input event to the Lambda and merges the Lambda response back

---

## States

### 1. ValidateFile (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-validate` |
| On success | → TransformData |
| On failure | → ErrorHandler |
| ResultPath | `$.taskResult` |

**Purpose**: Validates the uploaded file — checks size limit, encoding, and structural validity (CSV header presence / JSON array format). Writes the `recordCount` back into the state as `$.taskResult.data.recordCount`.

**Input schema:**
```json
{
  "jobId": "20240115T143022#uuid",
  "sourceBucket": "my-etl-drop-zone",
  "sourceKey": "transactions.csv",
  "inputFormat": "csv"
}
```

**Output schema (merged into state):**
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "inputFormat": "...",
  "taskResult": {
    "status": "ok",
    "data": { "recordCount": 42 }
  }
}
```

---

### 2. TransformData (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-transform` |
| On success | → EnrichData |
| On failure | → ErrorHandler |

**Purpose**: Loads the raw file, normalises column names, casts types, removes empty strings (→ null), coerces date strings to ISO 8601, and deduplicates records. Writes the transformed data to `processed/{jobId}/transformed.json`.

**Input schema (constructed via Parameters):**
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "inputFormat": "...",
  "recordCount": 42
}
```

**Output schema (added to state):**
```json
{
  "transformedKey": "processed/20240115T143022#uuid/transformed.json",
  "recordCount": 40
}
```
Note: `recordCount` may be lower after deduplication.

---

### 3. EnrichData (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-enrich` |
| On success | → LoadOutput |
| On failure | → ErrorHandler |

**Purpose**: Adds enrichment fields to each record (`processed_at`, `job_id`, `record_index`) and wraps the output in a metadata envelope. Writes to `processed/{jobId}/enriched.json`.

**Input schema:**
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "inputFormat": "...",
  "recordCount": 40,
  "transformedKey": "processed/.../transformed.json"
}
```

**Output schema (added to state):**
```json
{
  "enrichedKey": "processed/20240115T143022#uuid/enriched.json"
}
```

---

### 4. LoadOutput (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-load` |
| On success | → PipelineSuccess |
| On failure | → ErrorHandler |

**Purpose**: Reads the enriched file, writes the final output to `output/{jobId}/result.json` in the output bucket, deletes temporary files from `processed/{jobId}/`, and marks the job as `LOADED` in DynamoDB.

**Input schema:**
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "enrichedKey": "processed/.../enriched.json",
  "recordCount": 40
}
```

**Output schema:**
```json
{
  "status": "ok",
  "data": {
    "jobId": "...",
    "outputBucket": "my-etl-output",
    "outputKey": "output/.../result.json",
    "recordCount": 40
  }
}
```

---

### 5. PipelineSuccess (Succeed)

Terminal state indicating the pipeline completed without errors.

---

### 6. ErrorHandler (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-error-handler` |
| Next | → PipelineFailed |

**Purpose**: Called from the `Catch` block of any Task state. Receives the error details from Step Functions, writes the `FAILED` status to DynamoDB with the error message, and logs the full error to CloudWatch.

**Input schema (injected by Step Functions Catch):**
```json
{
  "jobId": "...",
  "error": {
    "Error": "ValueError",
    "Cause": "{\"errorMessage\": \"File size 67.2 MB exceeds the limit of 50 MB\"}"
  }
}
```

The Parameters block in the ASL extracts `$.error.Error` and `$.error.Cause` before passing to the Lambda.

---

### 7. PipelineFailed (Fail)

Terminal state indicating the pipeline failed. The `Error` and `Cause` fields are visible in the Step Functions execution history (CloudWatch Logs for Express Workflows).

---

## Catch / Retry Logic

**Current configuration**: Each Task state has a single `Catch` block that catches `States.ALL` and routes to the `ErrorHandler`. There are no `Retry` blocks — the pipeline fails fast on any error.

**Recommended additions for production:**

```json
"Retry": [
  {
    "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"],
    "IntervalSeconds": 2,
    "MaxAttempts": 3,
    "BackoffRate": 2
  }
]
```

This handles transient Lambda invocation failures (cold start timeouts, throttling) without modifying the application code.
