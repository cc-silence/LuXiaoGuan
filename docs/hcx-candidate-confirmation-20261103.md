# Verified alternate hcx build confirmation, 2026-11-03

## Behavior

Keep the preset project body and signed local bundle as normal install choices.
If the immutable project download is corrupted, stop. If its network route fails
and an official-source fallback returns a different same-version build, download
within the existing 16 MiB cap and verify against current official source metadata.
The candidate name, exact version/revision, architecture, filename, length (when
published) and SHA256 must match. IPK embedded Package/Version/Architecture control
fields must also match. A source-index mismatch is never eligible for confirmation.

Offer a per-file decision through the existing common ConfirmDialog: Try install
or Skip hcx. It explains that this is another build, source integrity passed,
real-device validation is pending, and uninstalling does not mean complete rollback.
No permanent allowlist or remember choice is added. The original downloaded-file
hash is checked again after the decision. The existing hcx-only installation,
upload hash check, shared-library protections and runtime version checks remain.

## Thread and resource boundaries

The dependency service binds the decision to the original SSH connection and the
current task cancellation event. A service-owned gate emits a queued Qt signal;
only the window-owned presenter creates the dialog on the main thread. Waiting is
bounded to 300 seconds and cancellable. Cancellation or connection replacement
wins over approval; stale/late responses cannot approve a later request. Window
closure, missing presenter, dialog failure and timeout all prevent installation.
The worker never creates a dialog. Temporary candidate files are removed by the
existing context even on refusal, exceptions or cancellation.

## Evidence

The reported proxy run downloaded the original GitHub file: 37681 bytes, pin passed.
The reported direct USTC file was 37675 bytes. A read-only Aliyun probe returned a
37672-byte same-name file from packages-24.10, with a different SHA256; its fixed
24.10.8 path returned 404. These are distinct files, not a proxy-dependent checker.
Version labels alone cannot prove byte identity or a complete download.

Exact-source references: hcx-candidate-sources-20261103.json covers all seven opkg
parser revisions mapped from the retained 50-release index. Existing APK index
parsing is reused. No native package-manager installation options changed.

Tests use a real approved IPK recompressed into another valid same-version build,
and cover larger payloads, source hash/architecture errors, embedded control errors,
refusal, cancellation, post-approval mutation, per-request decisions, connection
replacement, timeout and main-thread presentation. Both common light/dark dialogs
were rendered and inspected: build/hcx-candidate-qa/light.qss.png and dark.qss.png.
No real router writes or trial installation were performed. Full Debian runtime
verification remains pending. Source has not been repackaged or published.

Combined targeted suite: 230 tests passed, including existing login installation,
hcx upload/install/upgrade protection, mirror routing and new candidate decisions.
A final cancellation-order regression was then added: set the task cancellation
event before waking the confirmation so cancellation cannot race into Skip-only.
Public light/dark rendering was inspected with all text and buttons visible.
