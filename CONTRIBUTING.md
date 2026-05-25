# Contributing

Guide for developing, testing, and extending the ETL pipeline locally.

---

## Prerequisites

- Python 3.12
- AWS CLI configured (`aws configure`)
- An AWS account with the infrastructure from [docs/setup-guide.md](./docs/setup-guide.md) already provisioned
- Git

Optional but recommended:
- [moto](https://github.com/getmoto/moto) for mocking AWS services in unit tests
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) for local Lambda invocation

---

## Local Development

There is no local server for this project — it is tightly coupled to AWS services. The recommended development loop is:

### 1. Set up a virtual environment

```bash
python -m venv venv
source venv/bin/activate      # Linux / macOS
venv\Scripts\activate         # Windows
pip install boto3 moto pytest
```

### 2. Run a Lambda function locally (SAM)

```bash
# Invoke the trigger Lambda with a mock S3 event
sam local invoke TriggerFunction --event tests/events/s3_put_event.json
```

Alternatively, invoke directly with a mock event dict in a test script — see the unit testing section below.

### 3. Unit testing with moto

```python
import json
import pytest
from moto import mock_s3, mock_dynamodb
import boto3
import os

os.environ["DYNAMODB_TABLE"] = "etl_jobs"
os.environ["STATE_MACHINE_ARN"] = "arn:aws:states:eu-west-1:123456789012:stateMachine:etl-pipeline"

@mock_dynamodb
@mock_s3
def test_trigger_valid_csv():
    # Create mock DynamoDB table
    ddb = boto3.resource("dynamodb", region_name="eu-west-1")
    ddb.create_table(
        TableName="etl_jobs",
        KeySchema=[
            {"AttributeName": "jobId", "KeyType": "HASH"},
            {"AttributeName": "status", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "jobId", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    from lambdas.trigger.handler import handler

    event = {
        "Records": [{
            "s3": {
                "bucket": {"name": "test-drop-zone"},
                "object": {"key": "transactions.csv"}
            }
        }]
    }

    # Note: Step Functions cannot be fully mocked with moto yet;
    # mock or patch the _get_sf().start_execution call separately.
    result = handler(event, None)
    assert result["status"] == "ok"
```

Run tests:

```bash
pytest tests/ -v
```

---

## Project Structure

```
serverless-etl-pipeline/
├── lambdas/
│   ├── shared/             # Bundled into every Lambda ZIP
│   │   ├── constants.py
│   │   ├── dynamodb_client.py
│   │   ├── s3_client.py
│   │   └── response_helper.py
│   ├── trigger/handler.py
│   ├── validate/handler.py
│   ├── transform/handler.py
│   ├── enrich/handler.py
│   ├── load/handler.py
│   └── error-handler/handler.py
├── step-functions/
│   └── pipeline_definition.json
├── docs/
│   ├── setup-guide.md
│   ├── dynamodb-schema.md
│   ├── step-functions-definition.md
│   └── cicd-guide.md
├── tests/
│   └── sample-data/
├── ARCHITECTURE.md
├── CONTRIBUTING.md
├── LESSONS_LEARNED.md
├── SECURITY.md
└── README.md
```

---

## Making Changes

### Modifying a Lambda function

1. Edit the handler file under `lambdas/{function-name}/handler.py`
2. If you changed `lambdas/shared/`, all six Lambda ZIPs must be re-uploaded
3. Package and upload — see the ZIP packaging instructions in [docs/setup-guide.md](./docs/setup-guide.md#step-4--deploy-lambda-functions)

**Quick re-deploy (PowerShell):**

```powershell
$fn = "validate"   # function name to redeploy
$tmpDir = "tmp-$fn"
New-Item -ItemType Directory -Force $tmpDir
Copy-Item "lambdas\$fn\handler.py" "$tmpDir\"
Copy-Item -Recurse "lambdas\shared" "$tmpDir\shared"
Compress-Archive -Path "$tmpDir\*" -DestinationPath "etl-$fn.zip" -Force
Remove-Item -Recurse -Force $tmpDir
aws lambda update-function-code --function-name etl-$fn --zip-file fileb://etl-$fn.zip
Remove-Item "etl-$fn.zip"
```

### Modifying the Step Functions definition

1. Edit `step-functions/pipeline_definition.json`
2. Open the AWS Console → Step Functions → `etl-pipeline` → Edit
3. Paste the updated JSON (replacing REGION, ACCOUNT_ID, and function name placeholders)
4. Save

### Modifying the DynamoDB schema

DynamoDB is schemaless for non-key attributes. Adding a new field requires only changing the Lambda code — no table migration is needed. Changing the key schema (`jobId`, `status`) requires creating a new table.

---

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/{description}` | `feature/add-parquet-support` |
| Fix | `fix/{description}` | `fix/step-functions-resultpath` |
| Docs | `docs/{description}` | `docs/lessons-learned` |
| Refactor | `refactor/{description}` | `refactor/shared-s3-client` |

---

## Opening a Pull Request

1. Create a branch from `master`
2. Make your changes and commit with a descriptive message
3. Open a PR describing what changed and why
4. Include a test scenario — either a unit test or instructions to manually upload a sample file and verify the expected DynamoDB status and S3 output

---

## Adding a New Pipeline Stage

To insert a new processing step (e.g., a `FilterData` stage between `TransformData` and `EnrichData`):

1. Create `lambdas/filter/handler.py` following the same pattern:
   - Accept the full event dict
   - Do processing
   - Write intermediate output to `processed/{jobId}/filtered.json`
   - Update DynamoDB with the new status via `update_job_status`
   - Return `{**event, "filteredKey": filtered_key}`

2. Add a new status constant to `shared/constants.py` (e.g., `FILTERED = "FILTERED"`)

3. Insert the new Task state into `step-functions/pipeline_definition.json` between `TransformData` and `EnrichData`, with `ResultPath: "$"` and a `Catch` block

4. Create a new Lambda function in the AWS Console and update the Step Functions definition

5. Add the new Lambda ARN to the Step Functions IAM role's inline policy

---

## Security

- Do not commit `.env` files, AWS credentials, or account IDs
- The `.gitignore` excludes `.aws/`, `.env`, and `builds/`
- All sensitive values (bucket names, ARNs, table names) must remain in Lambda environment variables — never hardcoded
- See [SECURITY.md](./SECURITY.md) for the full IAM policy design
