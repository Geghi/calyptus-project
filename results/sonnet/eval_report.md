# Evaluation report

- Accuracy vs `label`: 90.0% (36/40)
- Accuracy vs `corrected_label`: 100.0% (40/40)
- Flagged: 1/40
- Accuracy (vs `corrected_label`) of flagged tickets: 100.0% (1/1)
- Accuracy (vs `corrected_label`) of not-flagged tickets: 100.0% (39/39)
- Cost: $0.1768
- Tokens: 75399
- Wall time: 36.5s

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
| T006 | We're closing two sites so I need to cancel those locations. | Cancellation | Billing | Billing | 0.82 | Customer is closing two sites and wants to remove those locations (fewer locations, not leaving the service), which the tie-break rules route to Billing. |
| T007 | My login link keeps expiring before I can use it. | Billing | Account Access | Account Access | 0.85 | User cannot log in because their login link expires before use, which is a login access problem (not clearly an outage-caused bug). |
| T019 | Can you add a feature to cancel a shift swap request? | Cancellation | Feature Request | Feature Request | 0.93 | The ticket asks for a new ability to cancel a shift swap request, which doesn't exist yet. The word "cancel" refers to a swap request, not to leaving the service. |
| T033 | The schedule export to PDF cuts off the last column. | Feature Request | Bug | Bug | 0.95 | Existing PDF schedule export is behaving wrongly by cutting off the last column. |
