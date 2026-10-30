# Signal background read handoff (2026-10-29)

The visible signal page samples runtime status every two seconds. Previously each
read marked the verified configuration busy/stale and disabled every row action.
Runtime reads now retain the transport mutex while leaving configuration actions
available. A user request occupies one bounded handoff slot, cancels the read, and
starts only from the existing thread-completion callback. No additional timer,
polling, worker pool, or concurrent remote command path was introduced.

Before dispatch, the service checks the SSH connection object, inventory identity,
verified state, connection state, and shutdown flag. Duplicate requests are rejected.
Editor cancellation removes its own queued request. Completion and rejection release
all queued callbacks and cancellation-event references. Genuine configuration tasks
and unverified configurations continue to lock the controls.

## Compatibility scope and evidence

This changes when existing router operations execute, so the router compatibility
rule applies. No remote commands, parameters, result parsers, radio selection,
confirmation dialogs, backups, or rollback behavior changed. The handoff is shared
Python/Qt code for Windows and Debian 12, independent of router model/architecture.

The 14 fixed rpcd iwinfo implementations in the existing pinned source manifest
were read on 2026-10-29: rpc_iwinfo_info and rpc_iwinfo_assoclist remain read paths.
Each cached iwinfo.c.txt SHA-256 matched its manifest entry. Exact fixed commits,
source URLs and individual SHA-256 values are recorded in
build/compatibility-audit-20261029/sources/pinned-manifest.json.
Manifest SHA-256: 286b051728e473e2db3af232998e28d0343978c53129533b577ce62b8c558bb5.
This source check is not live validation of all supported OpenWrt versions/models.

## Validation

- Project virtual environment: targeted status, handoff, action, scheduling,
  editor-refresh and request-boundary regressions pass.
- Real QThread regression holds the cancelled reader in teardown and proves the
  foreground operation cannot start until that thread has exited.
- Windows native Qt backend: visible signal windows in both themes preserve row
  action availability during sampling; edit clicks dispatch and accepted actions
  lock controls during handoff. Images: build/review/signal-foreground-handoff/.
- Formal main.py --smoke-test opens the application and settings, visits all six
  business tabs, and verifies normal cleanup (exit 0).
- Syntax/UTF-8 and scoped git diff --check pass.

Live-router cancel latency and operations remain to be checked on an authorized
connection. Debian 12 desktop/import/exit validation is also pending; Windows
results are not a Debian acceptance result. The desktop input helper could not
start, so native Qt button tests do not claim manual mouse-driven acceptance.
