# Step Functions Workflow Definition

## Overview

The ETL pipeline uses an **Express Workflow** in Amazon States Language (ASL). Express Workflows are optimised for high-throughput, short-duration executions (under 5 minutes) and are significantly cheaper than Standard Workflows for file-processing workloads.

The full definition is in [`step-functions/pipeline_definition.json`](../step-functions/pipeline_definition.json).

---

## Workflow Design

```
ValidateFile → TransformData → EnrichData → LoadOutput → PipelineSuccess
     │               │              │             │
     └───────────────┴──────────────┴─────────────┘
                             │ (any failure — Catch: States.ALL)
                        ErrorHandler
                             │
                        PipelineFailed
```

### Key design principle — `ResultPath: "$"`

Every processing Task state uses `ResultPath: "$"`. This replaces the entire state with the Lambda's return value. Since every Lambda returns `{**event, ...new_fields}`, fields accumulate across states without needing `Parameters` blocks:

```
Initial input:     { jobId, sourceBucket, sourceKey, inputFormat }
After Validate:    { jobId, sourceBucket, sourceKey, inputFormat, recordCount }
After Transform:   { ..., recordCount (deduplicated), transformedKey }
After Enrich:      { ..., enrichedKey }
After Load:        { ..., loadResult: { status: "ok", data: {...} } }
```

> **Why not `ResultPath: "$.someKey"` or `ResultPath: null`?**
> - `ResultPath: "$.someKey"` merges the result under a nested key — useful for side-effect tasks, but requires downstream states to know the nesting level
> - `ResultPath: null` discards the Lambda output entirely — only appropriate when the Lambda is a pure side effect with no output needed
>
> See [LESSONS_LEARNED.md](../LESSONS_LEARNED.md#1-step-functions-resultpath-and-the-vanishing-lambda-output) for the full story of the bug caused by the original `ResultPath: null` design.

---

## States

### 1. ValidateFile (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-validate` |
| ResultPath | `"$"` — replaces entire state with Lambda output |
| On success | → TransformData |
| On failure | Catch `States.ALL` → ResultPath `$.error` → ErrorHandler |

**Purpose**: Validates the uploaded file — checks size limit, encoding, and structural validity (CSV header presence / JSON array format). Updates DynamoDB to `VALIDATED`.

**Input schema:**
```json
{
  "jobId": "20240115T143022#a1b2c3d4-...",
  "sourceBucket": "my-etl-drop-zone",
  "sourceKey": "transactions.csv",
  "inputFormat": "csv"
}
```

**Output schema (becomes the new state):**
```json
{
  "jobId": "20240115T143022#a1b2c3d4-...",
  "sourceBucket": "my-etl-drop-zone",
  "sourceKey": "transactions.csv",
  "inputFormat": "csv",
  "recordCount": 12
}
```

---

### 2. TransformData (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-transform` |
| ResultPath | `"$"` |
| On success | → EnrichData |
| On failure | Catch `States.ALL` → ErrorHandler |

**Purpose**: Normalises column names, casts data types, removes empty strings, coerces date strings to ISO 8601, and deduplicates records. Writes `processed/{jobId}/transformed.json`. Updates DynamoDB to `TRANSFORMED`.

**Input schema** (full previous state):
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "inputFormat": "...",
  "recordCount": 12
}
```

**Output schema** (new state — recordCount may be lower after deduplication):
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "sourceKey": "...",
  "inputFormat": "...",
  "recordCount": 10,
  "transformedKey": "processed/20240115T143022#.../transformed.json"
}
```

---

### 3. EnrichData (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-enrich` |
| ResultPath | `"$"` |
| On success | → LoadOutput |
| On failure | Catch `States.ALL` → ErrorHandler |

**Purpose**: Adds `processed_at`, `job_id`, and `record_index` to each record. Wraps output in a metadata envelope. Writes `processed/{jobId}/enriched.json`. Updates DynamoDB to `ENRICHED`.

**Input schema** (full previous state):
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "recordCount": 10,
  "transformedKey": "processed/.../transformed.json",
  "..."
}
```

**Output schema:**
```json
{
  "jobId": "...",
  "sourceBucket": "...",
  "recordCount": 10,
  "transformedKey": "...",
  "enrichedKey": "processed/20240115T143022#.../enriched.json"
}
```

---

### 4. LoadOutput (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-load` |
| ResultPath | `"$.loadResult"` — merges result under `loadResult` key |
| On success | → PipelineSuccess |
| On failure | Catch `States.ALL` → ErrorHandler |

**Purpose**: Reads the enriched file, writes `output/{jobId}/result.json` to the output bucket, deletes temporary files, and updates DynamoDB to `LOADED`.

`ResultPath: "$.loadResult"` is used here instead of `"$"` because the Load Lambda returns a `success()` wrapper dict, not `{**event}`. Since LoadOutput is the last processing stage before PipelineSuccess (a Succeed state), the state contents after LoadOutput do not need to be structured for a downstream Lambda.

**Output written to `$.loadResult`:**
```json
{
  "status": "ok",
  "data": {
    "jobId": "...",
    "outputBucket": "my-etl-output",
    "outputKey": "output/20240115T143022#.../result.json",
    "recordCount": 10
  }
}
```

---

### 5. PipelineSuccess (Succeed)

Terminal state — execution marked as `SUCCEEDED` in Step Functions. No further actions.

---

### 6. ErrorHandler (Task)

| Property | Value |
|----------|-------|
| Type | Task |
| Lambda | `etl-error-handler` |
| Parameters | `jobId.$: "$.jobId"`, `Error.$: "$.error.Error"`, `Cause.$: "$.error.Cause"` |
| ResultPath | `"$.errorResult"` |
| Next | → PipelineFailed |

**Purpose**: Parses the Step Functions error event, writes a `FAILED` item to DynamoDB with the error message, and logs the full details to CloudWatch.

The `Parameters` block extracts the three fields the Lambda needs from the enriched state (which still contains `$.jobId` from the original input, plus `$.error.Error` and `$.error.Cause` injected by the Catch block).

**Input to Lambda (constructed by Parameters):**
```json
{
  "jobId": "20240115T143022#...",
  "Error": "ValueError",
  "Cause": "{\"errorMessage\": \"CSV file has no header row\", \"errorType\": \"ValueError\"}"
}
```

---

### 7. PipelineFailed (Fail)

Terminal state — execution marked as `FAILED` in Step Functions.

```json
{
  "Error": "ETLPipelineError",
  "Cause": "One or more ETL pipeline steps failed. Check DynamoDB etl_jobs and CloudWatch Logs for details."
}
```

---

## Catch / Retry Logic

**Current configuration**: Each Task state has one `Catch` block catching `States.ALL`, routing to ErrorHandler with `ResultPath: "$.error"`.

The `ResultPath: "$.error"` in the Catch block merges the error object into the current state as:
```json
{
  "error": {
    "Error": "ValueError",
    "Cause": "{...}"
  }
}
```
The rest of the state (including `jobId`) is preserved, so the ErrorHandler can read `$.jobId` alongside `$.error.Error` and `$.error.Cause`.

**Recommended Retry blocks for production** (add to each Task state):

```json
"Retry": [
  {
    "ErrorEquals": [
      "Lambda.ServiceException",
      "Lambda.AWSLambdaException",
      "Lambda.SdkClientException",
      "Lambda.TooManyRequestsException"
    ],
    "IntervalSeconds": 2,
    "MaxAttempts": 3,
    "BackoffRate": 2
  }
]
```

This handles transient Lambda invocation failures (cold starts, throttling, brief service errors) with exponential backoff, without routing to the error handler unnecessarily.

---

## How to Update the State Machine

1. Edit `step-functions/pipeline_definition.json` in this repository
2. Open **AWS Console → Step Functions → etl-pipeline → Edit**
3. Paste the updated JSON
4. Replace all occurrences of `REGION`, `ACCOUNT_ID`, and function name suffixes with your actual values
5. Click **Save**

The state machine update takes effect immediately for new executions. In-flight executions continue running against the old definition.
