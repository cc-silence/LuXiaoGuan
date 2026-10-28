# Factory reset: login guidance and local network recovery

Implemented 2026-10-28. This does not change the native reset/backup/reboot transaction.

## Shared components

- `LanRecoveryPanel` is reused in `FactoryResetResultDialog`; current LAN apply dialogs retain their task-bound behavior.
- `LanRecoveryService.create_offer()` creates a post-task offer, valid for 10 minutes and revoked on dialog close/destruction. It does not retain the reset worker or its password.
- Renewal starts only on an explicit click. The original routed adapter is preferred; ambiguous routes require selection. Static settings are never rewritten. Each OS operation remains bounded and verifies adapter identity through the existing platform facade.
- The service owns renewal workers until completion, including when the result dialog closes. Closing revokes permission for work not yet started; an OS call already running finishes within its existing timeout. No timer is used to guess router readiness or schedule a network mutation.
- The button is offered after the reset task returns, with instructions to wait for router reboot. A returned reset command does not prove the router has completed booting. Renewal is not a router readiness test.

## Login metadata

Before resetting, read the firmware image's `/rom/etc/shadow` and emit only `empty`, `preset`, `locked`, or `unknown`. Never return a password hash. Prefer `/rom/etc/config/network`, otherwise the board LAN defaults used by OpenWrt config generation. Accept IPv4 address and CIDR representations; use the official 192.168.1.1 fallback only for a static LAN without an override. Unknown addresses and password states are explicitly identified.

The result shows the default address, root user, and empty-password guidance when confirmed by the image. Uncertain reset execution remains explicitly uncertain. The original backup and reboot result remain visible. Firmware boot-time customization may affect final runtime settings; those are not claimed verified.

## Compatibility evidence

Official config-generation sources were checked for 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12. All use a board LAN override or 192.168.1.1 for a static LAN; 25.12 stores generated IPv4 addresses as CIDR lists. Official shadow defaults were fetched for 19.07, 21.02, 23.05, 24.10 and 25.12 and have an empty root password. The 22.03 shadow fetch was unavailable in the browser; the implementation reads the actual firmware image instead of assuming it is empty.

Source URL pattern (replace SERIES with the versions above):
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-SERIES/package/base-files/files/bin/config_generate
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-SERIES/package/base-files/files/etc/shadow

There are no board-name, radio-number, CPU or driver branches in this feature. It inherits the existing standard reset-helper and squashfs/overlay capability gate; unsupported storage layouts are still rejected. No additional router packages are installed.

## Validation boundary

`tests/test_factory_reset_recovery.py` covers actual production shell output with an isolated filesystem, unchanged shadow bytes, password-state/address handling, native reset uncertainty, manual renewal, concurrent-click prevention, expiry, failure recovery, post-worker availability, UI wiring and offer revocation.

Screenshots for dark/light themes at 100% and 150% are stored under `build/review/factory-reset-20261028/`. Existing maintenance, official shell-contract and LAN recovery tests are also run. No real router was factory-reset; no real adapter was changed. Debian 12 runtime and real hardware reset/boot behavior remain unverified.

Final targeted validation: 86 tests passed on 2026-10-28. Nine touched Python modules/tests passed syntax and UTF-8 checks; git diff whitespace validation passed. The result and exact source URLs are recorded in `build/review/factory-reset-20261028/summary.json`.
