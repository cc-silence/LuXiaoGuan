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


## October 28 feedback revision

- `NetworkRecoveryResultDialog` now owns the shared result-window lifecycle. `FactoryResetResultDialog` supplies reset-specific guidance; LAN apply uses the same component with the reviewed old/new addresses.
- LAN progress remains disabled before native apply returns. The post-task result opens a fresh recovery offer instead of reusing the worker gate, which has already been revoked. The result remains open until the user closes it. Rollback offers validate the original subnet; forced retry carries the original recovery plan forward.
- Factory-reset renewal is initially disabled. The user confirms that reboot and the wired/Wi-Fi connection have completed before enabling it. Reset command completion alone cannot prove boot completion, especially while the client still has its old address. This is an explicit readiness fallback, not automatic device verification.
- Windows now releases and renews only the revalidated DHCP adapter. The shared facade reads the resulting address and rejects missing/link-local addresses and, when LAN target information is available, addresses outside the expected subnet. The result panel shows the address actually read back. No real adapter is disabled or enabled by the verification tests.
- Microsoft reference: https://learn.microsoft.com/en-us/powershell/scripting/samples/performing-networking-tasks (specific-adapter lease release and renewal).
- OpenWrt reference: https://openwrt.org/docs/techref/uci (rpcd session-specific apply/confirm/rollback). Existing capabilities and native rollback remain required; no version, board, CPU or driver heuristic was introduced. The station identity operation changes only reviewed DHCP hostnames and station MACs, retains Wi-Fi credentials, removes legacy identity recovery metadata on the selected stations, and offers a complete configuration backup before apply. It does not create a new identity restoration ledger.
- Regression evidence and password screenshots are under `build/feedback-1028-review/`. Tests use actual embedded Lua and Qt offscreen drawing. Real-router DHCP refresh/reset, Windows adapter execution, and Debian 12 runtime remain to be checked on their respective systems. This revision adds no router package dependency.


## Post-change compatibility and defect audit (2026-10-28)

Scope: password mismatch feedback, direct default-page save, LAN/reset client-address recovery, and common-settings station identity replacement. Existing unrelated terminal/update/package changes were not modified by this audit.

### Defects corrected during this audit

1. Successful client renewal previously replaced the stored original adapter row, so rollback guidance could describe the new address/gateway as the original configuration. `RenewalGate.plan` is now the immutable planning baseline; `RenewalGate.adapters` holds only the current display/identity row.
2. Readback previously accepted a matching GUID/MAC even when the adapter was disconnected or had changed to manual addressing during the operation. Readback now also requires connected DHCP configuration and a usable non-multicast IPv4 address, plus the reviewed target subnet when known.
3. A reset result opened for longer than ten minutes could remain permanently expired after a long reboot. Explicit readiness confirmation now begins a new bounded ten-minute recovery window; it never starts a network operation automatically.
4. Microsoft documents return value 1 as completion requiring reboot and 64 as unsupported. The script now still requests renewal after a successful release requiring reboot, reports the reboot requirement explicitly, and identifies unsupported methods separately. Actual PowerShell tests replace both CIM functions with fakes, so these branches execute without touching a real adapter.
5. A uhttpd custom index order can bypass index.html entirely. The snapshot now reads index_page; a conflicting order is reported before writing files. The equivalent document-root spelling /www/ is accepted. With the preview popup removed, unsupported-page reasons are now displayed directly instead of submitting an empty apply.

### Router protocol evidence

The exact fixed rpcd source commit was obtained from each supported series branch's official Makefile. The pinned uci.c sources were fetched and checked for session-specific apply, confirm, rollback and config.change. These are protocol-source checks, not per-board runtime tests.

| Series | Pinned rpcd commit prefix | Source result |
| --- | --- | --- |
| 19.07 | 67c8a3fda26e | apply / confirm / rollback / session present |
| 21.02 | 8d26a1ba3a17 | apply / confirm / rollback / session present |
| 22.03 | 7de4820c8743 | apply / confirm / rollback / session present |
| 23.05 | c07ab2f91061 | apply / confirm / rollback / session present |
| 24.10 | bba95191ff2f | apply / confirm / rollback / session present |
| 25.12 | d99f703e4035 | apply / confirm / rollback / session present |

All six series' packaged DHCP scripts accept hostname and pass it to udhcpc. The old mac80211 implementation (19.07/21.02/22.03/23.05) and the moved wifi-scripts implementation (24.10/25.12) accept the per-interface macaddr option. The change continues to validate the associated DHCP interface, MAC uniqueness, original device fingerprint and applicable runtime MAC/association before confirming. It does not introduce opkg/apk operations, switch/DSA rewrites, board-number assumptions, radio mappings or additional kernel modules.

Required components remain root management access, the existing Lua/JSON/UCI/ubus helpers, native rpcd UCI transaction methods, and driver support for a station MAC override. Missing capabilities are rejected by the existing probes. Wi-Fi passwords, DHCP client IDs, firewall zones and LAN settings are not changed by station identity replacement. A driver which ignores the MAC override cannot be certified solely from a config write; runtime verification remains required for an active station.

The version scope follows the exact releases in docs/offline-packages/support.json; source checks above are by series and pinned branch component revisions. This does not claim that every supported point release has been booted or tested here.

### Hardware scope

The full supported platform scope considered is: ath79/generic, ath79/nand, bcm27xx/bcm2711, bcm53xx/generic, ipq40xx/generic, ipq806x/generic, ipq807x/generic, mediatek/filogic, mediatek/mt7622, mvebu/cortexa53, mvebu/cortexa9, qualcommax/ipq807x, ramips/mt7621, sunxi/cortexa53, x86/64. These changes use common UCI/netifd/desktop APIs; there is no new CPU instruction or board-specific network device dependency. Platform package coverage is separate from model runtime verification.

| Model | Official source / image evidence | Limit |
| --- | --- | --- |
| Xiaomi AX3600 | 23.05.6 ipq807x/generic profiles.json contains xiaomi_ax3600; 24.10 qualcommax/ipq807x image definition uses this model | Do not infer 19.07/21.02/22.03 support; no device exercised |
| ASUS RT-ACRH17 | 22.03.7 ipq40xx/generic profiles.json lists it as an alternate title of asus_rt-ac42u; absent from the checked 19.07.10 and 21.02.7 profiles | Use the official alias; no device exercised |
| Redmi AX6S | 22.03.7 mediatek/mt7622 profiles.json contains xiaomi_redmi-router-ax6s; current image definition identifies MT7622 | No device exercised |
| JCG Q30 Pro | 23.05.6 and 24.10.5 mediatek/filogic profiles.json contain jcg_q30-pro; image definition identifies MT7981B | No device exercised |
| JCG Q30 | Existing support record says actual firmware unconfirmed | Never substitute Q30 Pro evidence |

Representative primary sources:
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-19.07/package/system/rpcd/Makefile
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/system/rpcd/Makefile
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-19.07/package/network/config/netifd/files/lib/netifd/proto/dhcp.sh
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/network/config/netifd/files/lib/netifd/proto/dhcp.sh
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-24.10/target/linux/ipq40xx/image/generic.mk
- https://downloads.openwrt.org/releases/23.05.6/targets/ipq807x/generic/profiles.json
- https://downloads.openwrt.org/releases/22.03.7/targets/mediatek/mt7622/profiles.json
- https://downloads.openwrt.org/releases/23.05.6/targets/mediatek/filogic/profiles.json
- https://raw.githubusercontent.com/openwrt/uhttpd/master/main.c

Every successful fetch's exact URL and SHA-256 is retained in build/feedback-1028-audit/official-sources.json; raw sources are alongside it. Unavailable initial source-path guesses are retained as failed lookups, with the correct fetched locations recorded separately.

### Desktop-platform evidence and outstanding checks

Windows: actual QNetworkInterface/IP Helper/registry inventory on this host correctly identified Ethernet0 as connected and DHCP-enabled, and distinguished the disconnected VPN adapter and loopback. No real lease was released/renewed. The fake-CIM PowerShell tests executed success, reboot-required, permission denial, unsupported and release failure paths. Microsoft references:
- https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/releasedhcplease-method-in-class-win32-networkadapterconfiguration
- https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/renewdhcplease-method-in-class-win32-networkadapterconfiguration

Debian 12 x86-64: the Bookworm NetworkManager manual and the nm-1-42 manager source were checked for UUID/ifname activation and bounded wait. The same explicit profile is reactivated; no competing DHCP client, global networking-off command, interface disable or autoconnect rewrite is introduced. Missing/unmanaged NetworkManager configurations remain unavailable rather than reporting fake success. Sources:
- https://manpages.debian.org/bookworm/network-manager/nmcli.1.en.html
- https://raw.githubusercontent.com/NetworkManager/NetworkManager/nm-1-42/src/core/nm-manager.c

No usable Debian runtime was established on this host; dependency installation, real import/startup, desktop rendering, activation and exit cleanup on Debian 12 remain unverified. Linux branch mocks and Windows Qt offscreen screenshots are not Debian runtime verification.

Remaining acceptance checks: use a real authorized router to change LAN subnet, wait for native apply, request renewal on the selected client adapter and confirm a target-subnet lease; repeat through forced apply and reset. Exercise the explicit reboot-readiness fallback before reset renewal. Confirm name/MAC replacement on each available driver and preserve the pre-change full backup for manual restore. Hardware/firmware-specific failures cannot be excluded by the offline suite.


## Second independent review (2026-10-28)

The review traced declined/accepted force retry, router changes while a prompt is open, window closure while a lease operation remains in progress, worker-start failure and adapter-list refill.

- Declining force retry after a verified rollback now opens the independent recovery result for the original subnet. Accepting force carries the recovery plan forward and does not open an extra result window. Switching routers during the prompt prevents an old recovery offer from opening.
- The common broker serializes lease operations per adapter identity across all offers. Closing a window revokes its permission but does not clear the adapter job until its worker completion callback. Older duplicate callbacks cannot unlock a newer job. Different adapters remain independent, and start failure releases both the task gate and adapter lock.
- The shared panel preserves a selected adapter when address rows are rebuilt and disables renewal for an adapter already being processed by another offer. Completion notifications update the affected offers directly; no timer, delayed workaround or repeated polling was added.
- Added tests/test_feedback_1028_recheck.py. The initial focused 54-case lifecycle/UI regression passed. Full integration results are recorded in build/feedback-1028-audit/recheck-summary.json.
- These are shared task/UI changes; they introduce no new Windows, Debian, OpenWrt-version or driver API. The previously recorded real-router/real-adapter/Debian runtime verification limits remain.


## Three requested independent passes (2026-10-28)

No production code changes were needed in these passes. Added 17 regression cases in tests/test_feedback_1028_three_passes.py. Each pass completed before starting the next; selected existing tests were included in each pass, so the counts below are not distinct-test totals.

| Pass | Scope | Result |
| --- | --- | --- |
| 1 | Mismatch remains local; editing either password clears feedback; invalid matching secret is rejected; Escape clears secrets and restores hidden echo; declining password confirmation keeps editable entries; no available homepage cannot be saved; reset readiness can be revoked and Escape releases its offer | 23 tests passed |
| 2 | Real Qt worker failure followed by retry; closed offer revokes in-flight permission; another offer cannot mutate the same adapter before completion; shutdown revokes permission and rejects new work; synchronous start exception unlocks state; 128 create/release cycles leave no retained offers and the offer cap remains enforced | 23 tests passed |
| 3 | Backup failure stops station/homepage writes; configuration changes during backup are preserved; cancel after backup stops the transaction; a simulated driver ignoring the new MAC is not confirmed and the real embedded Lua rollback restores configuration; a different adapter, the router's own address or link-local address is rejected; expired Linux permission does not activate a profile | 20 tests passed |

Results are retained in build/feedback-1028-audit/three-pass-summary.json. No new defect was reproduced in these three passes. No real router or adapter was mutated, and Debian 12 runtime/desktop validation remains outstanding. The results do not establish universal hardware correctness or replace the previously listed live acceptance checks.


## Follow-up: transaction must finish before local renewal (2026-10-28)

The supplied screenshot exposed the old acceptance/completion confusion: `checked_apply` returning only means rpcd accepted the apply request. The reconnect phase previously armed client renewal too early. The shared transaction now publishes a locked gate during application and verification; starting rollback also revokes permission. Only a separate completed-result offer can enable renewal. Reset still requires explicit post-reboot readiness confirmation. No timers or router commands were added.

Official source rechecked: https://raw.githubusercontent.com/openwrt/rpcd/bba95191ff2f22c9118a1ba1355b83afaa277ae3/uci.c . This changes desktop permission timing only and preserves the previously audited OpenWrt 19.07/21.02/22.03/23.05/24.10/25.12 apply/confirm/rollback command paths across the recorded targets and models; it adds no hardware-specific assumptions. Windows and Debian share the gate and login UI. Real-router, real-adapter and Debian runtime validation remain pending.

Both login dependency sections now explicitly request a collapsed initial state from the common card constructor; unrelated cards retain their default. Validation: 67 focused tests passed, including new success/lost-reply/rollback transaction assertions, permission revocation, result/reset recovery, and actual light/dark login rendering. Evidence: build/feedback-1028-readiness/login-False.png and login-True.png. Terminal excluded at the user's request.
