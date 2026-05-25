# Lessons Learned

Real problems encountered while building this pipeline, how they were diagnosed, and what the fix taught about AWS and Step Functions internals. Each lesson includes a **Takeaway** that generalises beyond this project.

---

## 1. Step Functions `ResultPath` and the Vanishing Lambda Output

### What happened

After the `ValidateFile` state completed successfully, the `TransformData` state failed immediately with:

```
An error occurred while executing the state 'TransformData'. The JSONPath
'$.taskResult.data.recordCount' could not be found in the input
'{"jobId":"...","taskResult":{"jobId":"...","recordCount":12}}'
```

### Root cause

Two bugs combined:

**Bug 1 — Wrong JSONPath**: `ValidateFile` stored the Lambda result at `$.taskResult`, but the validate Lambda returns `{**event, "recordCount": 12}` directly — a flat dict, not a `success()` wrapper. The JSONPath `$.taskResult.data.recordCount` expected a `.data` nesting that did not exist.

**Bug 2 — `ResultPath: null` discards outputs**: `TransformData` and `EnrichData` were configured with `ResultPath: null`. In Step Functions, this means the Lambda is invoked but its return value is completely thrown away. The state output remains unchanged — so `transformedKey` and `enrichedKey` would never have been visible to downstream states even if the JSONPath bug had been fixed.

### Fix

Changed all processing Task states from `ResultPath: "$.taskResult"` / `ResultPath: null` to `ResultPath: "$"`. This replaces the entire state with the Lambda's return value. Since every Lambda returns `{**event, ...new_fields}`, fields accumulate cleanly across states:

```
Trigger input:    { jobId, sourceBucket, sourceKey, inputFormat }
After Validate:   { jobId, sourceBucket, sourceKey, inputFormat, recordCount }
After Transform:  { jobId, sourceBucket, sourceKey, inputFormat, recordCount, transformedKey }
After Enrich:     { jobId, ..., transformedKey, enrichedKey }
After Load:       { ..., loadResult: { status: "ok", data: {...} } }
```

Also removed the `Parameters` blocks from `TransformData`, `EnrichData`, and `LoadOutput` — they were reconstructing a subset of the input unnecessarily, which hid the problem.

### Takeaway

> **Know your three ResultPath values before you write a single state.**
>
> - `ResultPath: "$"` — Lambda return value **replaces** the entire state (good for passing accumulated state forward)
> - `ResultPath: "$.someKey"` — Lambda return value is **merged** into the state at that key (good for adding a new field without losing the existing state)
> - `ResultPath: null` — Lambda return value is **discarded**; the state passes through unchanged (good for side-effect-only tasks)
>
> If your next state needs a field that a Lambda produces, `ResultPath: null` is almost certainly wrong.

---

## 2. S3 Object Keys With Special Characters

### What happened

When uploading a file whose name contained spaces (e.g. `valid transactions.csv`), the trigger Lambda received a URL-encoded key: `valid%20transactions.csv`. The file format validation checked for `.csv` as the extension, which worked fine, but when the key was passed to the validate Lambda and used in S3 API calls, it would fail with `NoSuchKey` because the encoded key was passed as-is instead of decoded.

### Fix

Added `urllib.parse.unquote_plus` in the trigger handler:

```python
from urllib.parse import unquote_plus
key = unquote_plus(record["s3"]["object"]["key"])
```

### Takeaway

> **Always decode S3 keys from event notifications.** S3 encodes special characters (spaces → `+` or `%20`, `+` → `%2B`) in event notification payloads. The `unquote_plus` call is essential for any file name that may contain spaces, parentheses, or other URL-special characters. Make it a reflex to add this on every S3-triggered Lambda.

---

## 3. DynamoDB Sort Key Design — Status as SK

### What happened

Initially, I considered storing just the latest job status (one DynamoDB item per job, updated in place). The SK would have been a constant like `"JOB"` or the `jobId` itself.

### Why we chose `status` as the SK instead

Using `status` as the sort key means each status transition creates a **new item**. Querying by `jobId` alone returns the complete processing history — an event-sourcing-style audit trail:

```
Query: jobId = "20240115T143022#..."
→ STARTED  (14:30:22)
→ VALIDATED (14:30:24)
→ TRANSFORMED (14:30:26)
→ ENRICHED (14:30:28)
→ LOADED   (14:30:30)
```

This makes it possible to answer questions like: *how long did the transform step take?* or *which step in the pipeline is the slowest?* — without CloudWatch queries. It also means a failed job retains its full partial history, which is valuable for debugging.

### Trade-off

You cannot update an item's sort key in DynamoDB — if you want to mark an item FAILED, you must create a new item (PK: jobId, SK: FAILED) rather than updating the existing one. The `update_job_status` function in `shared/dynamodb_client.py` uses `put_item` for this reason.

### Takeaway

> **Design your DynamoDB key schema around your most important query patterns, not around update convenience.** A pattern that looks redundant (multiple items per logical entity) can unlock powerful query capabilities that would otherwise require a GSI, a Scan, or a separate audit table.

---

## 4. Step Functions Express vs Standard Workflow

### What happened

When creating the state machine, the console asks you to choose Workflow type. Standard Workflow is the default. Using Standard Workflow for this pipeline would work, but it is significantly more expensive for file-processing workloads.

### The difference

| | Express Workflow | Standard Workflow |
|---|---|---|
| **Max duration** | 5 minutes | 1 year |
| **Pricing model** | Per execution + duration | Per state transition |
| **Execution history** | CloudWatch Logs only | Console + CloudWatch |
| **Idempotency** | At-least-once | Exactly-once |
| **Right for** | Short, high-volume | Long-running, audit-critical |

For a file-processing pipeline completing in <30 seconds, Express Workflow is the correct choice. A pipeline processing 1,000 files/day would cost roughly **$0.025/day** with Express vs **$0.625/day** with Standard — a 25× cost difference.

### Takeaway

> **Express Workflows are almost always the right choice for event-driven data pipelines.** Reserve Standard Workflows for human-approval workflows, long-running processes, or cases where exactly-once execution semantics are a hard requirement.

---

## 5. Lambda `shared/` Directory — Bundling Without Layers

### What happened

Python imports in Lambda assume the deployment ZIP root is on `sys.path`. Since `shared/` is bundled alongside `handler.py` in every ZIP (not as a Lambda Layer), the import `from shared.constants import ...` works because at runtime the directory structure is:

```
/var/task/
├── handler.py
└── shared/
    ├── constants.py
    ├── dynamodb_client.py
    ├── s3_client.py
    └── response_helper.py
```

This is intentional. Lambda Layers would be more elegant, but require extra provisioning steps (create Layer version, attach Layer ARN to each function). For a project deployed manually via the Console, bundling `shared/` is simpler and fully self-contained.

### Trade-off

If `shared/` changes, **all six ZIPs must be re-uploaded**. With Lambda Layers, only the Layer version would need updating. This trade-off is acceptable for a portfolio project but would push toward Layers (or IaC) in a team environment.

### Takeaway

> **Choose the deployment model that matches your operational context.** Layers optimise for shared code management at scale; bundling optimises for simplicity and portability. Neither is universally correct — pick based on how many functions share the code and how often it changes.

---

## 6. `malformed_no_header.csv` Passes Trigger, Fails Validate

### What happened

A CSV file without a header row successfully triggered the pipeline (created a STARTED entry, started a Step Functions execution). It was correctly rejected by the validate Lambda, which wrote a FAILED entry to DynamoDB.

This is the correct behaviour — the trigger Lambda only checks the file extension, not the file contents. Content validation is the responsibility of the validate step.

### Why this matters for the architecture

This confirms the single-responsibility principle of the pipeline stages:
- **Trigger**: Format check only (extension)
- **Validate**: Content integrity (structure, size, encoding)
- **Transform**: Business rules (normalisation, deduplication)
- **Enrich**: Enrichment logic (metadata fields)
- **Load**: Output persistence and cleanup

Mixing concerns (e.g., having the trigger download and parse the file) would make the trigger slower and harder to test, and would duplicate logic with the validate step.

### Takeaway

> **In a multi-step pipeline, guard different concerns at different stages.** The trigger's job is to route, not to validate content. Routing is cheap; content validation is expensive. Keeping them separate means each stage can be tested, scaled, and reasoned about independently.

---

## Summary

| # | Lesson | Key Fix |
|---|--------|---------|
| 1 | Step Functions `ResultPath: null` discards Lambda output | Use `ResultPath: "$"` to accumulate state across steps |
| 2 | S3 event keys are URL-encoded | Always `unquote_plus()` the key in the trigger Lambda |
| 3 | DynamoDB SK as `status` enables audit trail | `put_item` per status transition instead of `update_item` |
| 4 | Express vs Standard Workflow: 25× cost difference | Express Workflow for short-duration data pipelines |
| 5 | Bundling `shared/` vs Lambda Layers | Bundle for simplicity; Layers for team/scale environments |
| 6 | Trigger validates format, Validate validates content | Keep single responsibility per pipeline stage |
