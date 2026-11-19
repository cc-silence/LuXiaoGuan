# 抓包验收矩阵

更新日期：2026-11-14。本轮范围：无效信道、SSH 通道失败、扫描进程停止与恢复。不代表全部抓包功能已验收。

## 使用方法

修改前选出受影响的条目，修改后执行对应检查并记录证据。新问题先记录预期行为，不必等修好才写。离线通过、实机通过、失败和未验证分开标记；实机结果必须记录软件版本、系统小版本、型号、频段、连接方式和日志。不以模拟结果代替实机结果。

## 本轮检查表

| 编号 | 检查情况 | 必须满足 | 检查依据 |
| --- | --- | --- | --- |
| CAP-01 | 混合勾选有效信道和 -1/0 | 无效目标显示信道错误并禁止勾选（含全选、反选、拖动勾选），其他目标继续；预检与启动使用同一规则 | tests/test_handshake_invalid_channels.py |
| CAP-02 | 全部信道无效 | 不创建远端任务；重扫获得有效信道后状态恢复 | tests/test_handshake_invalid_channels.py |
| CAP-03 | 命令通道打开超时 | 仅做一次有时限的只读确认；有回应则保留原登录，不重放原操作 | tests/test_ssh_channel_isolation.py; tests/test_ssh_liveness.py |
| CAP-04 | 确认命令无回应或错误回应 | 不假报连接正常；回收本次通道，保留首错 | tests/test_ssh_liveness.py; tests/test_ssh_channel_isolation.py |
| CAP-05 | 停止命令失败但进程已退出 | 独立核实本任务进程编号与启动标记；确认退出才允许后续恢复 | tests/test_process_stop_verification.py |
| CAP-06 | 进程仍存在或身份不明 | 保留恢复凭据；不误杀复用进程编号的其他程序；读取失败不等于已退出 | tests/test_process_stop_verification.py; tests/test_process_recovery_boundaries.py |
| CAP-07 | 上次清理未完成又点抓包 | 预检前阻止；提示断开后重登同一路由器，清理成功后再扫描；失败则导出日志 | tests/test_process_stop_verification.py; tests/test_initial_reconnect.py |
| CAP-08 | 取消、扫描和连续抓包 | 保留已有取消、所有权、射频恢复及已有可用包跳过行为 | tests/test_scan_takeover.py; tests/test_handshake_rounds.py; tests/test_capture_monitor_cleanup.py |

本轮离线结果和源码哈希见 [验证记录](capture-feedback-validation-20261114.json)。以该文件的 success、failures、errors、skips 为准，不把跳过算通过。CAP-01 至 CAP-08 的真路由器验收均待验证。

## 旧系统保护与实机复测

- OpenWrt 19.07、21.02、22.03、23.05：保留原正常扫描、抓包和停止命令；不替换依赖或无线组件。用户反馈旧系统已可用，修改后实机回归仍待验证。
- OpenWrt 24.10、25.12：不因版本号猜测驱动故障或全面兼容；精确型号和小版本实机待验证。硬件范围继承 AGENTS.md 和 offline-packages/support.json，不从一台设备推广到其他型号。
- 在已授权网络中，用有线管理连接先关闭主动断连；分别扫描 5G 与 2.4G，完成后核实工具进程退出、临时监听接口删除、被接管无线恢复。再测试抓包与手动停止。
- 主动断连单独验收，不与被动结果混用。若再超时，导出新日志，核对 verify_process_stop 进程状态、ssh_liveness 连接确认、启动标识和内核日志。不把未回应说成已确认重启。
- Windows 离线与 Qt 离屏检查不等于 Debian 12 桌面实机通过；Debian 12 导入、启动、相关功能和退出清理待验证。

## 两份反馈的已知边界

111652 日志：5G 含 -1 目标导致整批拒绝；2.4G 主动断连超时后清理通道打开超时。114546 日志：被动 2.4G 扫描停止超时后 SSH 仍可读取设备，残留监听接口导致后续预检拒绝。较早一次重登启动标识相同，该次未重启。停止超时的底层原因仍未锁定，本轮不宣称已治愈驱动或路由器故障。


2026-11-14 追加验证：CAP-01/02 禁止勾选及有效信道刷新恢复，20 项定向检查通过。见 [追加记录](capture-selection-validation-20261114.json)；此记录更新表格源码哈希，不覆盖上一轮证据。


## 2026-11-14 实机故障 CAP-09

小米 3G / OpenWrt 25.12.6 / mt7603 39c960c3：用户确认主动断连会自行重启，关闭后不重启。状态：实机失败，根因未锁定。已通过原设备指纹核对并只读获取实际版本；未改配置、未装驱动、未触发复现。详见 [实机核实记录](mi3g-active-capture-investigation-20261114.json)。需要崩溃时内核记录区分驱动异常、看门狗与其他复位；不得将类似芯片的上游报告当作本机已证实的缺陷。


## 2026-11-14 CAP-10

旧接管恢复未确认时，新任务必须在创建任务目录前拦截；不得用新任务目录恢复其他任务的凭据。无线配置发生外部变更时保留恢复记录，不覆盖未确认的用户设置。相关恢归 68 项通过；实机恢复待用户确认并验证。

CAP-09 后续：用户授权后安装了匹配当前内核的临时诊断模块；第一次复现发生在采集结束后，未获得崩溃堆栈。后续因无线恢复冲突无法再次运行任务，内核故障原因仍未确认。


## 2026-11-14 pixiewps 依赖补充

离线回归 102 项通过；原抓包包中 1930 个固件组合均加入 pixiewps，并保留原有文件。官方索引和文件校验完成；三方固件、未知 SNAPSHOT 及真机运行尚未验证。详见 [验证记录](pixiewps-dependency-validation-20261114.json)。


## 2026-11-14 CAP-11

正常收尾时先核实本任务进程和临时接口已清理，再尝试恢复接管前信号。配置已变更或信号恢复失败时保留当前设置、提示用户可手动开启，不因信号恢复未成功阻止下次任务。连接中断、资源所属设备未核实或临时资源未确认清理时仍保留记录。实机复测待完成；同时保留旧版系统原有的正常恢复流程，旧版实机复测待完成。


## 2026-11-14 秒 Pin 系统 WPS 发送方式

秒 Pin 设置新增“发送方式”开关，默认仍是 reaver 裸帧注入；选择系统 WPS 时改用路由器自带 wpa_supplicant 完成 WPS 注册。系统 WPS 不可用时不做自动回退，直接提示改回 reaver。高级 Pin 不受该开关影响。

- 必需满足：默认值不变；选项持久化并只接受 reaver/system；“系统 WPS 用不了”时必须说清原因且不静默切换路径；临时接口、专属进程和临时配置在本目标结束时清理。
- 检查依据：tests/test_wps_system_sender.py、tests/test_wps_configuration_flow.py、tests/test_wps_quick_and_radio.py、tests/test_wps_lifecycle.py；离屏截图核对秒 Pin 设置与高级 Pin 工具（亮/暗、置灰状态）。
- 状态：离线通过；真机对比两种发送方式待验证，未验证前不宣称系统 WPS 可用。精简版 wpad 不含 WPS 已按 19.07、21.02、23.05、24.10 官方包核实，22.03 未逐包核对，实际以运行期探测为准。详见 [验证记录](wps-system-sender-20261114.json)。

## 2026-11-15 系统 WPS 实测修复与 wpad 升级入口

真机实测暴露两类问题：一是 wpa_supplicant 配置里的 `update_config=1` 在 25.12 精简版 wpad 上直接报 `unknown global field` 并退出；二是 reaver 注入在 MT7603E 上导致整机无线/SSH 掉线。本轮按实测结果修正系统 WPS 客户端，并把“精简版换完整版 wpad”做成显式、可回滚的独立流程。

- 系统 WPS 客户端：去掉 `update_config=1`；启动参数加 `-K`，从调试日志的 `WPS: Network Key` 读取密码；精简版失败原因写明“换完整版 wpad 或改回 reaver”；逐目标失败原因进入主日志。
- 开始前探测：登录时只读判断无线组件是否带 `WPS_REG`，分为完整版/精简版/缺失；秒 Pin 选系统 WPS 且为精简版时，开始前弹窗让用户改回 reaver、仍试一次或取消。
- 离线 Pixie：系统 WPS 模式下默认关闭；仅在选择系统 WPS 时显示勾选项，勾选后才跑 reaver 的离线 Pixie，算出的 PIN 仍用系统 WPS 验证。reaver 模式行为不变。
- wpad 升级：抓包界面独立入口，仅在探测到精简版时显示。两步确认（说明 + 风险）后执行：有线管理检查 → 官方源定位完整包与当前精简包 → 电脑侧下载两包并校验 SHA256 → 上传并复校 → 备份 `/etc/config/wireless` → 卸载精简包 → 本地安装完整包 → 验证 `WPS_REG`；失败自动装回精简包并恢复配置，回滚失败时保留临时包并明确提示。
- 必需满足：不得自动替换无线组件；无网线管理、源不可达、空间不足、找不到同版本回滚包时直接拒绝；不升级其他包、不改软件源、不动引导/内核/固件。
- 检查依据：tests/test_wps_system_sender.py（16 项）、tests/test_wps_start_controller.py（13 项）、tests/test_wpad_upgrade.py（7 项，假命令执行器）、tests/test_wps_configuration_flow.py、tests/test_capture_tool_cards.py；全量 WPS 定向 171 项通过。
- 状态：离线通过；真机升级与系统 WPS 成功发送待完整版 wpad 环境验证。真机已确认 25.12.6 精简版的 `update_config` 报错和 reaver 注入掉线现象；未验证前不宣称升级流程已在真机通过。


## 2026-11-15 Directed capture timeout isolation

- Status: offline passed (101 focused tests); router and native Debian 12 acceptance pending.
- Preserve the directed aireplay-ng command, positive bounded burst count, target selection and wireless configuration. The wait budget is now based on burst count rather than treating it as seconds.
- On execution timeout, confirm the owned sender stopped using PID plus start token, then skip only that target for this round. Continue other targets, retain any already usable result, and permit configured later rounds. Cancellation, disconnect, unknown ownership or cleanup failure must not continue sending.
- Normal completion, nonzero tool exit, missing beacon, grouped target isolation, retries, cancellation, verified stop failure and shared connection behavior are covered. A harmless POSIX shell smoke test verifies exit/output preservation; it is not router acceptance.
- Evidence and source hashes: [validation record](deauth-timeout-validation-20261115.json). Retest the failing Redmi AC2100 and a previously working router before claiming real-device compatibility. No package, driver, firmware or UI visual changes were made.


## 2026-11-15 Quick PIN upgrade entry relocation

The full-wpad upgrade button now belongs to the quick PIN start/settings dialog instead of the capture toolbar. It is available only for a connected, idle session identified as basic wpad. Clicking it exits quick settings and opens the existing two upgrade confirmations; it does not save settings or launch PIN capture. Router commands, upgrade checks and rollback are unchanged, so this is a local UI relocation. Offline validation: 29 focused tests passed; actual Qt renders inspected in light/dark at 100/150 percent. Native Debian and router upgrade execution were not performed for this placement change. Renders: build/validation-wpad-button-move.


## 2026-11-15 终端会话与滚动修复

| 编号 | 预期行为 | 验证状态 |
| --- | --- | --- |
| TERM-01 | 重置结束旧终端会话后建立新命令行；普通断开仍保留原会话保留行为 | 离线通过；路由器实机待验证 |
| TERM-02 | 文件管理操作确认重建会话，等旧线程结束后再发送，不向旧脚本输入命令 | 离线通过；路由器实机待验证 |
| TERM-03 | 首次连接及探测期间改变窗口均使用当前行列数；Ctrl+C 正确传递 | 离线通过；路由器实机待验证 |
| TERM-04 | 普通终端滚轮翻本地历史，不被远端残留鼠标模式截走；会话保留仍使用远端历史 | Windows 原生控件离线通过；现场待验证 |

66 项定向检查通过，无跳过。版本、源码哈希和固定版本 tmux 官方依据见 [验证记录](terminal-session-validation-20261115.json)。明暗主题在 Windows Qt 离屏绘制下检查了 1000 像素宽度的控件布局。Debian 12 现有系统 Python 缺少 PySide6，且未安装 tmux；未执行其桌面启动、交互和退出验收。未对任何真路由器执行重置或脚本。重置不撤销配置，不保证结束已脱离终端的后台任务。


## 2026-11-15 Originally disabled verification radio

Selection may use a uniquely identified disabled radio when no enabled candidate exists. Temporary enablement must not broadcast an original hotspot. Original exact radio flags are saved before mutation and restored on band changes and all exit paths, using the existing device-bound recovery journal and read-back verification. Existing enabled radios retain the previous path; ambiguous identity remains blocked. Old schema-1 recovery records remain readable.

Status: offline core checks passed (142 passed, 1 local-reference skip); final 12 new cases passed. A broader run also found 3 table-status label/color failures outside this change; they are recorded separately. No live router or native Debian verification. Exact 25.12 upstream source remains unverified due source availability/certificate errors; TLS verification was not weakened. Evidence: [validation record](password-disabled-radio-validation-20261115.json).


## 2026-11-16 猜密码未确认与通道自愈

| 编号 | 预期行为 | 验证状态 |
| --- | --- | --- |
| PWD-01 | 候选时间预算耗尽时判为“未确认”，不再用剩余毫秒发起状态查询；状态查询至少保留 1 秒 | 离线通过；路由器实机待验证 |
| PWD-02 | 未确认不判密码错误、不写永久跳过记录，继续下一个候选；下次重跑会重新试 | 离线通过；路由器实机待验证 |
| PWD-03 | 快速通道断开先重启一次并重试当前密码；重启失败切兼容通道，只影响当前信号 | 离线通过；路由器实机待验证 |
| PWD-04 | 下一个信号仍先尝试快速通道，不沿用兼容通道 | 离线通过；路由器实机待验证 |
| PWD-05 | 单个目标超时只跳过该目标，批量继续；SSH 断开、用户取消、配置恢复失败仍整体停止 | 离线通过；路由器实机待验证 |
| PWD-06 | 记录页“已试记录”新增“未确认”列，显示最近一次运行数量，并说明下次重跑会重新试 | 离线通过；明暗主题离屏截图已核对；实机待验证 |
| PWD-07 | 目标开始时快速通道能力探测超时或传输中断，改用兼容通道，不结束整批 | 离线通过；路由器实机待验证 |

Status: 97 项定向检查通过 96 项，1 项为既有基线失败（`test_password_records.FilterTests` 的表格筛选，与本改动无关）；旧数据库缺列时自动补 `unconfirmed` 列已验证。已按官方 wpa_supplicant 2.9/2.10/2.11 源码核对 STATUS 字段、WRONG_KEY 事件、disable_network 与 pmksa_flush，未发现与改法冲突；核对中发现并修复了“能力探测超时导致整批结束”的漏洞。无真路由器验证，Debian 12 桌面未验证。未新增路由器命令或参数。证据：[验证记录](password-unconfirmed-recovery-validation-20261116.json)。



## 2026-11-16 Batch capture continuation

Current batch continuation requires matching workspace session, runtime session and physical radio ownership. Previous-task recovery, password recovery and pending cleanup remain blocking. Channel tuning failure continues other targets before retry, with at most three attempts and the configured round limit. A genuine cleanup failure still stops the batch.

Validation: 27 core and 108 adjacent recovery tests passed. No live router verification; underlying channel-switch refusal remains undetermined. No router commands, timeout values or UI changes. Evidence: [validation record](batch-capture-continuation-validation-20261116.json).


## 2026-11-17 Channel tune failure evidence

Failure-only evidence now retains the exact capture command, requested channel/interface, timestamp, process liveness and up to 8192 characters of combined tool output. Existing channel readback traces provide initial/final observed channel; existing wireless diagnostics preserve interface, driver and kernel scene before cleanup. One further bounded log read after the owned process stops retains shutdown-flushed output. Empty/unavailable output is explicitly marked and does not imply a driver error. No additional normal-path queries, timers, radio mutations or command parameters. Existing diagnostic export redaction is retained.

Validation: 32 capture/evidence tests and 16 diagnostic/export tests passed on Windows. Live-router overhead and Debian native execution remain unverified; this is diagnostic enrichment, not a channel-switch root-cause fix. No new release package built.


## 2026-11-18 Native backup and restore scope

| 编号 | 预期行为 | 验证状态 |
| --- | --- | --- |
| BAK-01 | 生成备份和恢复都不再用软件自加的 etc/root 目录限制，允许路由器原生清单中的自定义目录 | 离线通过；真机恢复待用户确认后验证 |
| BAK-02 | 不再限制符号链接目标必须在 etc/root，也不保留 nginx、collectd 专用白名单；普通绝对链接和父级相对链接都按原生 `tar -C / -xzf` 处理 | 离线通过；真机待验证 |
| BAK-03 | 生成备份和恢复使用同一套 `inspect_archive` 检查，不再出现“备份能生成、恢复却被自己拦截” | 离线通过 |
| BAK-04 | 保留文件大小、解压上限、条目数、路径穿越、重复项、特殊文件、损坏文件和哈希校验；上传后仍核对 tar 清单 | 离线通过 |
| BAK-05 | 恢复前确认保留，继续提示仅使用可信备份，并说明管理地址、登录密码和 Wi-Fi 可能改变 | 现有弹窗已覆盖；未改界面 |
| BAK-06 | 用用户提供的 `配置备份_20261118_091023.tar.gz` 做只读预检 | 通过：48 项、14312 字节、SHA256 2791f233…、1 个 nginx 运行时链接 |

Status: 66 项相关检查通过，0 失败；用户备份只做本地只读解析和离线预览，未向任何路由器发送恢复命令。已核对 OpenWrt 19.07 / 23.05 / 25.12 分支的官方 `sysupgrade`：备份清单允许任意目录和符号链接，恢复为 `tar -C / -xzf`，因此移除应用层范围限制与原生行为一致。无真机恢复验证，Debian 12 未验证；硬链接和特殊文件仍按安全边界拒绝。证据：[验证记录](router-backup-native-scope-validation-20261118.json)。


## 2026-11-18 Command channel retry and target isolation

Audited read-only queries retry channel-closed or channel-not-open failures at most twice, within the original deadline and only on the same authenticated connection identity. Before-command channel-open failures are replay-safe. No new mutation replay after submission; the existing owner-authorized single Channel-closed retry remains intact. No retry delay or new router command is introduced. Retry failures remain visible in operation diagnostics. Existing target failure isolation continues other targets after confirmed cleanup; actual disconnect, cancellation and unconfirmed cleanup remain fatal.

80 targeted tests passed, including real loopback SSH rejection/reopen and subsequent command execution. No router or Debian native validation; no package built. Two additional radio-preflight fixture errors are recorded separately. Evidence: [validation](command-channel-retry-validation-20261118.json).


## 2026-11-18 Offline report display privacy

Offline intelligent-analysis summary, AP SSID/MAC table and report labels now follow the existing window-owned privacy controller, including toggles while open and new live targets. Raw report objects and capture files remain unchanged. Summary storage is bounded to 200000 characters; no network or file reads were added to rendering. Existing browser, layout and styles remain intact. Exempt from OpenWrt command compatibility review because this change only projects existing PC-side report data.

24 targeted tests passed. Dark AP and light summary renders inspected; both themes rendered. No Debian native desktop test and no new package built. Evidence: [validation](offline-report-privacy-validation-20261118.json).


## 2026-11-19 Regular capture defaults and completion reset

Scan and per-target timeout defaults are both 60 seconds; persisted custom values and PMKID-specific settings remain unchanged. Bulk clear now resets all CAPTURED targets including those with current-session artifacts, consistent with single-target clear. Files and artifact entries remain intact; revision invalidation and snapshot/history signals are retained. Privacy-on and privacy-off table updates verified.

39 checks passed, 1 existing environment skip; includes settings save/reopen and status/result-presence regressions. No live router or native Debian validation, no package built. Evidence: [validation](capture-defaults-clear-validation-20261119.json).
