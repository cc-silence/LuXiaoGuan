# 常用设置与抓包三轮兼容核查

日期：2026-10-25。下文保留修复前核查记录；后续已按用户授权修复 F1/F2/F3，见文末。未连接或写入真实路由器。

结论：不能宣称全部兼容。当时新确认三类遗漏，现已修复。每轮均包含资料核查、代码对照和定向验证，不把三组测试当成全硬件认证。

## 已确认问题

### F1：开机策略漏掉合法布尔写法（优先处理）

六系列指定的 UCI 源码均接受 `auto=false`，系统函数也接受 `ignore=true/yes`。软件只检查 `auto=0` 和 `ignore=1`，已复现这些配置下仍允许关闭上级开机连接。可能错把不自启动的 LAN 或停用地址分配的网络当作可用管理入口。不代表每个配置都必然断联。

位置：`router_policy_plan.py` 的 boot。建议按字段真正的消费者规则解析：不同字段的布尔语义不应一律猜测。上次修复的 `disabled=1` 保护仍存在，本项是新的格式边界。

### F2：多目标原包拆分失败仍删除原始下载（优先处理）

`DownloadedHandshake.analyze()` 对多目标拆分使用 finally 无条件删除原包。已用无效 PCAP 头复现：拆分抛出 ValueError 后，原始下载文件不再存在。其他拆分异常也会经过这段 finally（代码推论，未实测磁盘写满）。这是文件保留问题，不是所有驱动都会产生坏包。

位置：`handshake_capture.py`。建议拆分、分析和保存确认成功后才回收原包；失败时保留原始证据和可找到的路径。

### F3：临时接口添加结果不确定时缺少本地恢复凭据

临时接口记录只在添加命令退出码为 0 后建立。模拟远端已添加但 SSH 等待超时时，已复现本地记录为空、没有进入即时删除。远端真实状态需要后续核实，不应宣称每次超时都已添加，也不应宣称必然永久残留。

位置：`radio_session.py` 的 create。建议先证明名称未占用并保留任务归属，对不确定结果保留待核实凭据，恢复时核对归属，不盲删同名接口。

## 第一轮：系统与工具接口

实际联网下载六系列工具配方、固定 UCI 源码、系统布尔函数、三个 hcxdumptool 标签、三个 aircrack-ng 标签和多个 Linux 版本的 nl80211 定义。对照现有命令参数、工具准入、新旧配置与前次修复。定向回归 70 次通过。

| 系统系列 | 官方 hcxdumptool | 官方 aircrack-ng |
| --- | --- | --- |
| 19.07 | 6.0.1 | 1.5.2 |
| 21.02 | 6.0.1 | 1.6 |
| 22.03 | 6.2.4 | 1.6 |
| 23.05 | 6.2.4 | 1.7 |
| 24.10 | 6.3.4 | 1.7 |
| 25.12 | 6.3.4 | 1.7 |

此表是当次读取的官方分支配方，实际设备仍须探测已安装工具。现有握手命令的 background、channel、output-format、write-interval 等参数在 1.5.2/1.6/1.7 上游源码中均存在。已依据源码帮助选项重建当前三个受支持版本的被动参数契约，不代表真机已收到数据。

6.0.1 的 PMKID 路径被软件明确拒绝；不代表该系统的常规握手抓包不支持。主动请求目前仅适配 6.3.4，不以系统年份作唯一标准。随包静态工具只对已核实的 23.05.5 Filogic、aarch64_cortex-a53、opkg 组合开放，还要检查内核和真实接口 API。

## 第二轮：硬件与管理连接

实际联网重查 15 平台所属 12 个目标家族、六个代表发行版和四个型号的设备定义。92 个路径中 76 个存在，16 个为旧版不存在或目录变更。不把官方不存在的平台版本组合算作支持。定向回归 94 次通过。

| 平台 | 必须核对的硬件边界 |
| --- | --- |
| mediatek/filogic、mediatek/mt7622、ramips/mt7621 | mt76 具体驱动、固件和并发限制；不能因 CPU 相同推导任意 MAC 或注入能力。 |
| ipq40xx/generic、ipq806x/generic、ipq807x/generic、qualcommax/ipq807x | 具体无线驱动和固件；目标目录变更不代表硬件相同或功能已认证。 |
| ath79/generic、ath79/nand | 具体网卡能力、小内存和闪存空间。 |
| bcm53xx/generic | 实际使用的无线驱动；有固件包不代表支持 monitor。 |
| bcm27xx/bcm2711、mvebu/cortexa9、mvebu/cortexa53、sunxi/cortexa53、x86/64 | 实际内置或 USB/PCIe 网卡能力与存储布局；无无线硬件时不适用抓包。 |

型号核对包括 AX3600、RT-ACRH17（官方 RT-AC42U 定义中列为别名）、Redmi AX6S、JCG Q30 Pro。Q30 不并入 Pro 结论。按设备定义存在和驱动依赖给出证据，未将其写成四个型号已完成真机抓包。

已用当前执行入口核对用户要求：电脑经 2.4G 登录，5G 有上级连接时，不仅因上级占用而拒绝 5G。当前 SSH 路径是目标网卡时则拒绝，路径不明也不猜测。严格“仅用空闲网卡”模式仍有自己的准入限制，不等同于普通接管模式。

Linux 官方文档明确：支持监听不代表与 AP/STA 并发一定完整可用，也不代表可以任意注入、更换地址或跨越信道约束。上次已标出的批量 MAC 硬件约束仍是待真机验证边界。

## 第三轮：异常、恢复、原包与日志

对照官方数据格式和当前代码，注入非规范但合法的布尔值、原包拆分失败、接口添加超时。已复现 F1/F2/F3。当前日志、导出、任务恢复和取消边界测试 55 次通过。日志能留下命令超时和错误位置，但不能代替恢复凭据，更不能找回已删除的原包。

另复用原项目的 10 组离线测试，实际重新执行 224 项，并检查全部生产模块来自当前 RouterManager。其中 4 组未通过，不计为全绿，未修改旧测试使其通过：

- capture_basics：旧断言不期待新的异步管理路径探测，且没有提供当前脱敏函数的字符串返回值。
- capture_cleanup：旧测试监听 winbox.capture，当前日志名称为 routermanager.capture。
- config_takeover：旧模拟没有提供管理路径探测结果，被当前前置检查拒绝。
- openwrt_portability：模拟没有提供当前所需安装大小元数据；部分断言把 apk add --simulate 的只读试算也当成实际安装。

三轮当前定向测试共 219 次通过，计数含重叠覆盖。上述旧套件单独记录，不以旧项目历史通过数代替本次结果。

## 资料和证据

- [Linux wireless modes](https://wireless.docs.kernel.org/en/latest/en/users/documentation/modes.html)
- [Linux wireless hardware constraints](https://docs.kernel.org/driver-api/80211/cfg80211.html)
- [UCI boolean parser](https://github.com/openwrt/uci/blob/master/blob.c)
- [OpenWrt boolean helper](https://github.com/openwrt/openwrt/blob/openwrt-23.05/package/base-files/files/lib/functions.sh)
- [hcxdumptool 6.2.4](https://github.com/ZerBea/hcxdumptool/blob/6.2.4/hcxdumptool.c)
- [hcxdumptool 6.3.4](https://github.com/ZerBea/hcxdumptool/blob/6.3.4/hcxdumptool.c)
- [hcxdumptool 6.3.5](https://github.com/ZerBea/hcxdumptool/blob/6.3.5/hcxdumptool.c)
- [airodump 1.5.2](https://github.com/aircrack-ng/aircrack-ng/blob/1.5.2/src/airodump-ng.c)
- [airodump 1.6](https://github.com/aircrack-ng/aircrack-ng/blob/1.6/src/airodump-ng/airodump-ng.c)
- [airodump 1.7](https://github.com/aircrack-ng/aircrack-ng/blob/1.7/src/airodump-ng/airodump-ng.c)

证据目录：`build/settings-capture-audit-20261025/`。总结为 verification.json，复现脚本为 reproduce_findings.py，结果为 findings.json。source-manifest.json 包含 62 次源码查询的链接和哈希，其中 3 个 404 是 airodump 新旧目录差异，均已找到对应文件。硬件来源另存于 round2-hardware/sources/manifest.json。


## 修复与验证（2026-10-25）

- F1：新增公共 UCI 布尔解析，分别遵循 netifd 和系统 shell 函数的语义。同步开机策略、LAN 地址分配判断和编辑窗口开关回显，避免 false 被误显示为开启。
- F2：拆分、解析失败保留原包。实际后台统一持有下载对象，等目标结果处理和保存确认成功后才回收原包和已处理的临时文件，避免改成永久缓存。
- F3：创建前检查名称未被占用，并先登记待确认记录。接口创建成功后在内核 ifalias 属性中写入本任务的唯一归属标记。删除、重新启用前核对网卡和标记；重建同步保持记录。超时和取消不再丢失凭据，不删除已被替换或无法核实归属的接口。

无法确认远端创建是否完成、标记缺失或归属不符时，保留待核实状态并停止后续任务。不以“暂时没看到接口”宣称不确定的创建已安全恢复；需重新登录核实，仍不明时需保留现场处理，不无限重试或盲删。

兼容依据：[Linux 4.14 ifalias ABI](https://github.com/torvalds/linux/blob/v4.14/Documentation/ABI/testing/sysfs-class-net) 和 [Linux 6.6 ABI](https://github.com/torvalds/linux/blob/v6.6/Documentation/ABI/testing/sysfs-class-net)，该属性从 Linux 2.6.28 即存在，覆盖支持清单所用内核范围。不依赖 CPU 特定二进制或新增路由器软件包。没有改动界面样式。

最终当前项目回归 153 项通过，包含 18 项新的专项测试；真实 POSIX 脚本覆盖超时确已创建、取消、归属不符、接口重建和未确认状态保留。复用的 109 项旧核心测试中，仅一条仍要求“分析后立即删除原包”的旧断言未通过；其与本次保留原包的修复目标相冲突，未修改原 WinBox 项目文件。当前项目已补充新的原包保留、实际双目标交接和成功后回收测试。未将旧套件写成全绿。

修复记录：`build/settings-capture-fix-20261025/verification.json`，精确变更：`changes.diff`。未对真实路由器写入，不宣称所有芯片已真机验证。
