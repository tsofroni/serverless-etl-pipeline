# Architecture

## Overview

This pipeline processes structured data files (CSV or JSON) through a sequence of validated transformation steps. The design prioritises simplicity, observability, and cost efficiency — all appropriate for a portfolio workload.

---

## Data Flow

```
1. User uploads file → s3://drop-zone-bucket/{filename}.csv (or .json)

2. S3 ObjectCreated event fires → invokes etl-trigger Lambda

3. etl-trigger:
   - Validates file extension
   - Generates jobId = {timestamp}#{uuid}
   - Writes STARTED record to DynamoDB etl_jobs
   - Starts Step Functions Express Workflow execution

4. Step Functions orchestrates:
   a. ValidateFile  → etl-validate Lambda
   b. TransformData → etl-transform Lambda
   c. EnrichData    → etl-enrich Lambda
   d. LoadOutput    → etl-load Lambda
   e. PipelineSuccess (Succeed state)

   On any failure:
   → ErrorHandler → etl-error-handler Lambda → PipelineFailed (Fail state)

5. Final output: s3://output-bucket/output/{jobId}/result.json
```

---

## State Machine Design

**Express Workflow** was chosen over Standard Workflow because:
- Processing completes in seconds to minutes (well within the 5-minute Express limit)
- Express Workflows are significantly cheaper for high-frequency, short-duration executions
- Execution history is available in CloudWatch Logs (not in the console like Standard)

Each Task state passes the full event object to the Lambda and merges the response back via `ResultPath`. This avoids re-fetching data already available in the state input.

---

## DynamoDB Design

**Table: `etl_jobs`**
- **PK**: `jobId` (String)
- **SK**: `status` (String)

Using `status` as the sort key creates one item per status transition — an event-sourcing-style history. Querying by `jobId` alone returns all status milestones for a job, making it easy to reconstruct the full processing timeline.

Example query: *"Show me all status events for job X"* → `KeyConditionExpression: jobId = :id`

---

## S3 Prefix Strategy

| Prefix | Bucket | Purpose |
|--------|--------|---------|
| `{filename}` | drop-zone | Input files uploaded by users |
| `processed/{jobId}/transformed.json` | drop-zone | Intermediate: post-transform |
| `processed/{jobId}/enriched.json` | drop-zone | Intermediate: post-enrich |
| `output/{jobId}/result.json` | output | Final enriched dataset |

Intermediate files in `processed/` are deleted by the Load Lambda after the final output is written. Using the same bucket for drop-zone and intermediate files keeps IAM permissions simple — the Lambda role only needs access to two buckets.

---

## Lambda Design Decisions

- **No Lambda Layers**: Each function bundles `shared/` in its own ZIP. Simpler to deploy and reason about; appropriate for a portfolio project where the shared code is small.
- **Singleton boto3 clients**: Global `_resource = None` guard reuses connections across warm invocations — standard AWS Lambda optimisation.
- **Step Functions error propagation**: Processing Lambdas raise exceptions on failure; Step Functions catches them and routes to the ErrorHandler. This keeps error handling logic out of the individual functions.
- **Trigger Lambda**: Returns a structured response dict rather than raising (S3 invocations are async — there is no caller to receive the exception in a meaningful way; the error will appear in CloudWatch).

---

## Observability

- Every Lambda logs `start`, `end`, and key intermediate values via `print()` → CloudWatch Logs
- DynamoDB records each status transition with a timestamp → job timeline queryable at any time
- Step Functions execution history available in CloudWatch (Express Workflow)
- Failed jobs have `errorMessage` in DynamoDB for quick diagnosis without opening CloudWatch

---

## Limitations and Future Improvements

| Limitation | Potential improvement |
|------------|----------------------|
| Max 50 MB file size | Stream processing with S3 Select or chunked reads |
| Single-region | Multi-region replication with S3 Cross-Region Replication |
| No authentication on drop-zone | S3 pre-signed URLs with expiry |
| Manual deployment | AWS SAM or CDK for IaC |
| No retry logic in Step Functions | Add `Retry` blocks with exponential backoff per Task state |
