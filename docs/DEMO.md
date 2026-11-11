# Demonstration

The [recorded walkthrough](demo.mp4) shows the actual app executing the happy path and edge cases. Its narration is synthesized; it is not presented as the candidate's own voice. All inputs are synthetic. The video is a technical demonstration, not an interview recording or a submission to a hiring coordinator.

## Rehearse in five minutes

Start with a fresh database, following the README. No integration keys or internet access are needed after dependency installation.

1. **0:00 — Explain the problem.** Procurement needs complete, consistent vendor details before enabling payments. Show the empty workspace and its dashboard.
2. **0:25 — Happy path.** New submission → Complete submission. Show the company, tax, bank and labeled evidence. Run checks. Open the six stage results. Show the approval, evidence hashes, masked payment reference and decision-report download.
3. **1:20 — Missing/expired evidence.** Load Missing & expired evidence. Submit. Show pending status and the precise vendor follow-up asking for tax evidence and a current declaration.
4. **2:05 — Cross-field mismatch.** Load Third-party payee. The payee agrees with bank evidence but is a different entity from the vendor: pending for independent review. Invalid routing checksum is another prepared scenario.
5. **2:50 — Cross-run conflict.** Load Shared account after the approved happy path. The same bank account appears under a different tax identity: rejected with escalation, even though the submission agrees internally.
6. **3:40 — History and outputs.** Return to the dashboard, filter by status, reopen a run and download the follow-up. Restarting the server preserves history.
7. **4:15 — Explain limits.** Approval is internal policy, not bank/government/sanctions verification. No email was sent. Structured extraction avoids guessing. Discuss adding OCR, authenticated vendor resubmission, bank verification and sanctions screening as scoped integrations.

## Reproduce the recorded walkthrough

Install development dependencies, Chromium and FFmpeg with the `flite` filter. Run a server with an empty database on port 8001, then:

```sh
VENDORFLOW_DATA_DIR=/tmp/vendorflow-recording .venv/bin/gunicorn \
  'vendorflow.app:create_app()' --bind 127.0.0.1:8001 --workers 1 --threads 4
# In another terminal:
.venv/bin/python scripts/record_demo.py
```

The recorder opens a real Chromium browser, submits actual forms and waits for actual status assertions. Pauses only let the explanatory narration finish; the policy engine itself never delays execution. The output is `docs/demo.mp4`.
