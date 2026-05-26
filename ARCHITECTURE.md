# Architecture

Detailed technical design of the Serverless ETL Pipeline — component responsibilities, data flow, key design decisions, and cost model.

---

## Overview

The pipeline follows a **fan-in, linear execution** pattern: one file arrives, one Step Functions execution is started, and the file travels through a fixed sequence of Lambda functions before being written to the output bucket. There are no parallel branches, no fan-out, and no human approval gates — the design prioritises simplicity, observability, and cost efficiency.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Ingestion                                                          │
│  S3 Drop Zone ──(ObjectCreated)──► Lambda: etl-trigger             │
└──────────────────────────────────────┬──────────────────────────────┘
                                       │ start_execution()
┌──────────────────────────────────────▼──────────────────────────────┐
│  Orchestration                                                      │
│  Step Functions Express Workflow                                     │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Validate │→ │ Transform │→ │  Enrich  │→ │   Load   │→ Succeed  │
│  └────┬─────┘  └─────┬─────┘  └────┬─────┘  └────┬─────┘          │
│       └──────────────┴──────────────┴──────────────┘                │
│                              │ (any failure)                        │
│                     ┌────────▼────────┐                             │
│                     │  Error Handler  │→ Fail                       │
│                     └─────────────────┘                             │
└─────────────────────────────────────────────────────────────────────┘
            │                                       │
            │ (on FAILED execution)                 │ (all executions)
┌───────────▼───────────────────┐    ┌──────────────▼──────────────────┐
│  Alerting                     │    │  Storage                        │
│  EventBridge Rule             │    │  S3 Output Bucket               │
│         ↓                     │    │  output/{jobId}/result.json     │
│  Lambda: etl-notify           │    │                                 │
│         ↓                     │    │  DynamoDB (etl_jobs)            │
│  SNS → Email                  │    │  PK: jobId | SK: status         │
└───────────────────────────────┘    │                                 │
                                     │  CloudWatch Logs + Dashboard    │
┌──────────────────────────────┐     │  /aws/lambda/* | /aws/states/* │
│  Status API                  │     └─────────────────────────────────┘
│  API Gateway REST            │
│  GET /jobs                   │
│  GET /jobs/{jobId}           │
│         ↓                    │
│  Lambda: etl-status          │
│         ↓                    │
│  DynamoDB Query              │
└──────────────────────────────┘
```

---

## Components

### Trigger Lambda (`etl-trigger`)

**Responsibility**: Route — not validate content.

Receives the S3 `ObjectCreated` event, extracts the bucket name and object key (URL-decoding it with `unquote_plus`), validates the file extension, generates a unique `jobId`, writes the initial `STARTED` entry to DynamoDB, and starts the Step Functions execution. If the file extension is unsupported, it raises an exception immediately — no DynamoDB entry, no execution.

**Key implementation detail**: The `jobId` format is `{YYYYMMDDTHHMMSS}#{uuid4}`. This makes IDs chronologically sortable without a GSI, and it makes DynamoDB execution names safe for Step Functions (which requires alphanumeric + hyphens — the `#` is replaced with `-` in the execution name).

### Validate Lambda (`etl-validate`)

**Responsibility**: Content integrity — is this file safe to process?

Checks:
1. File size (max 50 MB via `head_object` ContentLength)
2. Encoding (tries UTF-8, UTF-8-BOM, Latin-1 in order)
3. For CSV: header row presence, at least one data row
4. For JSON: valid JSON, top-level array, non-empty

Raises an exception on any failure — Step Functions catches it and routes to the error handler. Returns `{**event, "recordCount": N}`.

### Transform Lambda (`etl-transform`)

**Responsibility**: Structural normalisation — make the data consistent.

Transformations applied to every record:
- **Column names**: `strip().lower().replace(" ", "_")` — e.g. `"Transaction ID"` → `"transaction_id"`
- **Empty strings**: Converted to `None` / JSON `null`
- **Date strings**: If a string value parses via `datetime.fromisoformat()`, it is re-serialised in ISO 8601 — coerces ambiguous formats to a canonical representation
- **Numeric strings**: Try `int()` first, then `float()`. Leaves genuine strings untouched.
- **Deduplication**: Records are fingerprinted as a `frozenset` of `(key, str(value))` pairs. Exact duplicates are removed; near-duplicates (differing only in ID fields) are kept.

Writes transformed data to `processed/{jobId}/transformed.json` in the drop-zone bucket (same bucket — simpler IAM, same data locality). Returns `{**event, "transformedKey": "..."}`.

### Enrich Lambda (`etl-enrich`)

**Responsibility**: Metadata enrichment — make the output self-describing.

Adds three fields to every record:
- `processed_at`: ISO 8601 timestamp of when enrichment ran
- `job_id`: Ties each record back to its pipeline execution
- `record_index`: 0-based position in the dataset (stable ordering reference)

Wraps the output in an envelope:
```json
{
  "records": [...enriched records...],
  "metadata": {
    "total_records": 42,
    "processed_at": "2024-01-15T14:30:28.312Z",
    "job_id": "20240115T143022#..."
  }
}
```

This envelope design means the output file is self-contained — a consumer reading `result.json` can determine provenance without querying DynamoDB.

### Load Lambda (`etl-load`)

**Responsibility**: Output persistence and cleanup.

Reads the enriched file, writes the final output to `output/{jobId}/result.json` in the output bucket, then deletes both temporary files (`transformed.json` and `enriched.json`) from the drop-zone bucket using the S3 `list_objects` + `delete_object` pattern. Updates DynamoDB to `LOADED`. If the delete fails, the pipeline does not fail — the output is already written and the job is marked complete. Temp file cleanup is best-effort.

### Error Handler Lambda (`etl-error-handler`)

**Responsibility**: Centralised failure recording.

Receives the Step Functions error event `{Error, Cause}` (injected by the Catch block), parses the `Cause` JSON string to extract the human-readable `errorMessage`, writes a `FAILED` item to DynamoDB, and logs the full error details to CloudWatch. This means every failure is visible in two places: DynamoDB (for quick status lookup) and CloudWatch (for full stack trace).

### Notify Lambda (`etl-notify`)

**Responsibility**: Human-readable failure alerting.

Triggered by an EventBridge rule whenever a Step Functions execution transitions to `FAILED`. The Lambda extracts the `jobId` from the execution input (stored in `detail.input`), queries DynamoDB for the `FAILED` status item to retrieve the `errorMessage` written by the error-handler Lambda, then constructs a plain-text email body including file name, error, duration, and troubleshooting links, and publishes it to the SNS topic.

Using a Lambda as an intermediary (rather than routing EventBridge directly to SNS) gives full control over the email content — particularly the ability to include the DynamoDB error message, which is not available in the raw EventBridge event payload.

### Status Lambda (`etl-status`)

**Responsibility**: Read-only job history API.

Invoked by API Gateway with Lambda proxy integration. Supports two operations:

- `GET /jobs/{jobId}` — queries DynamoDB with `KeyConditionExpression: jobId = :id`, sorts items by status order, and returns the full timeline plus summary fields (`currentStatus`, `sourceKey`, `outputKey`, `recordCount`, `errorMessage`, timing)
- `GET /jobs` — DynamoDB Scan with optional `status` filter, deduplicates results to one item per job (keeping the highest-status item), returns sorted by `updatedAt` descending

The `_json_default` function handles `Decimal` types returned by DynamoDB, converting them to `int` or `float` before JSON serialisation — the same problem solved by `_DecimalEncoder` in the finance tracker project.

### Shared Utilities (`lambdas/shared/`)

Bundled into every Lambda ZIP (no Lambda Layers). Four modules:

| Module | Provides |
|--------|----------|
| `constants.py` | `INPUT_FORMATS`, `SUPPORTED_ENCODINGS`, `JOB_STATUSES`, `MAX_FILE_SIZE_MB` |
| `dynamodb_client.py` | Lazy singleton boto3 resource; `write_job()`, `update_job_status()` |
| `s3_client.py` | Lazy singleton boto3 client; `read_object()`, `write_object()`, `get_file_size_mb()`, `list_objects()`, `delete_object()` |
| `response_helper.py` | `success(data)`, `error(message, details)` — plain dicts, no HTTP headers |

---

## Data Flow — State by State

```
Step Functions State          State Contents After Step
─────────────────────         ──────────────────────────
(initial input from trigger)  { jobId, sourceBucket, sourceKey, inputFormat }
↓ ValidateFile                { jobId, sourceBucket, sourceKey, inputFormat, recordCount }
↓ TransformData               { ..., recordCount (updated), transformedKey }
↓ EnrichData                  { ..., enrichedKey }
↓ LoadOutput                  { ..., loadResult: { status, data: { jobId, outputBucket, outputKey, recordCount } } }
↓ PipelineSuccess             (terminal)
```

Each Task state uses `ResultPath: "$"`, which replaces the entire state with the Lambda's return value. Since every Lambda returns `{**event, ...new_fields}`, fields accumulate across states without needing `Parameters` blocks or explicit field forwarding.

---

## DynamoDB Design

**Table**: `etl_jobs` | PK: `jobId` (String) | SK: `status` (String)

Using `status` as the sort key means each status transition creates a **new DynamoDB item**. A single job at `LOADED` state has five items in the table:

```
STARTED      → { jobId, status, sourceKey, inputFormat, startedAt }
VALIDATED    → { jobId, status, sourceKey, recordCount, updatedAt }
TRANSFORMED  → { jobId, status, sourceKey, recordCount, updatedAt }
ENRICHED     → { jobId, status, recordCount, updatedAt }
LOADED       → { jobId, status, sourceKey, outputKey, recordCount, updatedAt }
```

This is an **event-sourcing pattern**. You can query `KeyConditionExpression: jobId = :id` to get the full processing timeline, or `Key: {jobId, status: "LOADED"}` to check if a specific job completed. Timing between steps can be derived from the `updatedAt` timestamps.

The trade-off: `update_item` cannot change the sort key, so every status update is a `put_item` call that creates a new item rather than modifying an existing one.

See [docs/dynamodb-schema.md](./docs/dynamodb-schema.md) for the full attribute reference and example items.

---

## Step Functions Design

**Express Workflow** was chosen for three reasons:
1. **Cost**: ~25× cheaper than Standard for sub-minute executions (see cost model below)
2. **Throughput**: Express supports up to 100,000 executions/second; Standard is limited to 2,000/second
3. **Duration**: This pipeline completes in under 30 seconds — well within the 5-minute Express limit

Each Task state uses `ResultPath: "$"` and catches `States.ALL`. Error routing is centralised — adding a new stage requires only inserting a new Task state with the same Catch block pattern.

The `Catch` block writes the error to `$.error`, which makes `$.error.Error` and `$.error.Cause` available to the ErrorHandler state via its `Parameters` block.

See [docs/step-functions-definition.md](./docs/step-functions-definition.md) for per-state input/output schemas.

---

## S3 Prefix Strategy

| Prefix | Bucket | Lifecycle |
|--------|--------|-----------|
| `{filename}` | drop-zone | Input files — user-managed |
| `processed/{jobId}/transformed.json` | drop-zone | Temporary — deleted by Load Lambda |
| `processed/{jobId}/enriched.json` | drop-zone | Temporary — deleted by Load Lambda |
| `output/{jobId}/result.json` | output | Permanent output |

Using the same bucket for input and temporary files simplifies IAM (one bucket policy, one role permission) and keeps data locality. A separate output bucket cleanly separates user-facing results from processing artefacts.

---

## Security Model

- All config via Lambda environment variables (never hardcoded)
- Two IAM roles with least-privilege: `etl-lambda-execution-role` and `etl-stepfunctions-execution-role`
- Both S3 buckets have all public access blocked
- No external API calls — all traffic stays within AWS
- No VPC required (no database endpoints, no private network dependencies)

See [SECURITY.md](./SECURITY.md) for full IAM policy JSON and production readiness checklist.

---

## Cost Model

| Service | Unit price | Usage at 100 files/day | Monthly estimate |
|---------|-----------|------------------------|-----------------|
| Lambda | $0.0000166667/GB-s | 600 inv × 30 s × 256 MB | ~$0.08 |
| Step Functions Express | $0.00001/execution + $0.00001/GB-s | 100 exec × 30 s | ~$0.02 |
| DynamoDB On-Demand | $1.25/million writes | ~600 writes/day | ~$0.02 |
| S3 Standard | $0.023/GB + $0.005/1k PUT | ~3 GB + 300 PUT/day | ~$0.12 |
| CloudWatch Logs | $0.50/GB ingested | ~500 MB/month | ~$0.25 |
| **Total** | | | **~$0.49/month** |

At 1,000 files/day the cost scales roughly linearly to **~$4.90/month**. There are no fixed monthly costs beyond CloudWatch Logs minimum retention.

If using **Standard Workflow** instead of Express, the Step Functions cost alone would be ~$1.50/month at 100 files/day (6 state transitions × $0.025/1,000) — a 75× increase for the orchestration layer alone.
