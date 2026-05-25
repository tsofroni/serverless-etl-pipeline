# CI/CD Guide

This guide explains two approaches to automating Lambda deployments for this pipeline: **GitHub Actions** (external CI/CD) and **AWS-native services** (CodePipeline + CodeBuild). It covers how each works, their trade-offs, and why this project deliberately chose the AWS-native path.

---

## Comparison Overview

| Criterion | GitHub Actions | AWS CodePipeline + CodeBuild |
|-----------|---------------|------------------------------|
| **Where it runs** | GitHub-managed runners | AWS-managed build environment |
| **AWS credential management** | Secrets stored in GitHub (OIDC or long-lived keys) | IAM roles — no external secret storage |
| **Ecosystem integration** | Strong with GitHub (PRs, checks, badges) | Deep with AWS (CloudWatch, SNS, EventBridge) |
| **Visibility** | GitHub Actions tab | AWS Console (CodePipeline, CodeBuild) |
| **Cost** | Free tier: 2,000 min/month; ~$0.008/min after | CodeBuild: $0.005/min (general1.small); CodePipeline: $1/active pipeline/month |
| **Tooling knowledge required** | YAML + GitHub ecosystem | AWS IAM + CodeBuild + CodePipeline |
| **Vendor dependency** | GitHub + AWS | AWS only |
| **Trigger flexibility** | Push, PR, schedule, manual, webhook | S3 change, CodeCommit push, GitHub (via CodeStar connection), EventBridge |
| **Secrets handling** | GitHub Secrets (encrypted, external) | SSM Parameter Store / Secrets Manager (internal AWS) |
| **Portfolio signal** | Demonstrates GitHub ecosystem knowledge | Demonstrates AWS-native DevOps skills |

---

## Option A — GitHub Actions

### How it works

A GitHub Actions workflow file (`.github/workflows/deploy.yml`) runs on every push to `master`. The workflow:
1. Checks out the repository
2. Detects which Lambda functions changed (using path filters)
3. Packages each changed function as a ZIP
4. Uses AWS credentials (via OIDC or stored secrets) to call `aws lambda update-function-code`

### Example workflow

```yaml
name: Deploy Lambda Functions

on:
  push:
    branches: [master]

permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  detect-changes:
    runs-on: ubuntu-latest
    outputs:
      trigger: ${{ steps.filter.outputs.trigger }}
      validate: ${{ steps.filter.outputs.validate }}
      transform: ${{ steps.filter.outputs.transform }}
      enrich: ${{ steps.filter.outputs.enrich }}
      load: ${{ steps.filter.outputs.load }}
      error-handler: ${{ steps.filter.outputs.error-handler }}
      shared: ${{ steps.filter.outputs.shared }}
    steps:
      - uses: actions/checkout@v4
      - uses: dorny/paths-filter@v3
        id: filter
        with:
          filters: |
            trigger:      ['lambdas/trigger/**']
            validate:     ['lambdas/validate/**']
            transform:    ['lambdas/transform/**']
            enrich:       ['lambdas/enrich/**']
            load:         ['lambdas/load/**']
            error-handler:['lambdas/error-handler/**']
            shared:       ['lambdas/shared/**']

  deploy:
    needs: detect-changes
    runs-on: ubuntu-latest
    strategy:
      matrix:
        function: [trigger, validate, transform, enrich, load, error-handler]
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::ACCOUNT_ID:role/etl-github-actions-role
          aws-region: eu-west-1

      - name: Deploy ${{ matrix.function }}
        if: |
          needs.detect-changes.outputs[matrix.function] == 'true' ||
          needs.detect-changes.outputs.shared == 'true'
        run: |
          mkdir -p tmp
          cp lambdas/${{ matrix.function }}/handler.py tmp/
          cp -r lambdas/shared tmp/shared
          cd tmp && zip -qr /tmp/etl-${{ matrix.function }}.zip . && cd ..
          aws lambda update-function-code \
            --function-name etl-${{ matrix.function }} \
            --zip-file fileb:///tmp/etl-${{ matrix.function }}.zip
```

### IAM role for GitHub Actions (OIDC)

Using OIDC avoids storing long-lived AWS keys in GitHub. The role trusts GitHub's identity provider:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:tsofroni/serverless-etl-pipeline:*"
        }
      }
    }
  ]
}
```

Attach an inline policy granting `lambda:UpdateFunctionCode` on the six ETL functions.

### When GitHub Actions is the right choice

- Your team already uses GitHub for all workflows (PRs, code review, issue tracking)
- You want deployment status visible in the GitHub PR checks UI
- You need to deploy to multiple cloud providers from the same pipeline
- Your organisation has GitHub Enterprise and strong GitHub expertise

---

## Option B — AWS CodePipeline + CodeBuild (this project)

### How it works

```
GitHub (push to master)
        ↓  (CodeStar Connection / webhook)
CodePipeline Source Stage
        ↓
CodeBuild Build Stage
   → Detects changed functions
   → Packages ZIPs
   → Calls lambda:UpdateFunctionCode
        ↓
(Optional) Approval / Notify Stage
   → SNS notification on success/failure
```

Everything runs inside AWS. No external system holds credentials.

### Step-by-step setup

#### 1. Create an S3 artifact bucket

CodePipeline needs a bucket for pipeline artifacts:

- **Bucket name**: `your-name-etl-cicd-artifacts`
- Enable versioning
- Block all public access

#### 2. Connect GitHub to AWS (CodeStar Connection)

1. Go to **CodePipeline → Settings → Connections → Create connection**
2. **Provider**: GitHub
3. **Connection name**: `etl-pipeline-github`
4. Click **Connect to GitHub** and authorise the AWS Connector app
5. Copy the Connection ARN — you'll need it in step 4

#### 3. Create the CodeBuild project

1. Go to **CodeBuild → Create build project**
2. **Project name**: `etl-pipeline-build`
3. **Source**: No source (CodePipeline provides it)
4. **Environment**:
   - Managed image: Amazon Linux 2023
   - Runtime: Standard
   - Image: `aws/codebuild/standard:7.0`
   - Service role: Create a new role (`codebuild-etl-pipeline-role`)
5. **Buildspec**: Use a buildspec file → `buildspec.yml`
6. Click **Create build project**

Add the following inline policy to the CodeBuild service role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UpdateLambdaCode",
      "Effect": "Allow",
      "Action": "lambda:UpdateFunctionCode",
      "Resource": [
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-trigger",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-validate",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-transform",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-enrich",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-load",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:etl-error-handler"
      ]
    },
    {
      "Sid": "S3ArtifactAccess",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:GetBucketVersioning"],
      "Resource": [
        "arn:aws:s3:::your-name-etl-cicd-artifacts",
        "arn:aws:s3:::your-name-etl-cicd-artifacts/*"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "*"
    }
  ]
}
```

#### 4. Add `buildspec.yml` to the repository

Create `buildspec.yml` in the repository root:

```yaml
version: 0.2

phases:
  install:
    runtime-versions:
      python: 3.12

  build:
    commands:
      - echo "Packaging and deploying changed Lambda functions..."

      - |
        deploy_function() {
          fn=$1
          echo "Deploying etl-$fn..."
          mkdir -p tmp-$fn
          cp lambdas/$fn/handler.py tmp-$fn/
          cp -r lambdas/shared tmp-$fn/shared
          cd tmp-$fn && zip -qr /tmp/etl-$fn.zip . && cd ..
          aws lambda update-function-code \
            --function-name etl-$fn \
            --zip-file fileb:///tmp/etl-$fn.zip
          echo "etl-$fn deployed successfully"
          rm -rf tmp-$fn /tmp/etl-$fn.zip
        }

      - |
        # Get list of changed files in this commit
        CHANGED=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || git diff --name-only HEAD)

        SHARED_CHANGED=false
        echo "$CHANGED" | grep -q "lambdas/shared/" && SHARED_CHANGED=true

        for fn in trigger validate transform enrich load error-handler; do
          if echo "$CHANGED" | grep -q "lambdas/$fn/" || [ "$SHARED_CHANGED" = "true" ]; then
            deploy_function $fn
          else
            echo "No changes in etl-$fn — skipping"
          fi
        done

  post_build:
    commands:
      - echo "Build and deployment complete"

artifacts:
  files:
    - '**/*'
```

#### 5. Create the CodePipeline

1. Go to **CodePipeline → Create pipeline**
2. **Pipeline name**: `etl-pipeline-cicd`
3. **Service role**: Create a new role
4. **Artifact store**: Custom location → `your-name-etl-cicd-artifacts`
5. Click **Next**

**Source stage:**
- Provider: GitHub (Version 2)
- Connection: select `etl-pipeline-github`
- Repository: `tsofroni/serverless-etl-pipeline`
- Branch: `master`
- Output artifact format: CodePipeline default

**Build stage:**
- Provider: AWS CodeBuild
- Project name: `etl-pipeline-build`

**Deploy stage:** Skip (deployment is handled by CodeBuild)

6. Review and click **Create pipeline**

The pipeline will trigger on every push to `master`.

#### 6. Add pipeline execution permissions to the CodePipeline role

The auto-created CodePipeline role needs permission to start CodeBuild:

```json
{
  "Effect": "Allow",
  "Action": ["codebuild:BatchGetBuilds", "codebuild:StartBuild"],
  "Resource": "arn:aws:codebuild:REGION:ACCOUNT_ID:project/etl-pipeline-build"
}
```

---

## Why This Project Chose AWS-Native CI/CD

This project deliberately chose CodePipeline + CodeBuild over GitHub Actions for three reasons:

### 1. No credentials leave AWS

With GitHub Actions, AWS credentials must be stored externally — either as long-lived IAM access keys in GitHub Secrets, or via OIDC federation which requires additional IAM configuration and trust policies. With CodeBuild, the build environment runs inside AWS and authenticates via an IAM service role. There are no secrets to rotate, no external identity provider to configure, and no risk of credentials leaking through GitHub's systems.

### 2. Demonstrates AWS DevOps skills

For an AWS Community Builder portfolio, showing proficiency with AWS-native DevOps services (CodePipeline, CodeBuild, CodeStar Connections) demonstrates a deeper understanding of the AWS ecosystem than using GitHub Actions — which is primarily a GitHub product. Reviewers can see that you understand how AWS manages CI/CD at the service level, not just how to call AWS APIs from a third-party runner.

### 3. Full AWS Console visibility

CodePipeline integrates directly with the AWS Console. Build logs appear in CloudWatch. Pipeline execution history is queryable via the AWS CLI or SDK. You can add SNS notifications, approval gates, or EventBridge triggers without leaving the AWS ecosystem. This is particularly valuable for compliance and audit-heavy organisations where a full audit trail in one platform is required.

### Trade-offs acknowledged

- GitHub Actions provides a better pull request integration experience (checks visible directly on the PR)
- CodePipeline costs $1/active pipeline/month even with zero executions — GitHub Actions is free up to 2,000 minutes/month
- The `buildspec.yml` approach is less expressive than GitHub Actions matrix builds for detecting per-function changes

For a team-based product, the choice between the two would depend on existing tooling and team expertise. For this portfolio project, AWS-native CI/CD is the right signal to send.

---

## Monitoring the Pipeline

After setup, monitor deployments at:

- **CodePipeline console** → `etl-pipeline-cicd` → execution history
- **CodeBuild console** → `etl-pipeline-build` → build logs
- **CloudWatch Logs** → `/aws/codebuild/etl-pipeline-build`

A failed build will mark the pipeline stage red in the Console. You can configure an SNS notification from CodePipeline to receive an email on failure:

1. Go to **CodePipeline → `etl-pipeline-cicd` → Notify**
2. Create a notification rule → select `Failed` events → target: SNS topic
