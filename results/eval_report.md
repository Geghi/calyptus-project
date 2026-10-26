# Evaluation report

- Accuracy vs `label`: 90.0% (36/40)
- Accuracy vs `corrected_label`: 100.0% (40/40)
- Flagged: 2/40
- Accuracy (vs `corrected_label`) of flagged tickets: 100.0% (2/2)
- Accuracy (vs `corrected_label`) of not-flagged tickets: 100.0% (38/38)
- Cost: $0.0110
- Tokens: 79165
- Wall time: 26.2s

## Precision and recall (vs `corrected_label`)

| Category | Precision | Recall |
|---|---|---|
| Billing | 1.00 | 1.00 |
| Bug | 1.00 | 1.00 |
| Account Access | 1.00 | 1.00 |
| Feature Request | 1.00 | 1.00 |
| Cancellation | 1.00 | 1.00 |

## Confusion matrix (rows: `corrected_label`, columns: predicted)

|  | Billing | Bug | Account Access | Feature Request | Cancellation | failed |
|---|---|---|---|---|---|---|
| Billing | 10 | 0 | 0 | 0 | 0 | 0 |
| Bug | 0 | 9 | 0 | 0 | 0 | 0 |
| Account Access | 0 | 0 | 8 | 0 | 0 | 0 |
| Feature Request | 0 | 0 | 0 | 8 | 0 | 0 |
| Cancellation | 0 | 0 | 0 | 0 | 5 | 0 |

## Misclassified tickets

| Ticket | Text | Label | Corrected | Predicted | Confidence | Reason |
|---|---|---|---|---|---|---|
| T006 | We're closing two sites so I need to cancel those locations. | Cancellation | Billing | Billing | 0.85 | The customer is reducing their location count by closing two sites, not leaving the service, which the rules classify as Billing (fewer locations) despite the word "cancel." |
| T007 | My login link keeps expiring before I can use it. | Billing | Account Access | Account Access | 0.72 | The user cannot log in because the magic login link expires before use, which is a login problem; it could also be read as a bug in link expiry, but the core issue is access. |
| T019 | Can you add a feature to cancel a shift swap request? | Cancellation | Feature Request | Feature Request | 0.93 | The ticket asks for a capability that does not exist yet (cancelling a shift swap request), which is a Feature Request even though it mentions "cancel". |
| T033 | The schedule export to PDF cuts off the last column. | Feature Request | Bug | Bug | 0.95 | The schedule PDF export exists but is broken: it truncates the last column. |
