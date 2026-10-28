# Common settings: complete window re-audit, 2026-10-28

## Confirmed findings and fixes

1. Restore result contract was incomplete. RouterBackupManager.execute returned no operation discriminator for restore, although the view relied on operation=restore to suppress the duplicate modal. The real success AND uncertain paths now identify restore. Uncertain results are warnings in InfoPanel. A new integration test executes actual archive inspection/upload/hash/restore control flow through the deterministic device adapter, then passes its unmodified result to MaintenanceActions. The previous UI-only fixture supplied the missing field and did not expose this defect.
2. Shared relocation/orphan action messages still wrote status_label directly. Once the common page hides that duplicate banner, rejected-start and changed-connection messages could disappear. page_feedback.publish_page_message now uses the owning page's _message contract, with a status-label fallback for the existing signal page. The password dialog's changed-connection notice uses _message too. This is message routing only; no new visual layout or new dialog was introduced.
3. Relocation confirmation and both empty-preview dialogs were not explicitly released after exec(). Repeated visits retained child Qt objects until their page was destroyed. All three now use try/finally and deleteLater. Tests cover confirm cancellation, empty relocation and empty orphan results. Existing confirmation text and execution boundaries remain.

The old test named backup_and_restore_results_always_show_modal_notice still encoded the superseded restore requirement and omitted the operation discriminator. It was replaced with explicit backup/restore cases: backup keeps its modal, restore routes to InfoPanel. The initial broad run exposed that outdated assertion; final verification uses the updated tests and final code. No failures were hidden by skipping tests.

## Window coverage

| Function | Audit boundary |
| --- | --- |
| Router login password | Validation, independent password verification, request secret clearing, queued connection ownership and failure reporting. |
| LAN settings | Old/new address formats, editable mask, save-only vs native apply, rollback/force guards, same-device reconnect, explicit local adapter renewal, cancellation and shutdown. |
| Hostname/timezone | Anonymous UCI section identities, zone selection, save and runtime verification. |
| Relocation | Preview ownership, native single apply, reconnect/rollback/confirm failure cases, no cross-login adoption, result feedback and dialog cleanup. |
| Orphan interfaces | Read-only discovery, explicit selection and confirmation, configuration revision checks, no network reload, shared feedback and dialog cleanup. |
| Export backup | Native archive creation, bounded size, checksum and archive inspection, destination identity, cancellation and cleanup. |
| Restore backup | Optional current backup, frozen reviewed archive, unsafe member/parent checks, native listing/hash/space checks, guarded irreversible phase and real result-to-UI contract. |
| Factory reset | Firmware capability detection, mandatory pre-change backup, original default address/password metadata, explicit post-result network renewal and offer lifecycle. |
| Reboot | Authenticated same-device preflight, no command resubmission on disconnect, accepted vs uncertain outcome. |
| Initial security | Selected local inputs preflight, shared planner validation, default-hotspot opt-in, actual radio capability checks, create/verify replacement before deleting defaults. |
| Boot station connection policy | Explicit preview/apply, foreign-script detection, post-save state verification and status publication. |
| Batch station identity | Installed interface/radio ownership, retain credentials, runtime verification and same-device reconnection guards. |
| Batch AP identity | Keep encryption/band settings, validate planned identities and actual runtime state, no guessed radio mapping. |
| Default browser page | Installed page allowlist, custom root skip, file-only transaction, original-content revision guard and temporary backup cleanup. |

## Source and compatibility verification

Fresh downloads checked 16 official source files. All twelve existing contract hashes matched: sysupgrade and network init for OpenWrt 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12. Four 25.12 target image definitions contain the expected model definitions: Xiaomi AX3600 (qualcommax/ipq807x), ASUS RT-ACRH17 (ipq40xx/generic), Redmi AX6S (mediatek/mt7622), JCG Q30 Pro (mediatek/filogic). URLs, downloaded hashes and model markers are in build/review/common-settings-full-audit-20261028/official-sources.json. Wiki pages returned a bot challenge during this audit; official repository definitions were used instead. No challenge was bypassed.

Primary reference examples:
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/target/linux/qualcommax/image/ipq807x.mk
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/target/linux/ipq40xx/image/generic.mk
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/target/linux/mediatek/image/mt7622.mk
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/target/linux/mediatek/image/filogic.mk

This review covers the configured mediatek/filogic, mediatek/mt7622, ramips/mt7621, ipq40xx/generic, ipq806x/generic, ipq807x/generic, qualcommax/ipq807x, ath79/generic, ath79/nand, bcm53xx/generic, bcm27xx/bcm2711, mvebu/cortexa9, mvebu/cortexa53, sunxi/cortexa53 and x86/64 scope at shared control-path level. Tests cover legacy/new network configuration and native old/new maintenance contracts; no new driver or router CPU binary is added. This does not certify each hardware/firmware combination.

Actual firmware must provide the needed tools. Policy/LAN management requires Lua, libuci-lua, libubus-lua, luci-lib-jsonc, UCI, ubus, root, sha256sum and native rpcd apply/rollback where used. Installed LuCI pages and web root are detected, not assumed. Backup/reset commands and storage layouts are capability-gated. Radio mapping, swconfig/DSA, firewall3/firewall4 and opkg/apk selection remain governed by their existing actual-data/capability paths. CPU-family agreement is not evidence of radio or storage correctness. JCG Q30 remains distinct and unconfirmed.

Exact dependency bundle coverage stays in docs/offline-packages/support.json. Version/platform pairs must not be inferred as a Cartesian product. Excluded package platforms ramips/mt76x8, rockchip/armv8 and x86/generic do not imply all basic management is unavailable. No bundle coverage or package-manager requirement was changed by these fixes.

Desktop limitation remains: the current Windows branch pins Qt/PySide6 6.3.2, whose Python requirement is below 3.11. Debian 12 defaults to 3.11; the separate runtime adaptation must be completed and tested before declaring Debian support. No Debian files were changed. Sources and metadata are in compatibility-audit-20261028.md.

## Verification and outstanding acceptance

426 tests passed in 105.747 seconds on the final code. The additional dialog regression batch passed 25 overlapping tests. Final test counts and logs are recorded in build/review/common-settings-full-audit-20261028/summary.json. Eight audit-touched Python files compile and decode as UTF-8; git diff --check passes. Local tests use project-local Qt 6.3.2. No real router or local adapter was modified.

Outstanding: actual Win11 Microsoft IME input; router-side LAN apply/force, reset/restore, relocation and wireless runtime tests on the intended devices; Debian 12 installation/start/core operation/shutdown. Source evidence and offline tests do not replace those checks. No executable was built, committed, pushed or published. The existing executable does not contain these fixes.
