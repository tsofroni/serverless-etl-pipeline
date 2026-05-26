# Setup Guide

Step-by-step instructions for provisioning all AWS resources via the AWS Console.

**Estimated time**: 45–60 minutes  
**AWS Region**: Choose one region and use it consistently throughout (e.g., `eu-west-1`)

---

## Prerequisites

- An AWS account with Administrator or PowerUser permissions
- The code from this repository cloned locally

---

## Step 1 — Create S3 Buckets

You need **two** S3 buckets:

### 1a. Drop Zone Bucket (input + temp)

1. Go to **S3 → Create bucket**
2. **Bucket name**: `your-name-etl-drop-zone` (must be globally unique)
3. **Region**: your chosen region
4. **Block all public access**: Enabled (default)
5. Click **Create bucket**

This bucket serves two purposes:
- Receives uploaded input files (root level)
- Stores intermediate files during processing under the `processed/` prefix (auto-created by the pipeline)

### 1b. Output Bucket

1. Go to **S3 → Create bucket**
2. **Bucket name**: `your-name-etl-output`
3. **Region**: same as above
4. **Block all public access**: Enabled (default)
5. Click **Create bucket**

Final pipeline results will be written to `output/{jobId}/result.json` in this bucket.

---

## Step 2 — Create the DynamoDB Table

1. Go to **DynamoDB → Tables → Create table**
2. **Table name**: `etl_jobs`
3. **Partition key**: `jobId` — Type: **String**
4. **Sort key**: `status` — Type: **String**
5. **Table settings**: Customize settings
6. **Table class**: DynamoDB Standard
7. **Read/write capacity**: **On-demand**
8. Click **Create table**

See [dynamodb-schema.md](./dynamodb-schema.md) for the full attribute reference and example items.

---

## Step 3 — Create IAM Roles

### 3a. Lambda Execution Role

1. Go to **IAM → Roles → Create role**
2. **Trusted entity**: AWS Service → **Lambda**
3. Click **Next**
4. Attach these managed policies:
   - `AWSLambdaBasicExecutionRole` (CloudWatch Logs)
5. Click **Next**, name the role: `etl-lambda-execution-role`
6. Click **Create role**

Now add an **inline policy** to grant access to S3, DynamoDB, and Step Functions:

1. Open the role → **Add permissions → Create inline policy**
2. Switch to the **JSON** editor and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3Access",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:HeadObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-name-etl-drop-zone",
        "arn:aws:s3:::your-name-etl-drop-zone/*",
        "arn:aws:s3:::your-name-etl-output",
        "arn:aws:s3:::your-name-etl-output/*"
      ]
    },
    {
      "Sid": "DynamoDBAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:REGION:ACCOUNT_ID:table/etl_jobs"
    },
    {
      "Sid": "StepFunctionsStart",
      "Effect": "Allow",
      "Action": "states:StartExecution",
      "Resource": "arn:aws:states:REGION:ACCOUNT_ID:stateMachine:etl-pipeline"
    }
  ]
}
```

Replace `REGION`, `ACCOUNT_ID`, and bucket names with your actual values.

3. Name the policy: `etl-lambda-inline-policy`
4. Click **Create policy**

### 3b. Step Functions Execution Role

1. Go to **IAM → Roles → Create role**
2. **Trusted entity**: AWS Service → **Step Functions**
3. Click **Next**
4. Click **Next** (no managed policy needed here)
5. Name the role: `etl-stepfunctions-execution-role`
6. Click **Create role**

Add an inline policy to allow Step Functions to invoke Lambda:

1. Open the role → **Add permissions → Create inline policy**
2. JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeLambda",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": [
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-validate",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-transform",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-enrich",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-load",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-error-handler"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogDelivery",
        "logs:GetLogDelivery",
        "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
```

3. Name: `etl-stepfunctions-inline-policy`
4. Click **Create policy**

---

## Step 4 — Deploy Lambda Functions

Repeat this process for each of the six Lambda functions. The function names and their source directories are:

| Function name | Source directory |
|---------------|-----------------|
| `etl-trigger` | `lambdas/trigger/` |
| `etl-validate` | `lambdas/validate/` |
| `etl-transform` | `lambdas/transform/` |
| `etl-enrich` | `lambdas/enrich/` |
| `etl-load` | `lambdas/load/` |
| `etl-error-handler` | `lambdas/error-handler/` |

### For each function:

**A. Create a deployment ZIP**

Create a ZIP containing the function's `handler.py` and the entire `lambdas/shared/` directory:

```
etl-trigger.zip
├── handler.py
└── shared/
    ├── constants.py
    ├── dynamodb_client.py
    ├── s3_client.py
    └── response_helper.py
```

On Windows (PowerShell):
```powershell
$fn = "trigger"   # change for each function
$tmpDir = "tmp-$fn"
New-Item -ItemType Directory -Force $tmpDir
Copy-Item "lambdas\$fn\handler.py" "$tmpDir\"
Copy-Item -Recurse "lambdas\shared" "$tmpDir\shared"
Compress-Archive -Path "$tmpDir\*" -DestinationPath "etl-$fn.zip" -Force
Remove-Item -Recurse -Force $tmpDir
```

**B. Create the Lambda function in the Console**

1. Go to **Lambda → Create function**
2. **Function name**: `etl-trigger` (or the appropriate name)
3. **Runtime**: Python 3.12
4. **Execution role**: Use an existing role → `etl-lambda-execution-role`
5. Click **Create function**

**C. Upload the ZIP**

1. In the function page → **Code** tab
2. Click **Upload from → .zip file**
3. Upload your ZIP file
4. **Handler**: `handler.handler`

**D. Set Environment Variables**

Go to **Configuration → Environment variables → Edit** and add:

| Function | Variable | Value |
|----------|----------|-------|
| `etl-trigger` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-trigger` | `STATE_MACHINE_ARN` | *(fill in after Step 5)* |
| `etl-validate` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-transform` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-enrich` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-load` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-load` | `OUTPUT_BUCKET` | `your-name-etl-output` |
| `etl-error-handler` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-notify` | `DYNAMODB_TABLE` | `etl_jobs` |
| `etl-notify` | `SNS_TOPIC_ARN` | *(fill in after Step 8)* |
| `etl-status` | `DYNAMODB_TABLE` | `etl_jobs` |

**E. Set Timeout**

Go to **Configuration → General configuration → Edit**:
- **Timeout**: 5 minutes (300 seconds) — safe upper bound for file processing

---

## Step 5 — Create the Step Functions State Machine

1. Go to **Step Functions → State machines → Create state machine**
2. **Template**: Blank
3. **Type**: Express
4. Switch to the **Code** editor (not the visual editor)
5. Paste the contents of [`step-functions/pipeline_definition.json`](../step-functions/pipeline_definition.json)
6. Replace all occurrences of `REGION` and `ACCOUNT_ID` with your actual values
7. Replace `FUNCTION_NAME` placeholders with the exact function names:
   - `etl-validate`, `etl-transform`, `etl-enrich`, `etl-load`, `etl-error-handler`
8. Click **Next**
9. **Name**: `etl-pipeline`
10. **Execution role**: `etl-stepfunctions-execution-role`
11. **Logging**: Enable logging → **CloudWatch log group**: create new → `/aws/states/etl-pipeline`
12. **Log level**: ERROR (or ALL for debugging)
13. Click **Create state machine**

After creation, copy the **State Machine ARN** and add it as the `STATE_MACHINE_ARN` environment variable on the `etl-trigger` Lambda (Step 4D).

---

## Step 6 — Configure S3 Event Notification

1. Go to **S3 → your-name-etl-drop-zone → Properties**
2. Scroll to **Event notifications → Create event notification**
3. **Event name**: `etl-trigger-on-upload`
4. **Prefix**: *(leave empty — trigger on any upload to root)*
5. **Suffix**: *(leave empty — trigger accepts any extension; Lambda validates format)*
6. **Event types**: Check **s3:ObjectCreated:Put** (and optionally **s3:ObjectCreated:CompleteMultipartUpload**)
7. **Destination**: Lambda function → `etl-trigger`
8. Click **Save changes**

> **Note**: When you select a Lambda function as the destination, AWS will automatically add the necessary resource-based policy to allow S3 to invoke the Lambda. You do not need to do this manually.

---

## Step 7 — Verify CloudWatch Log Groups

Lambda creates log groups automatically on first invocation. After running a test (see below), verify in **CloudWatch → Log groups** that these exist:

- `/aws/lambda/etl-trigger`
- `/aws/lambda/etl-validate`
- `/aws/lambda/etl-transform`
- `/aws/lambda/etl-enrich`
- `/aws/lambda/etl-load`
- `/aws/lambda/etl-error-handler`
- `/aws/lambda/etl-notify`
- `/aws/lambda/etl-status`
- `/aws/states/etl-pipeline`

---

## Step 8 — Set Up SNS Failure Notifications

### 8a. Create the SNS topic

1. Go to **SNS → Topics → Create topic**
2. **Type**: Standard
3. **Name**: `etl-pipeline-alerts`
4. Click **Create topic**
5. Copy the **Topic ARN** — add it as `SNS_TOPIC_ARN` on the `etl-notify` Lambda (Step 4D)

### 8b. Subscribe your email

1. On the topic page → **Subscriptions → Create subscription**
2. **Protocol**: Email
3. **Endpoint**: your email address
4. Click **Create subscription**
5. Confirm the subscription via the email from AWS

### 8c. Grant notify Lambda permission to publish

Add this statement to `etl-lambda-execution-role`'s inline policy:

```json
{
  "Sid": "SNSPublish",
  "Effect": "Allow",
  "Action": "sns:Publish",
  "Resource": "arn:aws:sns:REGION:ACCOUNT_ID:etl-pipeline-alerts"
}
```

### 8d. Create the EventBridge rule

1. Go to **EventBridge → Rules → Create rule**
2. **Name**: `etl-pipeline-on-failure`
3. **Event bus**: default
4. **Rule type**: Rule with an event pattern
5. **Event pattern** (paste this JSON, replacing REGION and ACCOUNT_ID):

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

6. **Target**: AWS service → Lambda function → `etl-notify`
7. Click **Create rule**

---

## Step 9 — Deploy the Job Status API

### 9a. Create the API Gateway REST API

1. Go to **API Gateway → Create API → REST API → Build**
2. **API name**: `etl-status-api`
3. **Endpoint type**: Regional
4. Click **Create API**

### 9b. Create resources and methods

**Create `/jobs` resource:**
1. **Actions → Create Resource** → Resource Name: `jobs`, Path: `jobs`, enable CORS
2. **Actions → Create Method → GET** → Lambda Proxy integration → `etl-status`

**Create `/jobs/{jobId}` resource:**
1. Select `/jobs` → **Actions → Create Resource** → Resource Name: `{jobId}`, enable CORS
2. **Actions → Create Method → GET** → Lambda Proxy integration → `etl-status`

### 9c. Deploy the API

1. **Actions → Deploy API → New Stage** → Stage name: `prod`
2. Copy the **Invoke URL** — this is your base URL for all API calls

Test it:
```bash
curl "https://YOUR_API_ID.execute-api.REGION.amazonaws.com/prod/jobs"
```

See [docs/api-documentation.md](./api-documentation.md) for full endpoint documentation.

---

## Step 10 — Import the CloudWatch Dashboard

1. Go to **CloudWatch → Dashboards → Create dashboard**
2. **Dashboard name**: `etl-pipeline`
3. When prompted to add a widget, click **Cancel**
4. Click **Actions → View/edit source**
5. Paste the contents of [`cloudwatch-dashboard.json`](../cloudwatch-dashboard.json)
6. Replace all `REGION` with your AWS region and all `ACCOUNT_ID` with your account ID
7. Click **Update**

The dashboard will show metrics for all Lambda functions, Step Functions, DynamoDB, and the API Gateway once executions have run.

---

## Testing the Pipeline

Upload the sample files from `tests/sample-data/` to test different scenarios. See [tests/sample-data/README.md](../tests/sample-data/README.md) for expected outcomes.

**Happy path test:**
1. Upload `tests/sample-data/valid_transactions.csv` to `your-name-etl-drop-zone`
2. **Step Functions → etl-pipeline → Executions** — execution appears and succeeds
3. **DynamoDB → etl_jobs** — query by `jobId`, see all 5 status milestones
4. **S3 → your-name-etl-output → output/** — `result.json` present
5. **Status API** — `GET /jobs/{jobId}` returns full timeline

**Failure path test:**
1. Upload `tests/sample-data/malformed_no_header.csv` to the drop-zone
2. **Step Functions** — execution appears and fails at ValidateFile
3. **DynamoDB** — two items: STARTED and FAILED (with error message)
4. **Email** — notification arrives within ~30 seconds
5. **Status API** — `GET /jobs/{jobId}` shows `currentStatus: "FAILED"` and `errorMessage`
