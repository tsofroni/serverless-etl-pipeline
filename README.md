# Serverless ETL Pipeline

An event-driven, serverless ETL (Extract, Transform, Load) pipeline built on AWS. Files dropped into an S3 bucket are automatically picked up, validated, transformed, enriched, and written to an output bucket — with full job tracking in DynamoDB and orchestration via Step Functions.

Built as a portfolio project demonstrating AWS serverless architecture patterns for the AWS Community Builder programme.

---

## Architecture

```
S3 Drop Zone → Lambda (Trigger) → Step Functions Execution
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                     ▼
              Lambda (Validate) → Lambda (Transform) → Lambda (Enrich)
                                                              │
                                                              ▼
                                                    Lambda (Load) → S3 Output
                                                         │
                                                    DynamoDB (Job Metadata)

                              (on failure in any step)
                                         │
                                         ▼
                                Lambda (Error Handler) → DynamoDB (FAILED)
```

For a detailed visual diagram, open [architecture.drawio](./architecture.drawio) in [draw.io](https://app.diagrams.net/) or the VS Code Draw.io extension.

---

## AWS Services

| Service | Purpose |
|---------|---------|
| **S3** | Drop-zone bucket for input files; output bucket for final results; temp prefix for in-flight data |
| **Lambda** | Six Python 3.12 functions: trigger, validate, transform, enrich, load, error-handler |
| **Step Functions** | Express Workflow orchestrates the processing pipeline with built-in error routing |
| **DynamoDB** | `etl_jobs` table tracks job metadata and status transitions |
| **CloudWatch Logs** | Automatic log groups for each Lambda function (`/aws/lambda/etl-*`) |

---

## Supported Input Formats

| Format | Notes |
|--------|-------|
| `.csv` | Must include a header row; max 50 MB |
| `.json` | Must be a JSON array of objects; max 50 MB |

---

## Repository Structure

```
serverless-etl-pipeline/
├── lambdas/
│   ├── trigger/handler.py          # S3 event → start Step Functions
│   ├── validate/handler.py         # Size, encoding, format checks
│   ├── transform/handler.py        # Normalize, cast, deduplicate
│   ├── enrich/handler.py           # Add metadata fields
│   ├── load/handler.py             # Write to output S3, cleanup temp
│   ├── error-handler/handler.py    # Mark job FAILED in DynamoDB
│   └── shared/                     # Utilities bundled into each Lambda ZIP
│       ├── constants.py
│       ├── dynamodb_client.py
│       ├── s3_client.py
│       └── response_helper.py
├── step-functions/
│   └── pipeline_definition.json    # Amazon States Language definition
├── docs/
│   ├── setup-guide.md              # Step-by-step AWS Console setup
│   ├── dynamodb-schema.md          # Table schema with example items
│   └── step-functions-definition.md
├── tests/
│   └── sample-data/                # Valid and invalid test files
├── architecture.drawio             # Visual architecture diagram
└── ARCHITECTURE.md                 # Architecture decisions and data flow
```

---

## Local Development

There is no local runtime for this pipeline (it is tightly coupled to AWS services). Recommended approach for unit testing:

```bash
pip install pytest boto3 moto
pytest tests/
```

Use [moto](https://github.com/getmoto/moto) to mock S3 and DynamoDB calls in unit tests.

---

## Deployment

All infrastructure is provisioned manually via the AWS Console. Follow the step-by-step guide in [docs/setup-guide.md](./docs/setup-guide.md).

**Quick checklist:**
1. Create S3 buckets (drop-zone + output)
2. Create DynamoDB table `etl_jobs`
3. Create IAM roles for Lambda and Step Functions
4. Deploy the six Lambda functions (Python 3.12)
5. Create the Step Functions Express Workflow
6. Wire the S3 Event Notification to the trigger Lambda

---

## Testing the Pipeline

Upload the sample files from `tests/sample-data/` to your S3 drop-zone bucket and observe the results. See [tests/sample-data/README.md](./tests/sample-data/README.md) for expected outcomes per file.
