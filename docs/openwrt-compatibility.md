# 官方 OpenWrt 兼容核查

本轮日期：2026-10-22。范围仅为官方 19.07、21.02、22.03、23.05、24.10、25.12，覆盖现有功能；不承诺所有路由器硬件具有相同能力，不包括衍生固件。

## 本轮修复

- 原生扫描过去要求每条结果带有频率或频段，导致旧官方接口返回正常结果后仍报“无法核实扫描信号所属频段”。
- 公共解析器现在接收实际扫描网卡的信道表。优先核实结果里的频率、频段，缺少时只使用该网卡信道表中可唯一确认的频段；不按 radio 编号、型号或当前工作信道猜测。
- 数据互相冲突、多个频段共用信道、无效地址或名称等条目会被拒绝，保留同批其他有效结果并提示跳过数量。全部无效与真正没有发现信号分别处理。
- 补齐扫描工作流成功、失败、取消记录，以及返回条数、接纳/拒绝数量、新旧格式字段数量。日志不保存扫描名称、地址、密码或原始扫描正文。
- 增补异常数字、非有限频率和无效 Unicode 边界；坏条目不再使整批有效结果丢失。
- 修正旧表头测试：表格位于“无线桥接”子页，默认“已配置信号”页是卡片；未修改产品视觉设计。

## 官方源码证据与版本矩阵

从每个官方系列的 rpcd Makefile 读取固定提交，再核对对应 iwinfo.c 与 uci.c。固定提交、源码地址、哈希和派生测试样例见 tests/fixtures/official_openwrt_contracts.json；原始下载证据在 build/compatibility-audit/upstream/。样例为依官方格式构造的数据，不是真机抓取。

| 官方系列 | 扫描接口格式 | 常规包管理器 | 本轮结论 |
| --- | --- | --- | --- |
| 19.07 | 不提供频率、频段字段 | opkg | 旧格式解析回归通过 |
| 21.02 | 不提供频率、频段字段 | opkg | 包含本次用户故障的格式回归通过 |
| 22.03 | 不提供频率、频段字段 | opkg | 旧格式解析回归通过 |
| 23.05 | 提供频率、频段字段 | opkg | 新格式解析回归通过 |
| 24.10 | 提供频率、频段字段 | opkg | 新格式解析回归通过 |
| 25.12 | 提供频率、频段字段 | apk | 新格式及包管理选择回归通过 |

以上是对应分支固定源码的格式证据，不代表每个历史补丁版本都刷机验证。生产代码仍以实际探针选择包管理器和能力，不把表格当版本判断开关。六份固定 rpcd 源码均提供 apply、confirm、rollback 接口；实际设备仍须逐次探测，缺失时不能绕过保护。

## 逐功能核查清单

| 功能 | 本轮核查与验证 | 必需条件或边界 |
| --- | --- | --- |
| 登录、凭据、指纹、重连 | 当前登录与 SSH 模拟回归通过，核对连接身份隔离 | SSH 可用；指纹变化需要用户确认 |
| 型号、架构、网卡、频段 | 设备身份与 radio/phy 映射解析、非顺序网卡样例通过 | 无法唯一识别时停止无线接管 |
| 依赖检测、安装、卸载 | 核查探针、预演、空间、取消和清理；opkg/apk 相关测试通过 | 发行版、准确版本、架构及来源匹配；不替换已有核心无线包 |
| 离线包、电脑代下载、更新 | 本地匹配、完整性、旧源归档和 APK 本地索引流程核查 | 本地没有某版本资源时不等于应用不支持，可走已适配在线或代下载路线；包在官方源缺失时明确停止 |
| 信号列表、详情、隐私与密码 | 配置/运行状态解析、详情布局、密码回读和连接切换回归通过 | 固件未提供的信息显示未知；不把缺失数据当正常 |
| 桥接扫描与动态列表 | 六个官方格式、逐批回显、取消及临时接口清理回归通过 | 原生 iwinfo 与实际 PHY 可识别；扫描不提交配置、不启用 AP |
| 发射信号与高级参数 | 保存、带宽/工作频率筛选、客户端隔离、既有参数保留回归通过 | 选项同时受硬件与无线组件能力限制 |
| 无线桥接、身份伪装 | 独立子网、WDS、relayd、MAC 身份及地址分配恢复的 Lua 模拟通过 | WDS 需要上级支持；relayd 为 IPv4，端到端数据仍待真机 |
| 信号启停、父网卡启用 | 普通信号、桥接信号、仅父网卡关闭及失败回滚回归通过 | 同网卡其他已启用信号可能恢复；其他禁用信号保持禁用 |
| 编辑、删除、仅保存 | 版本/连接绑定、取消、共享接口保留与保存状态回归通过 | 不删除不属于本操作的配置 |
| 待提交改动、回滚 | 预览、提交、冲突拒绝、超时取消与恢复检查通过 | 采用 rpcd 独立会话；无法确认恢复时提示等待后重连 |
| 遗留接口、搬家清理 | 归属、引用、备份、旧式接口和新式设备配置路径的 Lua 回归通过 | 归属未知或仍被引用的配置不自动删除；本轮未对真实路由器清理 |
| 新旧网络、防火墙 | 核查逻辑网络引用、设备/MAC 路径与标准 UCI 区域配置 | 不按版本混用 ifname/device，不通过硬编码 iptables 命令适配 firewall4 |
| 全局/定向 PMKID | 工具帮助与版本参数、过滤、启动失败、资源管理离线测试通过 | 必须使用已确认工具 API 和驱动能力；不能保证目标返回 PMKID |
| 常规/自动抓包与同信道任务 | 调度、结果、清理、断线错误和通道分组离线核查 | 驱动监听/注入、真实射频效果待对应设备验证；不扩展现有 2.4/5G 抓包为 6G |
| WPS 与在线密码验证 | 核查工具调用、参数、工作目录、停止与恢复路径 | 依赖对应工具、目标能力和授权；本轮没有实际发包或登录外部网络 |
| 抓包结果下载与转换 | PCAP/EAPOL、输出策略、过滤和路径处理离线测试通过 | 保留原始证据，不把解析成功说成真实密码验证成功 |
| 文件操作与上传下载 | 目录列表、文件属性、路径、递归传输、取消和断线模拟通过 | SFTP 服务缺失须提示补充；不跟随不明链接越界操作 |
| 终端与会话历史 | 交互通道、输出、会话历史与普通 shell 回退模拟通过 | tmux 可选；没有 tmux 时不宣称断线后会话继续 |
| 设置、窗口、反馈、退出、更新 | 当前配置隔离、明暗主题/缩放、退出任务分类、反馈与签名更新回归通过 | Windows 原生窗口测试另用 windows 后端补跑；不修改线上更新通道 |

## 验收证据与未覆盖范围

- 当前项目基线及最终受影响模块复测，合计覆盖 78 个测试模块、600 次测试执行；计数包含导入的重复用例，不代表 600 个独立版本场景。额外第一轮为 19 个模块、193 次执行及 5,000 组异常字段组合；额外第二轮为 76 个模块、591 次执行，后续新增的登录、存储边界和打包一致性测试也分别通过。
- 唯一因 offscreen 跳过的原生窗口测试，随后使用 QT_QPA_PLATFORM=windows 补跑通过。主验收汇总见 build/compatibility-audit/acceptance.json，各模块原始输出在同目录。
- 额外执行了当前离线包收集器与断线清理测试。只读复用历史测试时，22 个模块共 374 次测试执行通过，且已核实生产模块来自本项目。
- 14 个历史模块最初存在旧项目契约差异或测试夹具问题，不作为当前验收通过项：包括旧日志名、已移除的设置/入口、缺少新增探针响应的模拟器、未模拟新的管理链路保护，以及要求 --allow-untrusted 的旧 APK 断言。其中历史登录测试在构造页面后才拦截线程，已迁入当前项目并在页面构造前拦截，6 项通过；其余旧契约差异仍单独保留。完整原始记录在 tests/migration-audit-round3.json，未通过放松产品安全校验让旧测试变绿。
- 上述结果属于代码审查、官方格式契约、模拟设备、真实 Lua 脚本和本机 Qt 检查；没有对六个系列逐一刷机，没有真机验证射频、WDS 互操作、relayd 终端转发、重启持久化和物理断线恢复。
- 最终版本为 1.0.10_alpha，用户在两轮额外核查后授权打包并发布。最终文件、包内源码一致性、线上校验和存储清理结果以验收汇总为准；中间测试包均未发布。

## 后续维护

项目 AGENTS.md 已要求今后每项新增、修改都核查上述六个官方系列，公共能力缺失时明确提示，不以模拟测试冒充真机认证。Cloudflare 发布入口已接入 1,000,000,000 字节峰值保护：只使用项目隔离认证，完整分页统计对象，验签当前通道，保留当前及必要回滚包、全部离线资源和共享对象；不足时只清理明确无引用的旧完整软件 ZIP，仍不足则停止发布。发布任务在同一主机互斥，删除前后复核引用并记录结果。此上限是用户指定的存储预算，不代表 Cloudflare 的全部计费规则。

## Frozen release integrity

The incremental build was found to retain stale transitive module bytecode. Release builds now use PyInstaller --clean and verify every frozen project module against current source before assets or delivery ZIP creation. A mismatch fails the build; per-module source SHA-256 values remain in the build report. Rejected unpublished artifacts are disposable local staging and are removed by scripts/build_cleanup.py; acceptance reports remain.


发布结果（2026-10-22）：1.0.10_alpha 已发布并通过客户端下载校验。发布后只读核实云端总占用 996,461,443 字节，低于 1,000,000,000 字节限制。本次删除 3 个旧软件完整包，释放 199,399,642 字节；离线包和共享更新文件保留。离线与本机核查不代表六版本逐台真机认证。


## 1.0.11 alpha 更新检测修复与保留策略

1.0.10 将主程序改名为“路小管.exe”，超出了 1.0.8 已发布更新器允许的根目录程序名，因此旧版拒绝新版更新目录。现恢复稳定文件名 LuXiaoGuan.exe，中文界面名称不变；生成更新目录时增加向后兼容检查，历史清单中的不兼容改名条目不下发给旧版。

使用 1.0.8 实际发布程序内的原始验签和目录解析代码核验 1.0.11 成功；原始更新规划器检查无冲突，仅 4 个变动文件，预计下载 17,177,490 字节。62 项相关定向回归通过；新打包的 384 个主程序模块及 17 个更新模块通过包内源码一致性检查，启动和更新程序烟测通过。

云端保留规则调整为：软件每个通道当前及上一版；常用依赖包、抓包依赖包分别只保留当前一版。上传和校验成功、切换签名通道后清理旧包；上传前仍校验 1,000,000,000 字节峰值。共享压缩更新文件服务按文件增量更新，仍有引用时保留。

首次按新规则清理后总占用 612,626,749 字节；1.0.11 上传前预估峰值 696,578,086 字节。最终线上旧版检测结果与清理后实际占用见 build/publishing 中的验收记录。


最终发布结果：按用户要求保持 1.0.11_alpha 版本号，重新纳入最新 SSH 和登录诊断代码。最终包 SHA-256 为 0c5049c011190da0163db09456d7e01507eec8756400050fe6da78d56bbb8f1b，大小 66,555,767 字节。385 个主程序模块和 17 个更新程序模块通过包内源码一致性检查；最新登录诊断相关 39 项检查通过。首次冻结界面烟测超时已保留原报告，源码及冻结程序复核分别 3.88 和 3.73 秒通过，随后全部冻结烟测重新通过。

已用 1.0.8 发布程序内的原始代码在线检测到 1.0.11_alpha，并核实更新目录、更新计划均通过：4 个变动文件，无冲突，预计下载 17,180,509 字节。首次上传失败及之前构建均未切换更新通道。

本次发布完成后的实际云端占用为 647,526,681 字节。软件完整包保留 1.0.10_alpha、1.0.11_alpha 两版，常用及抓包离线依赖各保留当前一版；共享更新压缩文件保留。发布回执及旧客户端验收记录已保存于 build/publishing/final-1.0.11.json。


## Radio identity and preparation diagnostics (2026-10-22)

- Shared identity merges runtime interface ownership with the native read-only `ubus iwinfo phyname` lookup. `radio` and `phy` numeric suffixes, CPU architecture, channel numbers and device-path suffixes never establish identity. The kernel phy inventory must also contain the claimed hardware.
- Device inventory, takeover, reverse lookup and verification preparation use this shared mapping. Conflicting sources or duplicate claims reject all affected identities. Same-band candidates are not resolved by list order. Disabled, missing and unsupported identity sources remain unknown unless independent runtime ownership proves the relationship.
- All six pinned official rpcd sources expose the section/phyname lookup contract. Their LF-normalized SHA256 hashes were checked against the existing official source inventory; derived contract samples are in `tests/fixtures/official_radio_identity_contracts.json`. Availability still depends on rpcd-mod-iwinfo, its backend and the actual device configuration. No driver or firmware replacement is performed.
- Preparation diagnostics retain only fixed stages, status, safe error categories, counts and elapsed time in the existing bounded operation journal. They omit arguments, credentials, commands, raw configuration and exception messages. Export preserves exact fixed operation names while retaining secret redaction for arbitrary text. Primary preparation and restore failures are recorded separately.
- Failed preparation that never changed configuration does not restart networking. Partial configuration setup still attempts restoration. Backup copy failures are no longer suppressed.
- Offline mapping/diagnostic/management-link regressions pass; this does not certify a physical router, successful capture or password verification. The existing user ZIP has no native identity lookup output, so its missing runtime ownership cannot alone prove how that router will resolve with this change.

Validation record: `build/radio-identity-audit/summary.json` contains 8 isolated modules, 70 tests, zero failures and zero skips. One combined-process run hit the asynchronous worker notification wait limit; the isolated management execution module subsequently passed all 4 tests. No product timeout was relaxed and no timer workaround was added. These are targeted offline checks only.


### Self-review corrections (2026-10-23)

- Optional identity-query timeout no longer invalidates an otherwise authenticated device probe; cancellation still propagates. Configuration-changing paths continue to require proven identity.
- Native probe output is capped at 32 KiB and accepted only with complete framing and exactly the requested radio sections. Refresh replaces the previous identity block. A successfully read current kernel inventory excludes stale phys from old runtime output.
- Restore audit returns an explicit unverified-state notice when its snapshot or ownership cannot be checked, or when the recorded physical owner differs from current ownership. It does not guess from numbering or silently treat unknown as clean.
- Verification failures omit router stdout/stderr from exception text. Actual command timeout raises TimeoutError so the phase journal distinguishes it from an ordinary failure.
- Final isolated review: 8 modules, 79 tests, all passed, zero skips. Evidence: `build/radio-identity-review/summary.json`. Six pinned official lookup contracts were rechecked against normalized source hashes. These results remain offline checks, with no router configuration changes or real-device certification.


### Second self-review (2026-10-23)

- Reproduced three cleanup failures before editing: a restore warning missing from the returned immutable result tuple, a restore exception replacing the primary preparation exception, and pause-control finalization preventing the router restore attempt.
- Cleanup now attempts router restore after control finalization failures, preserves the primary exception, records restore failure separately, and constructs returned results after cleanup so warnings are included. Control finalization has its own bounded safe diagnostic stage.
- Query cancellation remains cancellation even when output-limit failure occurs simultaneously. Old native identity is removed before all early-return guards, including the radio-count guard.
- Configuration-dirty state is set in the shared command execution boundary immediately before a modifying verification command is attempted. Cancellation after selection but before the first write no longer triggers an unnecessary network restart; uncertain partial execution still attempts restore.
- Final isolated regression: 9 modules, 87 tests, zero failures and zero skips; `build/radio-identity-review-round2/summary.json`. All nine affected Python source/test files compile and pass UTF-8 checks; `git diff --check` passes. No live router changes, build or publication were performed.


## 无线新增、编辑和中继兼容复核（2026-10-23）

本节覆盖官方 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12 的发射、新增、编辑、中继验证、保存并应用和仅保存。依据固定官方源码与离线原生 Lua 回归；不包含衍生固件，也不等于这些系列所有硬件都已通过真机认证。

### 参数与状态接口差异

| 系列/路线 | 已确认差异与处理 |
| --- | --- |
| 19.07 | 固定源码不解析 skip_inactivity_poll，界面禁用且后台拒绝提交；AP 没有 get_status，改用旧 BSS 接口、实际频率、Master 模式、SSID 和设备启用状态联合确认。 |
| 21.02 | 有不活动轮询参数及 get_status；组播转换按该设备实际支持的旧参数处理。 |
| 22.03 / 23.05 / 24.10 | 检查实际参数清单，优先显示官方界面对应的 multicast_to_unicast_all；不把旧的桥接层选项改写成这个参数。 |
| 25.12 | 同时核对 shell 与可选 ucode 两条路线及其参数别名。生产代码读取实际运行路线的清单，不按版本号选一份固定参数表。 |
| 全部系列 | beacon_int 属于 wifi-device，dtim_period 属于 wifi-iface；信标间隔从实际射频配置回显并写回，保留其他热点配置。 |

能力来源为只读 network.wireless get_validate。旧 C 服务与新 ucode 服务都按网卡返回 device / interface 作用域。设备只返回应用白名单的支持布尔值，不传回完整参数定义或密钥。没有清单、清单损坏、网卡不匹配时不猜测支持。未直接运行驱动 dump：旧脚本的该路线包含清理调用，不适合作为应用只读探针。

前端与后台共用无线参数能力策略。固件未声明的字段禁用，提交、自动补齐默认模式和中继预验证也检查支持；已有未知字段保留。WDS、漫游、加密、频段/带宽还需要相应驱动与无线程序能力，relayd 仍走原有的确认安装流程。名称采用统一官方术语，不因版本号随意改名。

新旧热点状态确认均核对实际 SSID 和设备启用状态。仅在 get_status 明确返回“方法不存在”时允许旧接口路径；DISABLED、权限错误和其他错误不会触发降级。旧热点尚未重启、名称不一致、频率不一致或网卡已关闭时，不能确认新设置成功。

能力重新装配前释放原来的联动回调与捕获的设备数据，每项职责只保留一份绑定，避免重复回调和旧配置持续占用内存。能力禁用不会覆盖带宽自身“尚无合法选项”的禁用条件。

### 官方证据与样例

- tests/fixtures/official_wireless_setup_contracts.json 包含六系列七种路线的固定提交、官方 URL、原始 SHA256 和按实际声明构造的 get_validate 样例；样例不是路由器抓取数据。
- build/wireless-version-audit/source-index.json 与 upstream/ 保存本次下载证据。追加第一轮复核了 46 份唯一固定来源，包含旧 BSS 在完成 setup 后注册的官方补丁。
- 测试执行真实 Lua 事务，核查参数归属和读回、原密码/网络保留、旧 ifname/type 与新 device 结构保留、WDS / IPv4 relay、仅保存、取消回滚、错误清单、实际状态及联动回收。没有强装驱动或替换核心无线组件。

### 本轮与追加六轮结果

| 检查 | 隔离模块 | 测试数 | 结果 |
| --- | --- | --- | --- |
| 本轮修正后基线 | 14 | 132 | 通过，零失败、零跳过 |
| 追加第 1 轮：固定来源与返回格式 | 2 | 26 | 通过，零失败、零跳过 |
| 追加第 2 轮：新增/编辑/中继/保存一致性 | 9 | 101 | 通过，零失败、零跳过 |
| 追加第 3 轮：状态切换与恢复边界 | 13 | 132 | 通过，零失败、零跳过 |
| 追加第 4 轮：逐参数七路线写入与读回 | 2 | 21 | 通过，零失败、零跳过 |
| 追加第 5 轮：能力刷新和联动生命周期 | 7 | 58 | 通过，零失败、零跳过 |
| 追加第 6 轮：最终完整定向复核 | 16 | 143 | 通过，零失败、零跳过 |

每轮有独立日志与 summary.json，位于 build/wireless-version-audit/initial-final 和 round1 至 round6。轮次之间有重复用例，不将它们相加宣称为独立测试数。逐参数测试内部还包含多版本子场景。

语法、UTF-8 和 git diff --check 检查通过；明暗主题和 100% / 150% 缩放离屏绘制已检查。现有设备识别等未提交工作保留，未打包、提交、推送或发布。

仍需对应真机确认：上级 WDS 互操作、实际客户端转发/DHCP、驱动对可选参数的执行效果、重启持久性以及管理链路中断后的恢复。仅声明支持某个官方系列，不代表该系列每个硬件、驱动和无线程序变体具有相同参数或能力。


### 追加第七、八轮复核（2026-10-23）

本次按要求再独立核对两轮，发现并修复三个重新载入边界问题：

- 第一轮：能力恢复后，先前被过滤的 WPA3 / OWE 选项未恢复。现在始终从构造时的固定选项表重建，避免把上次过滤结果作为下次的来源；仍保留设备当前的加密及密码策略。
- 第一轮：高级数字与文本字段重新载入后，可能残留上次未保存的输入。现在与其他字段一致，在重新装入设备配置时清空待提交文本，以最新原配置作为提示，避免旧输入被误提交。
- 第二轮：编辑概要反复追加当前信道文字，可能同时显示旧信道。现在从固定概要标题重新生成，保持单份最新信道说明。

三个问题均有修复前失败证据和修复后回归：round7-before 为前两个问题，round8-before 为第三个问题。第一轮保存路径的初次调用误写了两个测试模块名，出现零测试导入失败；未将该次调用当作通过，随后在 round7-save-paths 用正确模块名补齐，13 项全部通过。第一轮有效检查合计 8 个模块、86 项测试；第二轮最终覆盖 16 个模块、146 项测试，零失败、零跳过。轮次之间包含重复检查，不相加宣称独立测试数。

三项改动不涉及路由器命令、参数值、默认策略、MAC 功能、主题或布局。Python 语法、UTF-8 和差异空白检查通过；重复载入后的编辑窗口已按真实公共主题绘制并检查明暗及 100% / 150% 缩放，截图在 build/wireless-version-audit/round8/visuals。全部结果仍为离线验证，未连接真机，不能作为长时间防掉线或各硬件实际互通结论。


### 追加第九、十轮复核（2026-10-23）

按用户要求再核对两轮，并在中断后继续完成。本次发现并修复两个后台问题，未修改界面主题、布局、MAC 功能或参数默认值。

- 第九轮：中继应用后的保存确认没有核对 DNS 列表与 peerdns（是否采用上级 DNS）的实际保存值。修复前，修改这两项已保存结果仍可能被确认成功；六系列七路线均有回归复现。现在在公共 saved_status 中核对提交地址、列表顺序与开关；显式清空 DNS 时同时确认原列表已移除。支持 UCI 列表及旧式空格分隔字符串，未提交 DNS 时保留原设置，WDS 不执行独立网络 DNS 核验。核验失败沿用原事务回滚，不确认保存。
- 第十轮：相同配置重复执行“仅保存”时，会因会话没有差异而报 save_commit。现在无差异路径必须先通过结果核验回调，再复查无线与 DHCP 配置版本，之后返回已保存且未应用；不创建备份、不重复提交、不重载网络。错误的预期配置仍被拒绝。该公共函数的遗留网络接口清理调用方也已同步回归。

修复前证据：round9-before 的两个新增测试方法共复现 35 个失败子场景；round10 在七个配置路线的重复仅保存检查中失败，均保留原日志。第九轮修正后 7 个模块、93 项测试通过。第十轮最终结果在 round10-fixed：17 个模块、159 项测试，零失败、零跳过；增加覆盖共同调用方所需的遗留接口清理测试。不同轮次测试数不相加作为独立用例数。

语法、UTF-8 与 git diff --check 检查通过。两轮均为离线真实 Lua 事务和 Qt 回归，未连接或修改真路由器，不能据此宣称长时间防掉线或各硬件互通已验收。未打包、提交、推送或发布。


### 追加第十一至十五轮复核（2026-10-23）

按用户要求连续完成五轮检查，发现并修复两个公共边界问题：

1. 第十一轮：基本数字字段的校验曾通过字符串转换接受整数对象，以及普通数字以外的部分 Unicode 数字。界面输入通常是字符串，但这些其他格式可能越过校验后才在设备端失败。现在 DTIM、信标间隔、连接数、跃点、功率和信道只接受底层执行需要的普通数字字符串，保留原范围与自动信道选项。无效输入在设备检测和会话创建前拦截，不改变正常参数及默认策略。修复前新增边界测试复现 14 个失败子场景。
2. 第十二轮：中继保存后的防火墙区域成员关系未完整核对，缺失、错误区域或同时属于多个区域时可能仍确认成功。现在针对独立网络及 IPv4 中继核对所选区域的唯一成员关系，兼容列表和空格分隔字符串；WDS 共用网络路径不套用此规则。遍历回调只收集状态，在回调外统一产生结构化错误，避免跨原生回调丢失错误码。异常沿用既有事务恢复，不确认保存。修复前六系列七路线复现 21 个失败子场景，修复后的三种异常均按预期拒绝。

| 本次轮次 | 核查重点 | 模块 / 测试 | 结果 |
| --- | --- | --- | --- |
| 第十一轮 | 数值范围、类型及设备访问前拒绝 | 4 / 46 | 全部通过，零跳过 |
| 第十二轮 | 防火墙归属、新旧列表格式及保存保留 | 5 / 68 | 全部通过，零跳过 |
| 第十三轮 | 固定官方证据、逐参数七路线及依赖来源 | 4 / 53 | 全部通过，零跳过 |
| 第十四轮 | 取消恢复、删除归属、仅保存和任务结束 | 8 / 65 | 全部通过，零跳过 |
| 第十五轮 | 所有本次相关路径最终回归 | 19 / 174 | 全部通过，零跳过 |

有效日志分别位于 build/wireless-version-audit/round11、round12-fixed、round13-fixed、round14、round15。保留 before 及修正过程中未通过的日志，不计为通过。各轮包含重复测试，不相加宣称独立用例数。

第三轮同时重新计算并核对 46 份固定官方源码的 SHA256，与六系列七路线样例逐项匹配，证据见 round13/source-review.json。本次未发现额外版本差异，仍按设备实际能力决定可用字段。第四、五轮未发现新增问题。相关 Python 语法、UTF-8 和 git diff --check 检查通过；本次未改界面布局、主题、MAC 功能或默认参数。

全部仍为离线验证，未连接真机，未测试长时间断线、上级设备 WDS 互通或真实驱动执行效果。未打包、提交、推送或发布。


### 无线应用期间响应、共用管理链路识别及编辑窗口控制（2026-10-23）

针对“编辑电脑当前连接的 Wi-Fi 后短暂断线，源码版窗口还在但点不动”的反馈，离线真实 Qt 复现确认：原信号编辑器使用 QDialog.open()，等待 SSH 应用结果期间仍是模态窗口，会阻挡主窗口输入。此次将信号设置窗口改为有所属的非模态窗口；任务中只禁用当前表单，保留取消、窗口控制和后台任务互斥。恢复仍由常驻服务的原后台线程完成，不使用定时补丁或强制终止线程。该复现说明一条已修复的输入封锁路径，不代表已经取得用户当时主线程阻塞栈或排除了所有其他卡顿原因。

“保存并应用”在正式确认前新增只读后台检查，直接复用抓包与扫描使用的 openwrt.management_link.read_management_link。它根据当前 SSH 对端的实际回程、邻居表、桥转发表和 sysfs 无线归属判定接口与物理网卡，不依赖电脑 Wi-Fi 名称，也不另建 Windows 网卡猜测线路。无线设置仅新增只读运行接口到配置节的对应关系：network.wireless status、network.device status 及 iwinfo phyname，均为既有官方兼容路线。六系列七路线均有真实 Lua 只读回归，不建立写配置会话。

提示区分：
- 当前登录经过本次编辑的同一信号：明确提示应用后 Wi-Fi 和管理连接会暂时断开，等待恢复、重新连接 Wi-Fi 并重新登录。
- 共用同一块物理无线网卡：明确提示同网卡信号也可能中断，不按 radio 名称或频段猜测。
- 已核实有线、已核实另一块物理网卡或无法确认：分别说明证据及可能中断，不把未知写成安全。路由器经过中继、虚拟化或桥接时，继续使用共用回程证据。
- 只读检查取消、用户拒绝确认、检查期间连接已变更时，不执行保存。仅保存流程不增加链路确认。
- 抓包原有的严格拒绝/确认策略保持原样；编辑功能使用检测结果形成风险提示，复用探测而不混同两种操作的决策策略。

保存前只读检查使用独立任务类型 wireless_apply_check，其结束不触发无关的自动清单刷新，避免确认窗口打开期间被刷新抢占任务槽。该竞争已通过真实常驻服务调度回归复现后修正。

公共 FramelessDialog 为编辑型窗口提供最小化、最大化、还原及双击标题切换；复用现有图标与明暗主题。最大化时隐藏缩放手柄，还原后恢复；最小化/最大化保留未保存输入与任务状态。主窗口沿用原有控制按钮，确认、消息和纯进度窗口不重复添加。所有现有编辑型无边框弹窗通过公共默认策略获得支持，包括信号编辑、文件名称/权限输入等。

验证：
- 修复前保存等待的两个真实 Qt 测试均复现主窗口被模态窗口封锁；修复后验证工作线程仍在等待时主窗口可操作、断线结果交接、任务释放和窗口保留。
- 22 个相关模块的最终有效结果共 181 项，零失败、零跳过。最终结果由 final-reviewed 与 physical-final 中同名模块的最新结果合并，不相加重复测试。包含六系列七路线、原抓包链路与真实 POSIX 探针、自动调度、取消确认、窗口状态和主题作用域。
- 新标题栏和最大化状态已实际离屏绘制并检查，覆盖明暗及 100% / 150% 缩放；断线确认正文也按真实公共主题绘制。截图及测试日志位于 build/wireless-responsiveness。
- Python 语法、UTF-8 与 git diff --check 检查通过。本次未在真实路由器上应用当前连接 Wi-Fi 的修改，离线响应测试不能替代真机短断线验证。
- 仅修改源码；需重启源码版后使用。本次未替换正在运行的安装版、未打包、提交、推送或发布。


### 上述改动的独立边界复查（2026-10-23）

用户要求复查刚加入的断线响应、连接识别及编辑窗口控制。本轮新增四个回归，修复前均失败，归为三类问题：

- 编辑器销毁后的生命周期：确认框的父编辑器在等待确认期间被销毁时，原清理代码会再次访问已销毁的 ConfirmDialog；迟到的检查结果也可能访问已销毁的 QWidget。现在只回收仍存活的确认框，并在检查连接身份、结果交接前核对编辑器是否仍有效。已销毁的窗口不再接收控件更新或继续保存。两条回归执行真实 Qt 销毁路径。
- 只读检查取消的交接边界：后台已经返回但尚未在主线程发布结果时，用户取消仍可能得到成功检查结果。现在在 apply_check 的最终交接点再次核对取消事件，丢弃未发布的成功值并返回已取消；其他已经提交配置的任务不套用此规则，避免错误报告提交结果。
- 原有模态编辑器最小化：单独最小化模态窗口后，其主窗口仍被模态输入锁阻挡。公共窗口控制现在将带可见所属窗口的模态编辑器按窗口组最小化，保持模态关系和输入；从所属窗口恢复后继续编辑。非模态信号编辑器继续单独最小化，保留后台任务和取消功能。

验证结果：build/wireless-responsiveness/review-before 保留四条失败证据；review-fixed 中 9 个相关模块、70 项测试全部通过，零失败、零跳过。覆盖主窗口响应、销毁后的迟到结果、取消交接、窗口状态恢复、六系列七路线识别以及原抓包保护和任务调度。Python 语法、UTF-8 与差异空白检查通过。Qt 对模态窗口及其父窗口销毁关系的说明见 https://doc.qt.io/qt-6/qdialog.html#exec 。

本轮没有连接真实路由器，没有执行 Wi-Fi 断线操作；离屏 Qt 回归不作为真机断线验证。源码需要重启生效，未更新安装版或发布。
