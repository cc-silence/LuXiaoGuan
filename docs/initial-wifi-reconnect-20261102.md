# Initial Wi-Fi replacement with user-paced reconnect

Implemented on 2026-11-02. This is an initial-setup lifecycle change, not a general LAN force-apply option.

## Scope and behavior

The preview obtains the actual SSH source adapter. Initial setup that replaces factory access points uses the reconnect mode only for a proven physical Wi-Fi connection. The existing acknowledgement confirms the displayed no-timeout/no-automatic-rollback impact. Wired initial setup and ordinary LAN settings keep their existing checked apply/confirm/rollback path. The separate investigation of a prior user's virtual-machine LAN failure does not change initialization behavior. No VM-specific option is introduced.

Before any new hotspot or local IP mutation, static client planning checks for router/client address collisions. No alternative client address is silently selected. A fresh adapter inventory must match the reviewed route. The existing first phase creates and verifies the replacement AP while preserving the factory AP. The final phase stages the remaining reviewed operations and performs exactly one native `uci.apply` with `rollback=false`. Hashes are rechecked immediately before submission. A fresh-SID confirm probe requires NO_DATA and rejects a foreign pending rollback before unchecked apply can disturb rpcd snapshots. No router reboot command is added.

A lost submission reply never triggers replay or automatic restoration. The operation returns a pending-reconnect result, not verified success. The final submit phase clears the earlier phase countdown. A bounded atomic receipt survives application restarts without storing Wi-Fi passwords, SSH credentials, private keys, or session IDs. It records the pinned router identity, requested configuration, file hashes and local compensation ownership. At most 16 pending routers and 256 KiB per receipt are accepted; failed temporary writes are removed.

On login, the existing status worker discovers a receipt for the authenticated host fingerprint, then schedules a separate exclusive management task. The task verifies reviewed values, requested LAN runtime, replacement AP identity/readiness, fixed file hashes and remaining service operations. Only then does it finish the owned static client migration and clean owned recovery copies. A final connection-identity check precedes receipt removal. Different host fingerprints cannot consume the receipt; login aliases are accepted only for the same pinned router and all runtime checks still apply. Mismatches retain evidence and do not overwrite unrelated client edits. Interrupted cleanup is idempotent when the expected final client state or absence of an already-cleaned owned directory is proven.

## Official source audit

Local source cache was absent, so exact official sources were downloaded before implementing the changed router operation. `initial-wifi-reconnect-sources-20261102.json` contains the URLs, fixed rpcd commit, SHA-256 and audit date for every supported precise release: 50 releases across 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12, resolving to 14 distinct rpcd implementations. All 14 implementations were read and checked: rollback defaults to false, explicit rollback is parsed, native apply commits selected session deltas and emits configuration events, and scheduling the rollback timer is conditional on rollback=true. The confirm probe uses the existing NO_DATA/foreign SID semantics. Existing capability detection and first-stage AP verification are retained.

This lifecycle does not select radios or introduce hardware-specific commands. AX3600, RT-ACRH17, AX6S, Q30 Pro and the other supported targets continue through actual AP-capacity and runtime capability checks. This source audit is not new live validation of any router model; JCG Q30 remains distinct and unverified.

Qt interface classification follows the documented QNetworkInterface type values: https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QNetworkInterface.html . VMware mode interpretation follows https://knowledge.broadcom.com/external/article?legacyId=1018697 . Host bridge configuration is not inferred or changed by this feature.

## Validation and limits

Targeted tests execute the real Lua request with an isolated rpcd fixture, including stale hashes and a foreign pending rollback. Offline regressions cover acknowledged/lost replies, duplicate requests, wrong routers, login aliases, runtime mismatches, exclusive worker ownership, static finalization, secrets rejection, already-cleaned backup recovery, theme/scale rendering and countdown removal. Existing LAN and policy regressions are also run.

Actual Windows adapter type inspection was read-only. No live router settings or real computer IP addresses were changed during this task. Debian NetworkManager uses the shared policy and receipt logic; new Wi-Fi profile identity changes are treated as external edits and are not overwritten. Debian 12 desktop operation and real Wi-Fi replacement/reconnect on both systems still require live validation. If the application dies before final submission, the prepared receipt deliberately remains unresolved rather than replaying a destructive operation.
