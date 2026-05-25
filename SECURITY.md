# Security

Security model for the ETL pipeline — IAM design, data handling, and what would need to change for a production workload.

---

## Credentials and Secrets

- **No credentials in code.** All sensitive values (bucket names, table names, ARNs) are injected via Lambda environment variables.
- **No hardcoded AWS account IDs or region names** in any Lambda handler. The only place they appear is in the Step Functions definition as placeholder strings (`REGION`, `ACCOUNT_ID`) that must be replaced before deployment.
- The `.gitignore` explicitly excludes `.env`, `.aws/`, and `builds/` to prevent accidental credential commits.

---

## IAM — Least Privilege

The pipeline uses two IAM roles:

### Lambda Execution Role (`etl-lambda-execution-role`)

Grants only the permissions each function actually needs. No wildcard resources except for CloudWatch Logs (which requires `*` by AWS policy design).

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
        "arn:aws:s3:::your-etl-drop-zone",
        "arn:aws:s3:::your-etl-drop-zone/*",
        "arn:aws:s3:::your-etl-output",
        "arn:aws:s3:::your-etl-output/*"
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
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

### Step Functions Execution Role (`etl-stepfunctions-execution-role`)

Grants only Lambda invocation rights for the five processing functions. No S3 or DynamoDB access — those belong to the Lambdas, not the orchestrator.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokePipelineLambdas",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": [
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-validate",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-transform",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-enrich",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-load",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-error-handler"
      ]
    }
  ]
}
```

### S3 Resource-Based Policy (automatically added by AWS)

When you configure the S3 event notification to invoke the trigger Lambda, AWS automatically adds a resource-based policy to the Lambda allowing `s3.amazonaws.com` to invoke it. No manual action required.

---

## S3 Bucket Configuration

Both buckets should be configured with:

| Setting | Value | Reason |
|---------|-------|--------|
| Block all public access | Enabled | No public read/write needed |
| Server-side encryption | SSE-S3 (AES-256) or SSE-KMS | Encrypt data at rest |
| Versioning | Optional | Not required for pipeline inputs; useful for output audit |
| Access logging | Optional | Enable for compliance/audit workloads |

**Bucket policy**: Neither bucket needs a bucket policy for this pipeline — access is controlled entirely via the Lambda execution role's IAM identity policy.

---

## Data Flow Security

```
User upload → S3 Drop Zone (encrypted at rest, private)
                    ↓ (S3 event, internal AWS)
             Lambda Trigger (no network egress)
                    ↓ (AWS internal API)
             Step Functions (encrypted state machine input)
                    ↓ (AWS internal invocation)
             Processing Lambdas (VPC not required — no external endpoints)
                    ↓
             S3 Output (encrypted at rest, private)
             DynamoDB (encrypted at rest by default)
```

All traffic stays within the AWS network. No external API calls, no public endpoints. Lambda functions do not require a VPC for this use case.

---

## What Would Need to Change for a Production Workload

| Concern | Current state | Production recommendation |
|---------|--------------|--------------------------|
| **Input validation** | File size + format only | Add virus scanning (e.g., ClamAV on Lambda) before processing |
| **Encryption at rest** | SSE-S3 default | Use SSE-KMS with a customer-managed key for audit log access control |
| **Encryption in transit** | HTTPS enforced by AWS SDKs | Add S3 bucket policy denying `aws:SecureTransport: false` |
| **Upload authentication** | Any principal with S3 write access | Restrict with S3 pre-signed URLs or an upload API with Cognito auth |
| **Secrets management** | Lambda env vars (plaintext) | Use AWS Secrets Manager or SSM Parameter Store for sensitive values |
| **Network isolation** | Lambda in default VPC-less mode | Place Lambdas in a private VPC subnet for defence-in-depth |
| **Logging** | CloudWatch Logs (application) | Enable AWS CloudTrail for API-level audit; enable S3 server access logging |
| **Data retention** | Unlimited | Add S3 Lifecycle rules to expire/archive old output files |
| **Alerting** | DynamoDB FAILED status (passive) | Add CloudWatch Alarm on Lambda error rate or a DynamoDB Streams processor |

---

## Dependency Security

This project has no third-party Python dependencies beyond `boto3` (provided by the Lambda runtime). There is no `requirements.txt` to audit.

If you add dependencies in future:
- Pin exact versions (`package==1.2.3`)
- Run `pip audit` regularly
- Consider AWS CodeGuru Reviewer or Dependabot for automated scanning
