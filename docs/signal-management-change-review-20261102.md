# Signal management change review - 2026-11-02

Scope: this conversation's signal status refresh, startup diagnostics,
cancellation, failure readback, coordinated completion and empty-apply changes.
Unrelated uncommitted capture/dependency/release changes are excluded.

## Findings reproduced and corrected

1. P2: runtime sampling now replaces SignalInventory, while the confirmation
   dialog required the original object identity. A runtime-only update could
   reject an otherwise valid enable/disable confirmation. Full-read generation
   is now tracked separately. Acceptance requires the same connection,
   generation, revision and saved signal fields; only runtime fields may differ.
   Existing protection against replacement/full re-read and reconnection is
   retained, including identical file hashes on a different connection.

2. P2: the Lua collector accepted runtime identities containing dot, hyphen or
   colon, but the new parser accepted only letters, digits and underscore. One
   emitted identity could reject the entire sample. Parser and collector now
   share the same ASCII character range and 64-character limit. Invalid,
   duplicate and oversized observations remain rejected.

3. P3: direct toggle cancellation raises SignalError(code=cancelled), while
   startup evidence classified cancellation only by InterruptedError. It now
   recognizes both forms. Cancellation belongs in configuration events and the
   general failure collection, not configuration_failures, which deliberately
   retains failed/uncertain terminal outcomes only.

4. P2: an enable/disable operation could be verified successfully, then be
   reported as a failed write when its combined editing-environment inspection
   lost a channel. Known read/probe failures now retain the verified inventory,
   invalidate unavailable editing metadata and report the follow-up failure
   separately. They do not repeat the write. Unknown program errors, uncertain
   mutation results and disconnection are not converted into success. A result
   from an old router connection cannot be published after reconnection.

## Screenshot distinction

The screenshot's router_signal_probe / exec_request / SSHException is a real
status-probe transport failure, distinct from rpc_uci_apply_5. A successful AP
creation does not imply all subsequent SSH requests will succeed. Independent
post-create inspection can still report this failure; the transport cause on
the customer router has not been established or fixed. This review corrects
result attribution for the newly coordinated toggle path, not the underlying
connection issue or every post-create message route. No retry/timer workaround,
driver change or suppression of underlying diagnostic evidence was added.

## Validation

Each finding was reproduced before the correction. Tests cover runtime-only
confirmation, changed saved values with a reused hash, actual service read
generation, reconnection protection, collector identity boundaries, both
cancellation forms, follow-up channel failure, old-router result rejection and
unknown exception handling. Relevant existing protection tests remain intact.
65 targeted tests passed before the final combined run. Final combined results
are reported in the chat. Python syntax and diff checks are required.
Windows offscreen verification only; Debian 12 and the customer router remain
unverified. This is not a full-repository audit. No commit, push or release.
