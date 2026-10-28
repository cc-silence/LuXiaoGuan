# Feedback compatibility audit, 2026-10-28

## Findings fixed

1. Fresh localhost proxy defaults could populate the unconfigured protocol in a sparse or legacy existing configuration. Normalization now distinguishes a fresh configuration from existing endpoint settings. Explicit blanks and existing custom hosts remain intact. Reproduced before fixing.
2. Relocation captured its original SSH session inside the queued worker. A new login between the click and worker start could become the adoption baseline. The service now captures the client and connection generation at click time; the manager rejects stale generations before opening its separate connection. Existing fingerprint and final adoption checks remain. Reproduced before fixing; tests cover both service capture and early manager rejection.
3. Queued adapter renewal did not honor cancellation or application shutdown. The worker now checks both before invoking platform code; its permit callback also checks both immediately before the native operation. Running native calls retain their existing bounded timeout. Both queued cancellation and shutdown reproduced before fixing.

## Verification

325 test executions passed in two non-overlapping selected-module batches (269 + 56), using Python 3.10 and project-local Qt/PySide6 6.3.2. Logs and machine-readable summary are in build/review/compatibility-audit-20261028*. The five audit-touched Python files compile and decode as UTF-8. No interface visuals were changed in this audit.

Coverage includes optional restore backup, frozen source validation, native backup/reset version contracts, independent homepage changes, proxy migration and direct fallback, pinned relocation/recovery, LAN force apply, cancellation, login prompts, diagnostics export and synthetic input events. This is targeted offline verification, not a full-repository or real-device certification.

## Systems and platform scope

| Scope | Evidence and limit |
| --- | --- |
| OpenWrt 19.07 / 21.02 / 22.03 / 23.05 / 24.10 / 25.12 | Official policy and maintenance contracts for all six series are recorded in tests/fixtures/official_policy_contracts.json and official_maintenance_contracts.json; these contract tests passed. Actual installed UCI/rpcd rollback, tools, web root, pages and storage capabilities still control availability. Version support does not imply every model existed in every series. |
| Router homepage | Official LuCI root redirect files were checked for all six series in the preceding batch; source URLs/hashes are in build/review/feedback-7-10-20261028/summary.json. The selected installed page only changes the approved /www/index.html file. Custom web roots are skipped. Browser cache and real firmware acceptance remain pending. |
| Windows / Win11 | Runtime pin and synthetic uppercase/IME commit/deletion tests passed. Qt's upstream IME fix is documented in docs/remaining-feedback-20261028.md. The user's Win11 Microsoft IME issue has not been reproduced and accepted on Win11 here. |
| Debian 12 | NOT accepted. PySide6 6.3.2 requires Python below 3.11; Debian 12 defaults to Python 3.11. A separate runtime adaptation is needed; the Windows pin must not be applied to Debian 12 unchanged. No Debian branch files were modified by this audit. |

Official sources: [Qt PySide6 package metadata](https://pypi.org/pypi/PySide6/6.3.2/json), [Debian 12 release notes](https://www.debian.org/releases/bookworm/amd64/release-notes/ch-whats-new.en.html), [LuCI 25.12 root index](https://raw.githubusercontent.com/openwrt/luci/openwrt-25.12/modules/luci-base/root/www/index.html).

## Hardware and dependency scope

| Model | Official reference / boundary |
| --- | --- |
| Xiaomi AX3600 | [Official device page](https://openwrt.org/toh/xiaomi/ax3600), Qualcomm IPQ8071A. The audit adds no radio/ath11k driver behavior; actual network configuration and rpcd capabilities still need device acceptance. |
| ASUS RT-ACRH17 | [Official ipq40xx target list](https://openwrt.org/docs/techref/targets/ipq40xx). Old and new network formats are covered offline; no real-device acceptance here. |
| Redmi AX6S | [Official hardware data](https://openwrt.org/toh/hwdata/xiaomi/xiaomi_ax6s), mediatek/mt7622. CPU family does not select the homepage/backup path; firmware capabilities do. No real-device acceptance here. |
| JCG Q30 Pro | [Official mediatek target list](https://openwrt.org/docs/techref/targets/mediatek), filogic. Kept separate from JCG Q30, whose actual firmware remains unconfirmed. No real-device acceptance here. |

Platform review covers the existing mediatek/filogic, mediatek/mt7622, ramips/mt7621, ipq40xx/generic, ipq806x/generic, ipq807x/generic, qualcommax/ipq807x, ath79/generic, ath79/nand, bcm53xx/generic, bcm27xx/bcm2711, mvebu/cortexa9, mvebu/cortexa53, sunxi/cortexa53 and x86/64 scope. This audit changes shared Python task guards and HTTP settings; it adds no compiled router binary, radio mapping, kernel module or driver replacement. Existing swconfig/DSA and storage guards remain the execution boundary, not a model-name assumption.

Offline package coverage remains EXACTLY the version + target/subtarget + architecture entries in docs/offline-packages/support.json, including unavailable profiles/dependencies. It is not a Cartesian product of the series and platforms above. Listed package exclusions include ramips/mt76x8, rockchip/armv8 and x86/generic; package exclusion does not mean all SSH/basic management is unsupported. Proxy-to-direct fallback changes the download route only; signature, hash and package identity validation remain mandatory. Installing an already-downloaded bundle uses SSH and its existing package-manager capability path.

## Delivery boundary

Source fixes and targeted offline tests are complete. Real router LAN/apply/reset/restore/relocation checks, actual Win11 IME acceptance, and Debian 12 runtime acceptance are outstanding. No router or real adapter was modified, no package was built, and no commit/push/publication was performed. Existing executable files do not contain these source changes.
