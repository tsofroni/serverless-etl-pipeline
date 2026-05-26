# Serverless ETL Pipeline

![AWS](https://img.shields.io/badge/AWS-Serverless-orange?logo=amazon-aws)
![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Step Functions](https://img.shields.io/badge/Step%20Functions-Express%20Workflow-pink)
![Status](https://img.shields.io/badge/Status-Live-brightgreen)

An event-driven, serverless ETL (Extract, Transform, Load) pipeline built entirely on AWS. Drop a CSV or JSON file into S3 and the pipeline automatically validates, transforms, enriches, and writes the result to an output bucket — with full job tracking in DynamoDB and visual orchestration via Step Functions.

Built as a portfolio project for the AWS Community Builder programme, demonstrating real-world serverless architecture patterns without any infrastructure-as-code: every resource is provisioned and observable through the AWS Console.

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
                                         │
                                   EventBridge Rule
                                         │
                                Lambda (Notify) → SNS → Email

API Gateway → Lambda (Status) → DynamoDB (read)

CloudWatch Dashboard (Lambda + Step Functions + DynamoDB + API Gateway metrics)
```

For the full visual diagram, open [architecture.drawio](./architecture.drawio) in [draw.io](https://app.diagrams.net/) or the VS Code Draw.io extension.

---

## AWS Services

| Service | Role in the pipeline |
|---------|---------------------|
| **S3** | Drop-zone bucket receives input files; output bucket stores final results; `processed/` prefix holds temporary intermediate files |
| **Lambda** | Eight Python 3.12 functions — six pipeline stages + notify + status API |
| **Step Functions** | Express Workflow orchestrates the processing sequence with built-in error routing |
| **DynamoDB** | `etl_jobs` table stores one item per job-status milestone (event-sourcing pattern) |
| **EventBridge** | Rule fires on Step Functions `FAILED` executions — triggers the notify Lambda |
| **SNS** | `etl-pipeline-alerts` topic delivers failure emails to subscribed addresses |
| **API Gateway** | REST API exposes `GET /jobs` and `GET /jobs/{jobId}` for job status queries |
| **CloudWatch** | Dashboard with 8 metric widgets; log groups for every Lambda and Step Functions |

---

## Why Serverless?

| Traditional approach | Serverless approach |
|---------------------|---------------------|
| Always-on EC2 instance | Pay only per invocation |
| Manual scaling | Scales automatically with S3 events |
| OS patching, instance management | Zero infrastructure maintenance |
| Fixed monthly cost | Near-zero cost at low volume |

At 100 files/day (avg 1 MB each, 30-second processing time), the estimated monthly cost is **under $1** — dominated by DynamoDB On-Demand reads.

---

## Supported Input Formats

| Format | Requirements |
|--------|-------------|
| `.csv` | Must include a header row; max 50 MB; UTF-8, UTF-8-BOM, or Latin-1 encoding |
| `.json` | Must be a JSON array of objects at the top level; max 50 MB |

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| `status` as DynamoDB sort key | Creates one item per status transition — enables a full audit trail queryable by `jobId` |
| Step Functions Express Workflow | ~25× cheaper than Standard for sub-minute file-processing executions |
| `ResultPath: "$"` on all Task states | Lambda returns `{**event, ...new_fields}` — state accumulates cleanly without `Parameters` blocks |
| `shared/` bundled in each ZIP | No Lambda Layers needed — self-contained, simple manual deployment |
| No Lambda VPC | No external dependencies — VPC adds cold start latency without security benefit here |
| EventBridge → Lambda → SNS (not direct) | Lambda fetches the error message from DynamoDB before notifying, giving richer email content |
| `GET /jobs` uses DynamoDB Scan | No GSI needed for portfolio volume; acceptable trade-off for simplicity |

---

## Repository Structure

```
serverless-etl-pipeline/
├── lambdas/
│   ├── trigger/handler.py          # S3 event → validates format → starts Step Functions
│   ├── validate/handler.py         # File size, encoding, CSV header / JSON array checks
│   ├── transform/handler.py        # Normalise columns, cast types, deduplicate
│   ├── enrich/handler.py           # Add processed_at, job_id, record_index, metadata block
│   ├── load/handler.py             # Write to output S3, delete temp files
│   ├── error-handler/handler.py    # Update DynamoDB FAILED, log error details
│   ├── notify/handler.py           # EventBridge → SNS failure email
│   ├── status/handler.py           # API Gateway → DynamoDB job status query
│   └── shared/                     # Bundled into every Lambda ZIP
│       ├── constants.py
│       ├── dynamodb_client.py
│       ├── s3_client.py
│       └── response_helper.py
├── step-functions/
│   └── pipeline_definition.json    # Amazon States Language definition
├── docs/
│   ├── setup-guide.md              # Step-by-step AWS Console provisioning
│   ├── dynamodb-schema.md          # Table schema with example items
│   ├── step-functions-definition.md # Workflow states, input/output schemas
│   ├── cicd-guide.md               # GitHub Actions vs AWS-native CI/CD
│   ├── monitoring-guide.md         # CloudWatch dashboard, SNS alerts, Status API setup
│   └── api-documentation.md        # Job Status API endpoints and response schemas
├── tests/
│   └── sample-data/                # Valid and invalid test files with expected outcomes
├── cloudwatch-dashboard.json       # Import-ready CloudWatch dashboard definition
├── buildspec.yml                   # CodeBuild spec for AWS-native CI/CD
├── architecture.drawio             # Visual architecture diagram (draw.io)
├── ARCHITECTURE.md                 # Architecture decisions and data flow
├── CONTRIBUTING.md                 # Development workflow and extension guide
├── LESSONS_LEARNED.md              # Real bugs encountered and what they taught
├── SECURITY.md                     # IAM design and security model
└── README.md
```

---

## Local Development

There is no local runtime for this pipeline (it depends on S3, Step Functions, and DynamoDB). The recommended approach is unit testing with [moto](https://github.com/getmoto/moto):

```bash
python -m venv venv
source venv/bin/activate      # Linux / macOS
venv\Scripts\activate         # Windows
pip install boto3 moto pytest
pytest tests/ -v
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development workflow, ZIP packaging scripts, and instructions for adding new pipeline stages.

---

## Deployment

All infrastructure is provisioned manually via the AWS Console. Follow the step-by-step guide in [docs/setup-guide.md](./docs/setup-guide.md).

**Quick checklist:**
1. Create S3 buckets (drop-zone + output)
2. Create DynamoDB table `etl_jobs` (PK: `jobId`, SK: `status`)
3. Create IAM roles for Lambda and Step Functions
4. Deploy the six Lambda functions (Python 3.12)
5. Create the Step Functions Express Workflow
6. Wire the S3 Event Notification to the trigger Lambda

---

## Monitoring and Observability

Three layers of observability are built into the pipeline:

| Layer | Tool | What it shows |
|-------|------|--------------|
| **Metrics** | CloudWatch Dashboard | Lambda invocations, errors, duration P95; Step Functions success/failure rate; API Gateway request count |
| **Alerts** | EventBridge → Lambda → SNS | Email notification on every pipeline failure with job ID, file name, error message, and duration |
| **API** | API Gateway → Lambda | `GET /jobs/{jobId}` returns full status timeline; `GET /jobs` lists recent jobs |

See [docs/monitoring-guide.md](./docs/monitoring-guide.md) for setup instructions and troubleshooting guide.

---

## CI/CD

This project uses **AWS CodePipeline + CodeBuild** for continuous deployment — keeping all credentials and build infrastructure inside AWS. See [docs/cicd-guide.md](./docs/cicd-guide.md) for a full comparison with GitHub Actions and step-by-step setup instructions.

---

## Testing the Pipeline

Upload sample files from `tests/sample-data/` to your drop-zone bucket. See [tests/sample-data/README.md](./tests/sample-data/README.md) for expected outcomes per file.

| File | Expected result |
|------|----------------|
| `valid_transactions.csv` | Pipeline completes → LOADED in DynamoDB, `result.json` in output bucket |
| `valid_transactions.json` | Same via JSON path |
| `invalid_format.txt` | Trigger rejects — no DynamoDB entry, no execution |
| `malformed_no_header.csv` | Validate fails → FAILED in DynamoDB |
| `malformed_invalid_json.json` | Validate fails → FAILED in DynamoDB |

---

## Documentation Index

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Technical rationale, data flow, cost model |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | Development setup, branching conventions, extension guide |
| [LESSONS_LEARNED.md](./LESSONS_LEARNED.md) | Real bugs and what they taught about AWS |
| [SECURITY.md](./SECURITY.md) | IAM policies, data flow security, production readiness |
| [docs/setup-guide.md](./docs/setup-guide.md) | Step-by-step AWS Console provisioning |
| [docs/dynamodb-schema.md](./docs/dynamodb-schema.md) | Table schema with example items |
| [docs/step-functions-definition.md](./docs/step-functions-definition.md) | Workflow states, catch/retry, input/output schemas |
| [docs/cicd-guide.md](./docs/cicd-guide.md) | CI/CD options: GitHub Actions vs CodePipeline |
| [docs/monitoring-guide.md](./docs/monitoring-guide.md) | CloudWatch dashboard, SNS alerts, Status API setup |
| [docs/api-documentation.md](./docs/api-documentation.md) | Job Status API endpoints and response schemas |

---

## Cost Estimate

Estimated monthly cost at 100 files/day (1 MB avg, 30 s processing time):

| Service | Usage | Estimated cost |
|---------|-------|---------------|
| Lambda | 800 invocations/day × avg 20 s × 256 MB | ~$0.12 |
| Step Functions | 100 Express executions/day | ~$0.02 |
| DynamoDB | On-demand, ~600 writes + ~200 reads/day | ~$0.06 |
| S3 | 3 GB storage + 300 PUT + 300 GET/day | ~$0.12 |
| CloudWatch Logs + Dashboard | ~500 MB logs/month + 1 dashboard | ~$0.28 |
| SNS | 100 emails/month (failure alerts) | ~$0.00 (free tier) |
| API Gateway | 1,000 requests/month | ~$0.00 (free tier) |
| **Total** | | **~$0.60/month** |

Costs scale linearly with file volume. The pipeline has no fixed-cost components.
