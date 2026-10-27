# Common router settings

The Common Settings tab follows Router Login. It is created on demand; opening it does not query the router. Cards open separate editors for login password, LAN configuration, and hostname/timezone. Relocation and orphan-interface review retain their existing preview/confirmation workflows.

## Device behavior

- Password: administrator access and the system passwd command are required. The preflight distinguishes permission denial, missing passwd and probe failure; it does not require BusyBox applet listing. Prompts are read from both stdout and stderr. Two password lines travel through encrypted SSH stdin, never command arguments or application logs. A separate password-only login checks the pinned host fingerprint. The existing session stays connected. Confirmed changes update the current in-memory password and the user's existing Windows credential preference. An unconfirmed result is shown explicitly.
- LAN: reads and writes only the existing network.lan section. Offers Save and Apply (native rpcd apply with a 90-second rollback boundary, reconnect to the explicitly configured address with the original SSH host fingerprint, verify the saved values and runtime address, then confirm immediately) and Save Only (no network reload; apply on a later manual restart). The confirmation explains management-address changes and that DHCP address-pool settings are not edited. Existing legacy type/ifname bindings are preserved; changing that legacy binding requires the router web interface.
- Hostname/timezone: requires one unambiguous system section. Uses the same native checked-apply protocol and checks the runtime hostname and timezone file/link. This does not rename SSIDs.
- Timezone picker preserves the current rule and offers Beijing, UTC, and whole-hour fixed offsets. Fixed offsets do not implement daylight-saving transitions.
- Both configuration editors use an isolated rpcd UCI session, file-hash conflict detection, unique backups, readback verification, and conditional recovery. Pending edits in the target configuration block saving.
- Required configuration capabilities: administrator access, Lua, uci, ubus/rpcd, luci.jsonc and sha256sum. No automatic package installation occurs.

## Compatibility evidence

Official 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 branch sources were inspected for the common static-address options and system reload contract. The selected path depends on available capabilities and configuration shape, not a guessed version or interface number.

Primary source paths (replace BRANCH with openwrt-19.07, openwrt-21.02, openwrt-22.03, openwrt-23.05, openwrt-24.10 or openwrt-25.12):

- https://github.com/openwrt/openwrt/blob/BRANCH/package/base-files/files/etc/init.d/system
- https://github.com/openwrt/luci/blob/BRANCH/modules/luci-base/htdocs/luci-static/resources/protocol/static.js
- https://github.com/mirror/busybox/blob/master/loginutils/passwd.c
- https://github.com/mirror/busybox/blob/master/libbb/bb_askpass.c

Offline tests execute the real Lua transaction with simulated UCI/rpcd/filesystem data, including legacy ifname/type bindings and modern device bindings. Coverage includes preserving unrelated settings, stale reviews, backup/commit failures, missing wireless configuration, runtime reload failure, password transport and credential handoff, cancel, and connection switches. UI checks render light/dark themes at 100 and 150 percent.

These are source and offline checks, not live-router certification. Live password and configuration changes still require device verification.

## Backup, restore and factory reset

Two-column cards use matching color icons from the user's reference collection, already shipped under images/user_icons/icons_quick_settings. Wrapping and short buttons keep two columns usable in narrow windows. The common action helper clears focus before disabling a button, preventing focus from walking down through sibling cards and scrolling the page.

Export uses native sysupgrade -b, downloads to an owned local temporary directory, verifies SHA-256 and gzip/tar integrity, then publishes the local file. A damaged download never replaces an existing backup.

Restore previews up to 80 file names. The user confirms the backup belongs to this router and firmware and chooses a separate local safety-backup path. A verified current configuration backup must be saved there before sysupgrade -r starts. The inspected source is frozen before upload, and its hash and the device/configuration token are rechecked before writes. Pending UCI changes block maintenance.

Restore supports normal configuration files under etc and root, including safe service symlinks. Traversal, special files, duplicates, symlink write-through parents and links escaping that scope are rejected. Custom paths outside those trees require checking/restoring in the original management interface. Limits: 32 MiB compressed, 128 MiB decompressed, 8192 entries and 256 KiB of path names. Payload processing is chunked through temporary files.

Factory reset requires an observed squashfs/overlay configuration and an installed factoryreset or jffs2reset helper. A typed confirmation and verified local safety backup are required. Settings and later-installed packages are deleted. This does not install the manufacturer's stock firmware; configuration backups cannot restore all installed software or user files.

Once a destructive command starts, cancellation cannot interrupt it halfway. Bounded observation reports uncertainty explicitly without automatic retries. Restore/reset request reboot; the user reconnects using the resulting address and password. Uncertain restore workspaces are retained to avoid interrupting a command that may still be running.

The official 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 sources were checked for native -b/-r behavior. The native helper handles opkg/apk metadata differences. The 25.12 etc/uci-defaults service-state member is supported. Firstboot changed its helper from jffs2reset to factoryreset in 25.12; selection uses observed capabilities.

Primary references:

- https://github.com/openwrt/openwrt/blob/openwrt-19.07/package/base-files/files/sbin/sysupgrade
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/sbin/sysupgrade
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/lib/upgrade/tar.sh
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/sbin/firstboot
- https://github.com/openwrt/fstools/blob/master/factoryreset.c
- https://github.com/openwrt/luci/blob/openwrt-25.12/modules/luci-mod-system/htdocs/luci-static/resources/view/system/flash.js

Tests are offline archive/command-adapter checks and UI renders. No live router backup, restore, reset or reboot was executed.

## 重启与连接中断提醒（2026-10-24）

常用设置新增“重启路由器”卡片，使用已有 System Restart 图标。点击后先提示临时断网和重新登录，确认前取消或切换设备不会提交。运行时探测管理员权限、标准 reboot/sync 命令及官方 OpenWrt 身份，不依赖备份工具或按版本号猜测。

六个官方系列的重启按键源码均核对了标准 reboot 线路，固定提交、地址、哈希和源码见 tests/fixtures/official_reboot_contracts.json。实际设备仍须通过能力探测；衍生固件和全部硬件未作真机认证。

| 操作 | 连接影响与最终确认 |
| --- | --- |
| 重启 | 临时断网并断开登录，启动后重新登录；不清空配置 |
| 恢复配置、恢复出厂 | 重启并断线；管理地址、密码和 Wi-Fi 可能改变，最终确认正文明确说明 |
| 搬家清理 | 移除旧 Wi-Fi 并更换网络身份，可能断开登录和上网；预览后最终确认明确说明 |
| LAN 修改 | 可选保存并应用（确认后保存、校验并重启）或仅保存（之后手动重启）；需要记住新管理地址 |
| 登录密码 | 当前 SSH 通常保留；网页可能要求重新登录，下次登录使用新密码；提交前再确认 |
| 遗留接口清理 | 本次只保存，不重载；以后重载时所删接口不再可用，最终确认提示可能影响依赖连接 |
| 主机名与时区 | 使用 system reload，不重启网络，通常不影响登录 |
| 生成备份、只读检查 | 不修改网络，不主动断线 |

重启以及恢复/重置后的重启复用同一后台请求入口，命令观察最多 12 秒（打开 SSH 命令通道另有独立期限），不等待路由器开机，不自动重试。提交后取消不会中断正在发生的重启；请求结果不明确则按“未确认”提示。旧连接在工作线程中清理，设备切换后不关闭新连接。真实 Qt 工作线程回归验证：断线时主线程仍能处理事件，工作线程结束后释放任务占用。

恢复/重置的长提醒使用可滚动正文，高缩放不会把文字直接裁掉。证据见 build/router-reboot-review/。

## 遗留接口检查范围复核（2026-10-24）

已复现并修复：原检查漏掉不带数字的 wwan；空运行设备字段被当作有效设备绑定。明确的实际设备绑定、正在运行、待连接、引用或状态不完整仍会阻止清理。六系列 netifd 固定源码的 up/pending/device/l3_device 字段核对证据保存在 build/router-reboot-review/netifd/。

“没有可勾选项”不等于“没有遗留接口”。现有安全范围只允许清理带本软件归属记录、对应信号已不存在、无其他引用且未运行的接口。网页或旧脚本创建、没有归属记录的 wwan 可以展示原因，但不会仅凭名字开放删除。本次未连接用户设备检查真实配置，也未执行实际清理、重启、恢复或重置。


## Common settings extension audit

See [common-settings-security-audit.md](common-settings-security-audit.md) for new policy cards, shared progress, evidence and verification limits.

## 常用设置界面调整（2026-10-24）

- 主机名输入框右侧增加随机按钮，复用已有品牌/手机型号名称生成器。只修改弹窗输入，保存前不写路由器。
- 时区优先读取路由器安装的 LuCI 时区表；没有该表时使用随软件提供的官方 LuCI 25.12 地区表（445 项），保留对应夏令时规则和未识别的当前值。六系列均核对了官方 tzdata.lua；下载证据位于 build/common-settings-ui-refinement/sources/manifest.json。
- [官方时区表](https://github.com/openwrt/luci/blob/openwrt-25.12/modules/luci-lua-runtime/luasrc/sys/zoneinfo/tzdata.lua)，来源哈希保存在 router_timezones.py。
- 搬家详情改为 7 项实际操作清单，初始设置详情改为 17 项可选操作；初始化编辑页只显示选项和输入，不再逐项显示说明段落或恢复确认勾选。最终执行预览仍显示实际变更与连接影响。
- 开机自动连接上级 Wi-Fi 不再显示详情按钮；遗留接口检查移至最后。三个卡片一行、按钮底部对齐保持不变。
- 本次定向回归 31 项通过，包含设备时区读取、夏令时、未知时区保留、随机输入不直接保存、取消及明暗主题/缩放。未连接真实路由器改配置。


## Initial confirmation and editable AP batch identities (2026-10-25)

The initial policy editor starts with an unchecked confirmation that the router is in the initial state of its current OpenWrt firmware. Continue and programmatic acceptance are blocked until checked. Each new dialog requires a fresh confirmation; this is a user declaration, not an automatic factory-state detector or reset operation.

The AP batch workflow now opens an editor after reading the current device. Every saved AP (including disabled APs) is listed by its old SSID and section, with editable new SSID and MAC. Each row has a random action for both values; the global action regenerates every row. Generation is local, bounded, and avoids known configured/device MAC addresses and generated name collisions. Manually identical SSIDs remain allowed for intentional roaming configurations. Input validation rejects invalid unicast MACs, conflicting addresses, control characters, SSIDs outside 1-32 UTF-8 bytes, and missing/duplicate/foreign sections. Preview rebuilds the plan from the freshly read configuration; existing connection checks and transaction hashes still guard execution. Passwords, frequency, network links, and enabled states remain unchanged.

Validation: 53 existing policy/identity tests and 9 new editor/transaction tests passed. Both themes at 100% and 150% were rendered and inspected. New/legacy UCI fixture transactions preserved unrelated fields. Official mac80211 scripts from 19.07.10, 21.02.1, 22.03.7, 23.05.5, 24.10.0 and 25.12.0 were fetched with normal TLS verification and checked for the existing ssid/macaddr options; source URLs and hashes are recorded under build/ap-batch-editor-20261025/official-wireless-contracts.json. The public option reference is https://openwrt.org/docs/guide-user/network/wifi/basic . No new router-side command or dependency was introduced. Driver acceptance of a chosen AP MAC still requires live-device verification; local tests do not certify hardware behavior.


## Password and message routing review (2026-10-26)

Common Settings reuses InfoPanel and InfoPanelDock to the right of the cards, with the same default width ratio as Router Login. Cards reflow in place between one and three columns on viewport resize, avoiding horizontal clipping without timers or reconstructing widgets. The parent owns the bounded log, theme/font synchronization and diagnostic export. Status messages from configuration and maintenance controllers go through the page message entry point. Password failures are logged here and shown with MessageDialog; keyed common-settings failures no longer enter the Router Login log. Constructors still perform no remote work.

Root cause: the previous preflight used `busybox --list`, conflating a disabled BusyBox listing applet with a missing password tool or insufficient privilege. Official OpenWrt defaults disable BUSYBOX_DEFAULT_BUSYBOX while retaining individual applets. See [23.05 defaults](https://github.com/openwrt/openwrt/blob/openwrt-23.05/package/utils/busybox/Config-defaults.in) and [BusyBox configuration explanation](https://github.com/openwrt/openwrt/blob/main/package/utils/busybox/config/Config.in). The same listing default was checked in official 19.07, 21.02, 22.03, 24.10 and 25.12 branch defaults. [LuCI 19.07](https://github.com/openwrt/luci/blob/openwrt-19.07/modules/luci-base/luasrc/sys.lua) invokes passwd; [rpcd sys](https://github.com/openwrt/rpcd/blob/master/sys.c) invokes /bin/passwd. Neither requires successful BusyBox listing.

Compatibility: selection is by actual UID and passwd availability, independent of router CPU, radio driver or network layout. No board-specific path was added. The supplied TL-XDR6088 / 23.05.5 log records root login, but its bounded operation history no longer includes the failed password preflight. Its exact remote exit reason remains unverified. Offline tests cover tool/permission distinction, both prompt streams, fragmented prompts, secret cleanup, pinned verification, current-page error ownership and both theme renders. A real router password change and Debian 12 desktop run remain pending. On device, change once with the existing root session, confirm the new-password verification result, retain the new password, and check a fresh login; do not repeatedly submit an uncertain result.


## Input focus and absent LAN options (2026-10-26)

Password-dialog Tab order visits the new and confirmation edits consecutively, then both visibility buttons and the action buttons. Shift+Tab traverses the same chain backwards; visibility buttons retain keyboard access. This is local Qt form navigation and does not alter router commands or capabilities. See [QWidget tab order](https://doc.qt.io/qt-6/qwidget.html#setTabOrder).

LAN staging now consults the freshly validated current settings before deleting an empty optional field. A missing field already has the requested state, so no delete RPC is issued. Existing values are still deleted, real RPC failures still abort the transaction, and commit verification is unchanged. [rpcd UCI deletion](https://github.com/openwrt/rpcd/blob/master/uci.c) returns NOT_FOUND when an option is absent; [ubus status definitions](https://github.com/openwrt/ubus/blob/master/ubusmsg.h) assign that status code 4. The Lua test device now reproduces that official behavior; the old staging code reproduced the customer's exact rpc_uci_delete_4 error before the fix. This is independent of network device naming, radio driver and CPU; it applies to the common transaction used for supported official OpenWrt versions. No network reload or real router change was performed during tests; real-device and Debian desktop verification remain pending.

The reported Windows 11 sidebar button background was not reproduced by either the local offscreen renderer or the local Windows native backend with WindowsVista style. No icon/stylesheet change is justified by those results alone; the affected run's version and source/EXE identity need comparison.

## Configuration application feedback

All relevant network/wireless application tasks publish real worker phases to the existing themed task dialog. It displays the phase maximum remaining time and a no-power-off notice. A display-only clock updates the text; task completion closes the dialog immediately, including early completion. Expiry never reports success. Recovery has its own bounded phase, and uncertain outcomes remain explicit. No task sleeps merely to finish a countdown. Dialog timers stop on completion and are reclaimed with the dialog.

LAN, hostname/timezone and immediate initialization/station/AP policy changes use native apply/confirm/rollback. New initialization APs are confirmed before default APs are removed. Ordinary wireless enable/disable and runtime repair retain their intended direct-application semantics, with visible verification progress; intentional disconnects are not forced into automatic rollback. Wireless configuration add/edit/delete, bridge operations and relocation retain their existing native rollback transactions, now with the same visible phases. Boot-only policies, orphan cleanup and Save Only stay save-only. Password changes, scans, reads, backup and explicit reboot/reset/restore do not become network apply operations. Fixed-file and service changes are separate checked phases, outside rpcd UCI rollback.

Native rollback protects unconfirmed configuration changes while the router is running; it is not a power-loss guarantee. A lost apply response is reconciled using the same session rather than resubmitting the write. Recovery verifies original configuration hashes; LAN also verifies the old runtime state. Policy recovery checks previously active wireless interfaces. Concurrent changes observed during wireless runtime verification halt subsequent policy steps without overwriting the external edit.

Official implementation references:

- https://github.com/openwrt/rpcd/blob/master/uci.c
- https://github.com/openwrt/luci/blob/openwrt-23.05/modules/luci-base/htdocs/luci-static/resources/ui.js
- https://github.com/openwrt/luci/blob/openwrt-23.05/modules/luci-base/htdocs/luci-static/resources/luci.js
- https://github.com/openwrt/luci/blob/openwrt-23.05/modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js

Offline Windows regression coverage includes real Lua apply/confirm/rollback, lost replies, permission rejection, stale reviews, cancellation before writes, backup-before-write, initialization AP ordering, concurrent edits, GUI responsiveness, early completion and countdown expiry. Real-router behavior and Debian runtime remain pending device testing.

## Two-pass application audit (2026-10-26)

Pass one compared the actual rpcd revision pinned by each supported OpenWrt branch (19.07, 21.02, 22.03, 23.05, 24.10, 25.12), not only current master. All six expose native apply/confirm/rollback. Exact revisions and URLs are retained in `tests/fixtures/official_checked_apply_contracts.json`. Official NO_DATA results for unchanged apply and inactive confirmation are now treated separately from permission or transport failures. A lost confirmation is reconciled by checking retained values and runtime; the apply write is not repeated. Original hashes are checked in the same Lua command immediately before apply. Verification that overruns the deadline cannot send a late confirm.

Pass two inspected execution and failure paths independently, and injected stale runtime state into real Lua. The following gaps were found and fixed:

- Reconnect parameters came from editable login controls; now they come from the successful authenticated SSH connection, with no password retained in the SSH client. A fresh transport is required after application; a still-open old socket is not proof of reachability. A distinct literal WAN management address remains an explicit candidate, while the removed old LAN address is not reused as a shortcut.
- Saving values and loading them into netifd are separate operations. Network/wireless policy changes explicitly complete the idempotent network load request before checking runtime. Hostname/timezone changes do not force a network reload. Network-load failure remains an applied-write failure requiring recovery, not a pre-write rejection.
- Wireless checks now compare loaded netifd SSID, password, encryption, identity and radio settings before reporting ready. Same-SSID password changes and old loaded channels cannot pass merely because the old AP is running.
- LAN checks include the active protocol/device, IPv4 prefix length, requested gateways and IPv6 addresses/prefixes. Missing status is not evidence of a disabled interface. DHCP does not validate disabled static gateway fields. The none protocol is allowed to keep a bridge up without assigning IPv4; it is not equivalent to administratively stopping the bridge. Prefix lengths outside the native 1..64 IPv6 routed-prefix range are rejected before writing.
- Policy verification now carries LAN runtime state back through its public result, verifies timezone, and checks old address/hostname/timezone plus previously active/associated wireless state on recovery. Previously offline stations can have identity settings changed without forcing a connection the user did not request.

New failure reproductions are in `tests/test_configuration_apply_audit.py` and `tests/test_checked_apply.py`; authenticated connection snapshots are also exercised against a real local Paramiko SSH server with password, empty-password and selected-private-key routes. These local tests do not reproduce a specific OpenWrt device's Dropbear build or wireless driver.

The pinned netifd 19.07 through 24.10 sources confirm loaded-config and none-protocol fields. The pinned 25.12 netifd revision could not be retrieved from the GitHub mirror; official source downloads failed certificate verification and TLS checks were not bypassed. Its rpcd and service scripts were retrieved, but complete netifd source and real-device validation remain open. Windows offline/Qt checks are separate from Debian 12 desktop and real-router acceptance; no full hardware or platform compatibility certification is claimed.

Final audit validation: 321 related offline regressions passed after the combined fixes, followed by 69 focused checks after the optional-wpa-cli adjustment. All 39 changed/new Python sources compiled; the rg encoding scan and git diff whitespace check were clean. The optional-wpa-cli case checks loaded station configuration and actual MAC without requiring association when no previous association could be established; known previously associated stations still require reconnection. No router configuration was written during this audit.


## LAN client address recovery (2026-10-26)

Before applying a changed static LAN address, the review includes a read-only snapshot of the local interface used to reach the authenticated router. Windows uses Qt native interface enumeration, IP Helper index-to-GUID conversion and DHCP registry metadata; this avoids slow PowerShell network-provider startup. A known route takes priority over overlapping private subnets. Without a uniquely identified route, the user must explicitly select an eligible interface. A distinct WAN/VPN management route does not reset an unrelated LAN interface.

The existing apply/countdown dialog offers targeted DHCP recovery only after the remote apply has been sent. The user requests the operation explicitly; static configuration is never rewritten. Windows revalidates GUID, interface index, MAC, DHCP mode and expiry before the instance-specific RenewDHCPLease call. It does not pre-release the lease. Permission rejection may use the existing shared administrator host. Debian NetworkManager revalidates interface/profile identity and reactivates that exact profile and device without issuing device disconnect/down; unsupported managers receive manual guidance. There is no all-adapter reset or competing DHCP client.

Recovery runs in an owned background task. A concurrent recovery blocks configuration confirmation, and late worker completion cannot reopen a finished apply dialog. Command success alone does not finish application: the client address/prefix and router-based gateway/DNS must match, followed by the existing authenticated router/runtime verification. Static clients receive the target subnet, mask and conditional gateway/DNS instructions; rollback shows their original settings. A VM operation only changes the guest's own interface. Recovery can be retried after completion, while concurrent clicks remain blocked.

Validation includes adapter selection, manual settings, stale gateway/DNS, identity changes, expiry, background ownership, late completion, native Lua apply integration and light/dark 100/150-percent rendering. Windows interface inspection was exercised read-only on this machine. Actual DHCP release/renew, real-router LAN changes, UAC acceptance and Debian 12 runtime remain pending device acceptance; development tests do not disconnect the current network.

Official references: [Microsoft targeted DHCP operations](https://learn.microsoft.com/en-us/powershell/scripting/samples/performing-networking-tasks), [RenewDHCPLease](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/renewdhcplease-method-in-class-win32-networkadapterconfiguration), [NetworkManager nmcli](https://networkmanager.dev/docs/api/latest/nmcli.html).
