# Monitoring Guide

This guide covers the three observability features of the ETL pipeline: the **CloudWatch Dashboard**, **SNS failure notifications**, and the **Job Status API**. It also explains where to look when something goes wrong.

---

## 1. CloudWatch Dashboard

The dashboard provides a real-time operational view of the pipeline across all metrics.

### Setup

1. Go to **CloudWatch → Dashboards → Create dashboard**
2. **Dashboard name**: `etl-pipeline`
3. When prompted to add a widget, click **Cancel** — you will import the JSON instead
4. In the dashboard, click **Actions → View/edit source**
5. Paste the contents of [`cloudwatch-dashboard.json`](../cloudwatch-dashboard.json)
6. Replace all occurrences of `REGION` with your AWS region (e.g. `eu-west-1`)
7. Replace all occurrences of `ACCOUNT_ID` with your 12-digit AWS account ID
8. Click **Update**

### What the dashboard shows

| Widget | Metrics | Purpose |
|--------|---------|---------|
| Step Functions Executions | Started / Succeeded / Failed (Sum) | Pipeline throughput and failure rate at a glance |
| Step Functions Duration | P50 / P95 execution time (ms) | Detect slowdowns — P95 spike often means a large file |
| Lambda Invocations | All 8 functions (Sum) | Confirm each stage is firing; detect missing invocations |
| Lambda Errors | All 6 processing functions (Sum) | Immediately highlights which stage is failing |
| Lambda Duration P95 | 5 processing functions | Identify the slowest stage in the pipeline |
| Lambda Throttles | All 6 processing functions | Indicates concurrency limit hit — rare at low volume |
| DynamoDB Requests | PutItem / Query / GetItem on etl_jobs | Confirms status writes are reaching the table |
| API Gateway | Count / 4XX / 5XX on etl-status-api | Status API health and error rate |

### Useful CloudWatch Logs Insights queries

**Find all FAILED jobs in the last 24 hours:**
```
fields @timestamp, @message
| filter @logStream like /etl-error-handler/
| filter @message like /DynamoDB updated/
| sort @timestamp desc
| limit 50
```

**Find the slowest transform executions:**
```
fields @timestamp, @duration, @message
| filter @logStream like /etl-transform/
| filter @message like /Transformation complete/
| sort @duration desc
| limit 20
```

**Count jobs by status today:**
```
fields @timestamp, @message
| filter @message like /DynamoDB updated/
| parse @message "status=*" as status
| stats count(*) by status
```

---

## 2. SNS Failure Notifications

When a Step Functions execution fails, an EventBridge rule fires automatically, invokes the `etl-notify` Lambda, which fetches the error message from DynamoDB and sends a formatted email via SNS.

### Setup

#### Step 1 — Create the SNS topic

1. Go to **SNS → Topics → Create topic**
2. **Type**: Standard
3. **Name**: `etl-pipeline-alerts`
4. Click **Create topic**
5. Copy the **Topic ARN**

#### Step 2 — Subscribe your email

1. On the topic page → **Subscriptions → Create subscription**
2. **Protocol**: Email
3. **Endpoint**: your email address
4. Click **Create subscription**
5. Open the confirmation email from AWS and click **Confirm subscription**

#### Step 3 — Deploy the notify Lambda

Package and deploy `lambdas/notify/` following the same ZIP pattern as the other functions (see [setup-guide.md](./setup-guide.md#step-4--deploy-lambda-functions)).

**Function name**: `etl-notify`

**Environment variables:**

| Variable | Value |
|----------|-------|
| `SNS_TOPIC_ARN` | ARN from Step 1 |
| `DYNAMODB_TABLE` | `etl_jobs` |

**IAM permissions** — add to `etl-lambda-execution-role`:

```json
{
  "Sid": "SNSPublish",
  "Effect": "Allow",
  "Action": "sns:Publish",
  "Resource": "arn:aws:sns:REGION:ACCOUNT_ID:etl-pipeline-alerts"
}
```

#### Step 4 — Create the EventBridge rule

1. Go to **EventBridge → Rules → Create rule**
2. **Name**: `etl-pipeline-on-failure`
3. **Event bus**: default
4. **Rule type**: Rule with an event pattern
5. **Event source**: AWS events
6. **Event pattern** (paste this JSON):

```json
{
  "source": ["aws.states"],
  "detail-type": ["Step Functions Execution Status Change"],
  "detail": {
    "stateMachineArn": ["arn:aws:states:REGION:ACCOUNT_ID:stateMachine:etl-pipeline"],
    "status": ["FAILED"]
  }
}
```

7. Click **Next**
8. **Target**: AWS service → Lambda function → `etl-notify`
9. Click **Next → Next → Create rule**

### Example notification email

```
Subject: ETL Pipeline FAILED — malformed_no_header.csv

ETL Pipeline Execution Failed

Job ID:       20240115T150011#b2c3d4e5-...
File:         malformed_no_header.csv (CSV)
Started:      2024-01-15T15:00:11+00:00
Failed at:    2024-01-15T15:00:14+00:00
Duration:     3.5s

Error:
  ValueError: CSV file has no header row

Step Functions Execution:
  arn:aws:states:eu-west-1:...:execution:etl-pipeline:20240115T150011-...

---
To investigate:
  1. DynamoDB etl_jobs — query jobId = "20240115T150011#..."
  2. CloudWatch Logs — /aws/lambda/etl-*
  3. Step Functions console — search for execution above
```

### Testing the notification

Upload `tests/sample-data/malformed_no_header.csv` to the drop-zone bucket. Within ~30 seconds you should receive the notification email. Check **CloudWatch → `/aws/lambda/etl-notify`** if the email does not arrive.

---

## 3. Job Status API

The Status API allows any HTTP client (curl, browser, application) to query job state and history without needing AWS Console access.

### Setup

#### Step 1 — Deploy the status Lambda

Package and deploy `lambdas/status/` following the ZIP pattern.

**Function name**: `etl-status`

**Environment variables:**

| Variable | Value |
|----------|-------|
| `DYNAMODB_TABLE` | `etl_jobs` |

The function uses the existing `etl-lambda-execution-role` — no additional IAM permissions needed (DynamoDB Query and Scan are already granted).

#### Step 2 — Create the API Gateway REST API

1. Go to **API Gateway → Create API → REST API → Build**
2. **API name**: `etl-status-api`
3. **Endpoint type**: Regional
4. Click **Create API**

#### Step 3 — Create the resources and methods

**Resource `/jobs`:**

1. **Actions → Create Resource**
2. **Resource Name**: `jobs`, **Resource Path**: `jobs`
3. Enable **CORS** checkbox
4. Click **Create Resource**
5. **Actions → Create Method → GET**
6. Integration type: Lambda Function → `etl-status` → Use Lambda Proxy integration
7. Click **Save → OK** (grants API Gateway permission to invoke Lambda)

**Resource `/jobs/{jobId}`:**

1. Select `/jobs` → **Actions → Create Resource**
2. **Resource Name**: `{jobId}`, **Resource Path**: `{jobId}`
3. Enable **CORS** checkbox
4. Click **Create Resource**
5. **Actions → Create Method → GET**
6. Integration type: Lambda Function → `etl-status` → Lambda Proxy integration
7. Click **Save → OK**

#### Step 4 — Deploy the API

1. **Actions → Deploy API**
2. **Deployment stage**: New Stage → **Stage name**: `prod`
3. Click **Deploy**
4. Copy the **Invoke URL** — this is your API base URL

#### Step 5 — Test

```bash
# Replace with your actual Invoke URL and a real jobId
curl "https://abc123def.execute-api.eu-west-1.amazonaws.com/prod/jobs"
curl "https://abc123def.execute-api.eu-west-1.amazonaws.com/prod/jobs/20240115T143022%23a1b2c3d4-..."
```

See [docs/api-documentation.md](./api-documentation.md) for full request/response schemas.

---

## Where to Look When Something Goes Wrong

| Symptom | Where to look |
|---------|--------------|
| File uploaded, nothing happens | CloudWatch `/aws/lambda/etl-trigger` — check for extension rejection error |
| Step Functions execution not starting | Check etl-trigger Lambda logs; verify S3 event notification is configured |
| Execution fails at ValidateFile | CloudWatch `/aws/lambda/etl-validate` — encoding, size, or structure error |
| Execution fails at TransformData | CloudWatch `/aws/lambda/etl-transform` — check for unexpected data types |
| Output file missing after LOADED status | CloudWatch `/aws/lambda/etl-load` — check OUTPUT_BUCKET env var |
| No failure email received | EventBridge rule target; CloudWatch `/aws/lambda/etl-notify`; SNS subscription confirmed? |
| Status API returns 500 | CloudWatch `/aws/lambda/etl-status` — DYNAMODB_TABLE env var set? |
| Status API returns 404 for a valid jobId | The jobId contains `#` — URL-encode it as `%23` |
| DynamoDB shows STARTED but no VALIDATED | Step Functions execution may not have started — check STATE_MACHINE_ARN env var on trigger |
