# Validation record

Validated in the managed Linux cloud environment with Python 3.12, system Chromium, FFmpeg and Docker. These are observed local results, not claims of public hosting or a GitHub Actions run.

## Automated checks

- **28 pytest tests passed** over policy, HTTP API, TXT/PDF uploads, output masking, authentication, limits, persistence and recovery.
- **Browser smoke passed**: five actual form submissions returned 1 approved, 3 pending, 1 rejected; six persisted stages per run; decision-report downloads; dashboard totals; rejection filtering; historical result reopening; responsive mobile layout without horizontal page overflow; no JavaScript runtime errors.
- The same five browser scenarios passed against the Docker application.
- Docker image built with TLS verification intact and ran as its configured unprivileged user.
- Restarted the Docker container with its named data volume: all five decisions and counts remained intact.
- `node --check vendorflow/static/app.js`, Python compilation and Git whitespace checks passed.

## Tests include

The happy path; missing and expired evidence; third-party payee; invalid routing checksum; cross-identity bank reuse; duplicate tax identity; contradictory tax/bank evidence; declared sanctions; unsupported country; wrong document type; UK IBAN happy path and invalid checksum; malformed JSON and fields; genuine text extraction from a generated PDF; blank PDF routing to pending; corrupt PDF rejection; unsupported file types, bad UTF-8 and upload size; cross-origin denial; absent token denial; authenticated API access; masked report data; history across app restart; interrupted-job resumption; explicit retry after a processing failure; expiry boundary; unsigned declarations; missing identity values distinguished from explicit conflicts.

Browser testing identified a transient stale-status display when switching runs. The loading state now resets the status immediately. The scenario and download assertions then passed against the container.

## Demonstration artifacts

- [Dashboard screenshot](screenshots/dashboard.png)
- [Rejected run screenshot](screenshots/rejected.png)
- [Mobile form screenshot](screenshots/mobile.png)
- [Real workflow recording with synthesized narration](demo.mp4)

The recording uses real submissions and actual results. Pauses give the synthesized explanation time to finish; there are no artificial delays in the policy engine.

## Not claimed

No public hosting was provisioned. No hiring-coordinator submission or external vendor email was sent. External bank ownership, tax registry checks, sanctions screening, OCR and document authentication are not implemented. GitHub Actions configuration is provided; its remote result must be observed separately. Production security/compliance suitability has not been established. See the README for the precise operating scope and data-storage limitations.
