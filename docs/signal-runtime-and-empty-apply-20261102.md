# Signal runtime refresh and empty apply fixes - 2026-11-02

## Confirmed bug 1: stale reload message

The inventory stores running/runtime_note at full-read time. Periodic wireless status updated network rates/peers but not those inventory fields. An AP observed while netifd pending could therefore keep its old reload message after netifd recovered.

The existing native status query now returns bounded per-section runtime observations, using its already available network.wireless status and network.device answers. No additional SSH command or timer is added. Optional observations are parsed into a typed tuple (maximum 256), preserved through rate calculation, and merged only into matching section/radio/mode runtime fields on the same connection. Configuration revision, saved enable flags, SSID and credentials are unchanged. Unknown/missing link data cannot become active; a truly pending radio retains the pending note. Completed samples update cards in place; failed samples retain the previous reading.

## Confirmed bug 2: no staged delta treated as application failure

The pinned official rpcd source returns UBUS_STATUS_NO_DATA when its session delta directory has no nonempty files; the ubus enum assigns that status 5. This is not the permission-denied status. The project already handled this in checked_apply, but the wireless save/toggle stage used a generic RPC wrapper and raised rpc_uci_apply_5. Repeated saving of identical already-enabled AP settings reproduces this gap in the Lua fixture.

Extract the existing native apply handling into one apply_staged primitive, and use it for checked apply, wireless stage and rollback-session toggle. An empty apply is only an unchanged candidate: the existing runtime and saved-value verification still run. Only after they pass may inactive confirmation (NO_DATA) be accepted for that no-apply session. A missing written password, a non-running AP or a different error still fails. The session is destroyed on completion or recovery. No forced reload, timeout extension, new installation, raw configuration logging or driver replacement is introduced.

This explains one concrete error branch; it does not prove that the customer changed nothing in the corresponding failed request. The old ZIPs do not retain that delta. Random MAC, DFS, driver compatibility and recurrent 5 GHz startup failure remain unconfirmed causes. Only-save intentionally stores the signal disabled and bypasses runtime startup; its success does not establish that hardware startup would succeed.

## Sources read

Checked 2026-11-02. OpenWrt 23.05.5 pins rpcd c07ab2f91061ad64209e9aaa1fb1b77061a1af25.
- https://raw.githubusercontent.com/openwrt/openwrt/v23.05.5/package/system/rpcd/Makefile SHA256 de35a17fbb76cc8d0eea577da07f33c349ec78a886290bd32fa94a63e6de6215
- https://raw.githubusercontent.com/openwrt/rpcd/c07ab2f91061ad64209e9aaa1fb1b77061a1af25/uci.c SHA256 7def24c17fe0add17512bc1a16279598875ac6c76b668374eda17722969b5d1b
- https://raw.githubusercontent.com/openwrt/ubus/master/ubusmsg.h was read to verify numeric status definitions; this moving URL is not used as proof of firmware pinning.
- The previously pinned netifd c18cc79d50002ab8529c21184aceb016c61ac61c source was re-read for up/pending/retry_setup_failed and interface fields. It is retained with hashes in the earlier source manifest.

## Verification and boundary

Real Lua tests cover native pending -> active/down sampling, stable configuration revision, missing and duplicate observations, no extra refresh worker and rate metadata retention. Empty-delta tests cover unchanged save, changed MAC then identical save, expected password not persisted and runtime not running. Shared transaction, exact-source readiness and foreground lifecycle suites are run together. Windows offscreen tests only; Debian 12 and the reported customer router are not verified. No commit, push, package or release has been performed.
