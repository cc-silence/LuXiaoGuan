# Local log history verification (2026-10-30)

## Delivered behavior

- The foreground automatic-capture toolbar has an Export logs button immediately after Local results, using the existing secondary-button theme and shared asynchronous ZIP worker.
- Information panels, router-terminal diagnostics and application-update diagnostics persist redacted evidence across application runs. The ZIP retains existing top-level files and adds current-run / previous-run directories and run-history.json.
- Seven days of run history are retained, subject to bounded storage. Export defaults to the current and immediately preceding application run; missing or empty previous-page evidence is explicit. Older, previously memory-only logs cannot be reconstructed retroactively.
- Foreground capture retains the latest task per radio and originating router. A reopened application can export the last router's preceding-run evidence before login. Clearing the visible console or rebuilding a page does not erase stored evidence.
- Background/offline router capture is explicitly excluded at the owner's request. Its scripts, remote logs, polling and lifecycle are unchanged by this work.

## Architecture and safety

- modules/log_history.py owns a bounded asynchronous SQLite writer under RouterManagerData/log-history. The application runtime creates one run identifier; page construction does not rotate runs.
- The writer queue is limited to 128 items and 8 MiB. Text records are capped at 64 KiB; streams retain startup evidence and a recent tail up to approximately 2 MiB / 2048 records, with periodic trimming. History has a 48 MiB payload budget, a 32768-record budget and a 64 MiB database-page ceiling. Export snapshots have an 8 MiB budget and share the existing 20 MiB ZIP budget.
- Incremental commits preserve evidence after abnormal exits. Shutdown drains the writer off the UI thread before releasing the window.
- Startup, throttled writes and export trigger seven-day pruning; SQLite reuses freed pages and incrementally vacuums. Only managed database records are pruned. User ZIPs, packet captures and pending wireless recovery journals/backups are not cleanup targets.
- Credentials, private-key blocks, known network identities, MAC addresses, IP addresses, raw long identifiers and newly discovered hcx network names are filtered before persistence. Split stream lines and private-key blocks are handled before writing. Export applies a second redaction pass.
- Explicit, whitelisted recovery attachments are copied as bounded redacted text rather than keeping references to disposable source files. Auto-capture attachments are bound to their originating router/radio.
- The old foreground duplicate raw text log writer is removed. No remote command or router state transition is introduced by this feature; OpenWrt remote-operation compatibility review is therefore not applicable.

## Validation and limits

- Final result: 107 targeted regressions passed, plus one separate real-application theme/scale/shutdown integration test. Syntax compilation, mojibake scan and git diff --check passed.
- Targeted tests cover run separation, abrupt subprocess exit, seven-day boundaries, write-triggered pruning, source-file deletion, redaction, bounded history, cancelled exports, UI clear/rebuild, actual Qt button clicks, router switching and background capture exclusion.
- The existing recovery, capture, update-export and terminal suites are included in the targeted regression run.
- A real application integration test exercises all eight router tabs, theme/scale changes and asynchronous shutdown.
- Public-theme toolbar screenshots were rendered and inspected for both light and dark themes; the new button text is legible and fits. Artifacts: build/log-history-audit-20261030/controls-light.png and controls-dark.png.
- Validation uses the project .venv (Python 3.10.11, PySide6/Qt 6.3.2). The system Python has Qt 6.3.0 and is not the supported native-terminal test environment.
- No live router was changed or used for these local-log tests. Debian 12 desktop execution remains unverified; verify startup, native save dialog, redacted ZIP, cleanup and shutdown there before claiming platform acceptance.

## Follow-up: complete identity-collision and preflight review

- Root cause: short registered network identities were replaced as substrings inside diagnostic dates; operation/radio time fields also passed through generic text masking.
- Persistence and text export now share validated ISO timestamps and exact generated hcxmon capture-name handling. Unknown filenames, invalid dates, credential-bearing lines, full identities equal to a generated literal, and privacy-budget overflow remain redacted. Structured operation/radio timestamps are validated as typed metadata.
- Regression tests cover short/full identity collisions, repeated redaction, disk persistence, restart, ZIP export, every operation evidence collection, radio timestamps, and privacy counterexamples.
- Read-only replay of both owner-provided ZIPs preserved 586 already-valid timestamp occurrences and checked both real capture filename patterns. Historical text already irreversibly redacted is not reconstructed. Original ZIPs remain unchanged.
- Preflight and final monitor deletion share the same presence/type/phy/delete/postcheck command builder. Missing interfaces are successful ABSENT results. Actual query/delete/postcheck failures retain stderr, exit code and stage; timeout/cancellation stop startup.
- The generated POSIX command was executed locally with a fake iw and isolated temporary files for absent/present/type/phy/query/delete/postcheck cases. No router was contacted; background/offline capture was not changed.
- Exact official OpenWrt v23.05.4 iw Makefile and iw v5.19 interface.c were read and SHA-256 verified against the existing source index under build/hcx-upgrade-audit-20261030/sources. No all-release or all-hardware live validation is claimed.
- Final targeted regression result: 127 tests passed. Real replay results: build/log-history-audit-20261030/collision-replay-result.json. Debian 12/live-device retest remains unperformed.
