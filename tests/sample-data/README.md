# Sample Test Data

Upload these files to your S3 drop-zone bucket to test different pipeline scenarios.

| File | Upload to S3? | Expected outcome |
|------|--------------|-----------------|
| `valid_transactions.csv` | Yes | Pipeline runs to completion. DynamoDB shows STARTED → VALIDATED → TRANSFORMED → ENRICHED → LOADED. Final `result.json` appears in the output bucket. TXN008 and TXN010 are duplicates of TXN001 (same fields after normalization) and may be deduplicated by the transform step. |
| `valid_transactions.json` | Yes | Same as above, via the JSON code path. Null `Amount` on TXN004 is preserved as `null` in the output. |
| `invalid_format.txt` | Yes | Trigger Lambda raises `ValueError: Unsupported file format`. No DynamoDB entry is created. No Step Functions execution is started. Check `/aws/lambda/etl-trigger` in CloudWatch for the error. |
| `malformed_no_header.csv` | Yes | Trigger creates a STARTED entry in DynamoDB and starts a Step Functions execution. The Validate Lambda raises `ValueError: CSV file has no header row`. Step Functions routes to ErrorHandler. DynamoDB shows a FAILED entry with the error message. |
| `malformed_invalid_json.json` | Yes | Same flow as above. The Validate Lambda raises `ValueError: File is not valid JSON`. DynamoDB shows a FAILED entry. |

## Notes on the CSV test data

- `valid_transactions.csv` includes an intentional empty `Amount` field on TXN004 — the transform step converts empty strings to `null`.
- TXN001, TXN008, and TXN010 share identical fields (`49.99`, `Food`, `Grocery shopping`, `Supermarket AG`) but different `Transaction ID` values. After column normalization, they differ only on `transaction_id`, so they will **not** be deduplicated (deduplication is based on all fields including the ID).
- Dates are already in ISO 8601 format — the transform step will re-parse and reformat them, which is a no-op here.

## Observing results

After uploading a file:
1. **Step Functions console** → State machines → `etl-pipeline` → Executions — check the execution graph
2. **DynamoDB console** → Tables → `etl_jobs` → Explore items — filter by `jobId`
3. **S3 console** → output bucket → `output/` prefix — download `result.json`
4. **CloudWatch console** → Log groups → `/aws/lambda/etl-*` — inspect logs for each step
