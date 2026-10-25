# 常用设置、信号管理、跑包：三轮兼容复核

日期：2026-10-25。本轮在 Windows 源码版完成资料、执行链和离线回归检查；没有连接或写入真实路由器。修复在当前源码中，先前交付的 1.0.17_alpha ZIP 未重新打包。

## 本轮发现并修复

1. 信号和管理入口的布尔格式不一致。官方 netifd 接受 0/1、true/false，但部分代码只识别 0/1，导致合法配置被拒绝、已停用接口被计入并发数量、已禁用 LAN 被当成管理入口。复现后统一 Python 与设备端 Lua 的 netifd 布尔解析，覆盖信号清单、父网卡启用、接管、信号保存和常用设置策略。保留原始配置；只有实际修改的开关写入原有规范值。未知写法仍拒绝，不将 shell 的 yes/on 语义套给 netifd。
2. 开机脚本把 disabled=true 的上级误登记为“软件停用”。以后重新允许开机连接时，可能将原本手动停用的上级启用。真实 shell 已复现并修复。新版脚本仅处理明确启用的 STA，保留已停用项；用新旧脚本的完整 SHA-256 统一识别，外部改动仍拒绝。旧版策略需用户再次保存“停用开机上级连接”后才更新，不在登录时自动改写。旧脚本哈希：0d0341acafb04ca4cc54241613cc4d4b19444dfd95f37b73b0907e97a4a36508。
3. 重新登录仍存在只凭 wbmon 名称前缀删除接口的旧入口，不能证明接口属于已结束任务。已移除该入口及调用，保留公共 ReconnectCleanup：先核对设备身份，停止登记的进程，再按任务记录和接口归属恢复，最后清理对应目录。未知接口和其他会话接口不再按前缀删除；无记录的旧残留不会假装已清理，需人工核实。本轮未增加自动轮询、路由器依赖或界面样式。

## 三轮和验证

| 轮次 | 核对重点 | 修复后定向测试 |
| --- | --- | --- |
| 一 | 常用设置：旧 ifname/type 与新 device 配置、保存回读、策略、防火墙区域、管理入口 | 73 项通过 |
| 二 | 信号管理：六系列原生契约，25.12 shell/ucode，父网卡、频段归属、扫描、字段保留、合法布尔格式 | 149 项通过 |
| 三 | 跑包与异常：任务恢复、临时接口归属、断线与取消、原包保留、管理连接和诊断 | 87 项通过 |

共 309 项，均无跳过；不是 309 台设备或全硬件认证。12 个本轮变更文件通过 py_compile、UTF-8 和混合换行检查，git diff --check 通过。没有改界面布局或主题，因此未声称新增视觉验收。源码有改动，未自动重打包。

另外复用原项目六组核心测试，但强制所有生产模块来自当前 RouterManager：
- hcx_capabilities、hcx_filter、capture_resources、capture_transport_failure 四组共 60 项全部通过。
- channel_capture 未全绿：仍有一条要求“分析后立即删除原包”的旧断言，与现在“结果交接和保存确认后回收”的规则冲突；当前原包保留专项测试通过。
- idle_radio 未全绿：旧模拟返回 tuple 而当前需要带能力的计划对象，未提供接口名称预检的 absent 回应、归属标记或管理连接证据。失败详情单独保留，未改原项目测试或削弱正式检查迁就它们，也不把这两组计为通过。

## 官方资料和系统差异

本轮重新下载主证据 164 个路径：148 个存在，16 个是旧版没有目标或目录变更，没有网络错误；所有带旧哈希的固定来源均一致。另下载 12 个各系列 UCI/netifd 布尔消费者、21 个工具配方和上游源码。原文、链接、哈希保留在本轮 sources 目录；对当前 PMKID 生成参数和 airodump 选项进行了 11 项源码存在性核查，不能以选项存在代替实际收包或发送行为验证。

| 系统 | 必须保留的差异和限制 |
| --- | --- |
| 19.07 | opkg、旧网络绑定和 hwmode；旧 hostapd 状态接口走受限兼容路径；官方 hcxdumptool 6.0.1 不满足当前 PMKID 接收过滤契约，保留常规握手抓包。 |
| 21.02 | opkg，网络结构随平台过渡，不能一律当 DSA；官方 hcxdumptool 6.0.1 同上。 |
| 22.03 | opkg、firewall4；官方工具 6.2.4 使用独立参数契约，主动 PMKID 请求未适配。 |
| 23.05 | opkg；无线运行态、驱动和实际功能探测决定路径；6.2.4 与 6.3.x 不能混用参数。 |
| 24.10 | opkg、firewall4；设备/网卡能力仍动态读取，不能仅凭版本承诺 monitor 或并发。 |
| 25.12 | apk；分别核对 shell 与 ucode 无线实现。新版 netifd 使用 examples/utils.uc 的 parse_bool，不能硬找已经迁移的 wireless.c。 |

通用配置逻辑使用原生 UCI/ubus；需要 Lua、libuci-lua、libubus-lua、luci-lib-jsonc 等实际组件、权限和能力探测。组件缺失并不表示硬件不支持；没有能力的驱动也不会因装上工具就具备能力。常用设置按实际服务、网络结构和存储探测；信号字段按 get_validate 结果开放；跑包按当前工具版本与帮助信息准入，不以固件年份替代探测。

当前 PMKID 被动路径明确适配 hcxdumptool 6.2.4/6.3.4/6.3.5；主动目标请求仅适配 6.3.4。未改这些既有能力边界，也没有把不存在的平台与版本组合推算成支持。官方软件配方本轮对应 airodump/aircrack-ng 1.5.2、1.6、1.7；命令选项存在性与当前版本准入分别核对。精确小版本、离线包可用性仍以 docs/offline-packages/support.json 为准；本轮没有增加或发布离线包。

官方入口：
- [19.07 原生无线配置](https://github.com/openwrt/openwrt/blob/openwrt-19.07/package/kernel/mac80211/files/lib/netifd/wireless/mac80211.sh)
- [25.12 shell 无线路径](https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/network/config/wifi-scripts/files/lib/netifd/wireless/mac80211.sh)
- [UCI 布尔消费者](https://github.com/openwrt/uci/blob/master/blob.c)
- [25.12 固定 netifd 布尔解析](https://github.com/openwrt/netifd/blob/cbb83a1857407a28a63dc09412a1f209195914ef/examples/utils.uc)
- [内核接口归属属性](https://github.com/torvalds/linux/blob/v4.14/Documentation/ABI/testing/sysfs-class-net)
- [Linux 无线模式限制](https://wireless.docs.kernel.org/en/latest/en/users/documentation/modes.html)
- [hcxdumptool 6.3.4](https://github.com/ZerBea/hcxdumptool/blob/6.3.4/hcxdumptool.c)

## 平台、型号和真机边界

| 平台范围 | 本轮核查与实际限制 |
| --- | --- |
| mediatek/filogic、mediatek/mt7622、ramips/mt7621 | 官方目标与设备定义存在性、mt76 配置路径；监听、并发、地址数量和注入取决于具体驱动与固件。 |
| ipq40xx/generic、ipq806x/generic、ipq807x/generic、qualcommax/ipq807x | 核对目标变更及设备依赖；旧目标不存在或目录迁移不等于相同硬件已通过验收。 |
| ath79/generic、ath79/nand | 目标和对应无线工具接口；闪存、内存和具体网卡能力仍需实际探测。 |
| bcm53xx/generic | 目标存在不保证具体无线驱动有监听能力。 |
| bcm27xx/bcm2711、mvebu/cortexa9、mvebu/cortexa53、sunxi/cortexa53、x86/64 | 读取实际网卡和存储能力；没有无线硬件时不适用无线功能，不根据 CPU 架构推导网卡能力。 |

本轮重新提取 AX3600（3 份定义）、RT-ACRH17（4 份；官方 asus_rt-ac42u 定义中的明确别名）、Redmi AX6S（4 份）、JCG Q30 Pro（3 份）的源码证据。JCG Q30 仍待实际固件确认，不并入 Pro。定义证据不等于四个型号全部真机通过。

真机待验收：各实际固件的保存重开、当前管理信号编辑后断线恢复、不同驱动监听/并发/注入、DFS 信道与区域限制、取消后恢复业务、低空间设备。应在已授权设备上先备份，分别验证；不能以本轮离线回归替代。Debian 12 桌面环境未验证；本轮新增共用代码不引入 Windows 专用依赖，但不宣称 Debian 运行通过。

## 可复核记录

build/compatibility-three-rounds-20261025/ 中保存 sources/manifest.json、boolean-contracts.json、tool-sources.json、tool-contracts.json、model-evidence.json、round-results.json、三轮逐项日志、core/ 的旧核心测试结果及 encoding.json。所有核查均针对当前源码，不将前轮历史通过数计入本轮。
