# Legacy IP security policy compatibility (2026-11-04)

Source implementation; no release package or live policy modification performed.

## Scope

Normal Windows firewall processing retains the existing LocalGPO path. Only MpsSvc
explicitly disabled AND stopped selects the legacy IPsec route. Profile switches
alone and failed service queries do not qualify. Linux uses no Windows policy API.

Before SSH login, the worker prepares the literal unicast IPv4 address and TCP port
selected for that login. Existing local assigned policy receives an exact endpoint
permit filter with mirrored return traffic. No new policy is assigned. This legacy
mechanism applies to all applications communicating with that endpoint, not only
the RouterManager executable. Downloads and unrelated ports are outside this scope.

First version rejects domain-joined machines, domain/cache/persistent policy stores,
ambiguous policy names, hostnames/IPv6 and unavailable BFE/PolicyAgent. Services are
never enabled or restarted. With no assigned legacy policy, no policy is created;
the software proceeds to the ordinary SSH attempt without claiming policy exemption.

## Mutation and recovery

Structured requests reuse the existing administrator session and validation on both
sides. No shell, raw commands, registry paths or backup paths come from the client.
The privileged worker serializes policy updates. Before mutation, netsh exports the
policy into a uniquely and atomically created administrator/SYSTEM-only directory:
Windows/Temp/RouterManager-IPsec-<random id>. The protected directory also contains
result.json with export SHA256 and transaction outcome. At 32 backup directories,
further new endpoints are rejected for administrator review; backups are not erased.
Verified existing endpoint rules are reused without another export or new rules.

Read-only snapshots compare every original local-store object between operations.
The active assignment is preserved; only its rule references and change timestamp
may change. netsh dump must independently describe the exact new filter/action/rule.
Failures remove only objects added by this transaction when the original policy
and ownership can still be verified. Unrelated concurrent edits stop automatic
rollback and report unconfirmed recovery, rather than importing an old policy over
someone else's changes. Successful configuration verification is followed by the
existing actual TCP/SSH/host-key/authentication checks; it is not proof of reachability.

Native commands have individual deadlines, a 90-second modification budget and a
30-second rollback reserve; the mutex wait is 10 seconds. Administrator authorization
uses the existing session behavior. Cancellation before/after authorization prevents
SSH continuation, but does not interrupt a partially running policy transaction:
the privileged worker finishes verification/recovery before returning.

## Evidence and verification

Feedback operation diagnostics now include at most eight sanitized network_policy
records: service states, selected route, TCP port, outcome, elapsed time, completed
steps, native authorization errors and rollback status. No passwords, policy dumps,
pre-shared keys or backup contents enter this structure. Detailed policy failures
also reach the existing error logger. Backups stay local and protected.

Offline regressions cover strict targets, request boundaries, backup failure,
step failures and rollback, concurrency protection, duplicates, repeat login,
disabled-service selection, ordinary firewall regressions, UAC cancellation error
codes, bounded diagnostics and feedback export. Windows service and domain APIs
were exercised read-only. Real legacy-policy mutation, runtime filter activation,
localized populated netsh dump output and disabled-firewall SSH connectivity remain
UNVERIFIED. Unsupported output formats fail verification and invoke safe recovery;
this is a first test version, not full live-environment certification.

## Primary references

- https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-ipsec
- https://learn.microsoft.com/en-us/azure/expressroute/expressroute-howto-ipsec-transport-private-windows
- https://learn.microsoft.com/fr-fr/security-updates/security/20141607
- Local read-only help: netsh ipsec static add rule ? / add filter ?

Local help confirms tunnel expects an endpoint address, not a yes/no switch;
transport rules omit that option and reject nonzero tunnel endpoints on verification.

## Follow-up review

See legacy-ipsec-three-round-review-20261104.md for three rounds of fixes and the
117-test final regression. Receipts are now atomic, flushed commits for every step;
raw command output is bounded during capture. Output decoding supports the current
Python 3.10 runtime, Unicode and Windows code pages, followed by full semantic
verification. Service-mode changes invalidate startup audit completion. No live
policy mutation or Debian machine certification is implied by these results.

## Second review and additional source cross-checks

See legacy-ipsec-six-round-review2-20261104.md and its source index for the next
three code rounds plus three added documentation rounds. Final regression: 137
targeted tests. In particular, system tool/backup paths use the shared system
Windows directory API, rather than the potentially per-user Windows directory.
