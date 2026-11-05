# Debian 12 adaptation: stage 4 - secure credential foundation and asynchronous integration

This stage includes storage, transport and login-page integration. All changes remain in feature/debian12-runtime.
The active Windows checkout and its dependencies have not been changed.

## Implemented

- A shared credential interface preserves the Windows backend calls and provides
  Linux read/write/delete operations through a dedicated helper process.
- The Linux backend uses SecretStorage 3.3.3 and Jeepney 0.9.0 with the existing
  cryptography pin. requirements-linux.txt includes requirements.txt and the
  two Linux-only pins; Windows requirements.txt is unchanged.
- The runtime admission checker follows local pinned requirement includes and
  verifies both Linux dependencies. Recursive/repeated includes are rejected.
- The backend uses only the default persistent Secret Service collection. It does
  not create a collection or fall back to a temporary session collection.
- The collection must already be unlocked. The app does not invoke an interactive
  unlock call. An encrypted Secret Service session is required before accessing
  secrets, with no plaintext transport fallback.
- Matching is scoped to the application and exact credential target. Writes
  replace that target and verify the result. Deletes verify absence and never
  delete unrelated entries. Duplicate/oversized search results fail safely.
- Connections close at the end of each operation. No persistent bus connection or
  additional password cache is retained in this backend.
- Passwords travel only in the helper's stdin/stdout pipes, not argv, environment,
  ordinary configuration, logs or temporary files.
- Linux synchronous callers are rejected on the Qt GUI thread. Background calls
  have a 10-second process timeout; subprocess.run kills/reaps a timed-out child.
- Exceptions crossing the boundary use fixed error codes/messages. Raw provider
  exceptions are not printed or exposed, because they can contain request data.
- The helper entry point is dispatched before GUI bootstrap.

The capture-service security rule in this isolated worktree now covers Windows
Credential Manager and Linux persistent keyrings.

## Verification

35 focused tests passed:
python -m unittest tests.test_linux_credentials tests.test_linux_runtime tests.test_login_credentials tests.test_router_password -v

All credential data was synthetic. No real user keyring or stored password was
read, written or deleted. New tests used an in-memory Secret Service model and
a real helper-dispatch subprocess with private stdin. Checks covered missing or
locked services, encrypted-session enforcement, exact-target isolation, ambiguous
records, write/delete verification, timeout/error redaction, GUI-thread rejection
and Linux dependency inclusion. Existing Windows login and password-change
regressions continued to pass.

These are offline contracts and Windows regression checks. Actual Debian D-Bus,
a real keyring provider and a packaged private Python runtime still need testing.

## Stage 4B: asynchronous integration completed in source

A resident-service-owned QProcess controller now connects initial reads, remember /
forget toggles, pre-login persistence and router-password-change handoff to Linux
secure storage. Windows callers retain their existing synchronous backend path.

- At most eight queued requests and one active child; ten-second operation budget.
- Passwords use private pipes, never argv, ordinary files or diagnostic output.
- Output is bounded; failed startup, malformed responses and timeouts are errors.
- Application exit cancels pending work and waits for the active child to finish;
  the service reports credential activity to the resident runtime.
- Read replies are guarded by identity, input revision and request intent, so late
  results cannot overwrite newly typed input or credentials for another router.
- Remember/forget settings commit after successful storage verification. Failed
  deletion restores the checkbox, preserves typed input and produces no success
  toast. Empty remembered input cannot delete a saved password during login.
- Login waits for a requested save; edits while waiting invalidate that login.
  Memory-only login does not require a desktop keyring service.
- A changed router password is copied into the bounded helper request before the
  task clears its secret bytearray. Local storage completion/failure is reported
  separately from the already completed remote change, scoped to the original key.
- Timeout/cancellation cannot promise that a provider rolled back an operation;
  persistent state remains unconfirmed until a later verified read/write/delete.
- Existing widgets/styles are retained. Storage/private-key hints now describe
  the current computer rather than claiming that all hosts are Windows.

16 asynchronous integration tests passed separately after the final edits.
The broader isolated audit report is documented in debian12-progress.md.
Actual Debian D-Bus/keyring and the packaged private interpreter remain unverified.
No Linux installer or interpreter bundle has been produced.

## Primary references

- Exact supported dependency metadata:
  https://pypi.org/project/SecretStorage/3.3.3/
  https://pypi.org/project/jeepney/0.9.0/
- Exact pinned collection/session implementation:
  https://github.com/mitya57/secretstorage/blob/3.3.3/secretstorage/collection.py
  https://github.com/mitya57/secretstorage/blob/3.3.3/secretstorage/util.py
- Secret Service overview and synchronous unlock caveat:
  https://secretstorage.readthedocs.io/en/latest/
