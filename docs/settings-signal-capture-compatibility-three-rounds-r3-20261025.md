# 常用设置、信号管理、跑包：第三次追加三轮复核

日期：2026-10-25。当前源码、本机 Windows 离线验证；未操作真实路由器，未重打包或发布。

## 本轮确认并修复的两处问题

### 1. 初始化热点仍漏掉父网卡 disabled=true

常用设置“替换默认热点”中，创建新热点时只检查父网卡 disabled=1，未识别同样合法的 true。因此父网卡没有被开启，新的热点无法正常发射。保存后的无线配置比对也存在同样的 0/1 假设。

真实 Lua 配置提交已复现；旧、新网络布局均可触发。修复统一使用既有 netifd_bool，一次解析同时用于创建和保存后比对。已经启用的 None/0/false 原值保留；未知状态在提交前拒绝。测试确认原默认热点保留、已有 Wi-Fi 密码未改、新热点配置存在，没有新增自动重试或默认策略。

### 2. 恢复自检受 UCI 配置段顺序影响

恢复自检解析 uci show wireless 时，把属性写入“上一条信号声明”，未核对属性自己的完整段名。合法配置中，wifi-device 放在 wifi-iface 后面时，网卡的 disabled/MAC 会覆盖前一条信号的字段，导致本应提示的恢复异常漏报。

已验证三段配置的六种排列，并通过实际 audit_restore 入口复现：原本配置停用、但仍运行的 home 信号不再得到提示。现在按完整段名归属字段，后续网卡属性不会进入信号记录。

这是只读恢复诊断问题；没有证据表明这个解析器本身会修改配置。修复不会自动启停无线，也不以它代替实际恢复动作。

## 三轮验证

| 轮次 | 检查内容 | 本轮通过数 |
| --- | --- | --- |
| 一 | 常用设置、初始化热点、开机策略、保存回显、维护/备份/重启与密码 | 117 |
| 二 | 六系列无线契约、父网卡、原参数保留、扫描、暂存、保存/删除/回滚和管理连接 | 159 |
| 三 | 跑包接管、恢复自检、进程与接口归属、取消断线、原包保留和依赖边界 | 86 |

共 362 项，均无跳过。不累加前两次的通过数，也不把这看成所有芯片真机认证。四个本轮变更文件通过 Python 语法、UTF-8、乱码及混合换行检查，git diff --check 通过。

第二、三轮同时回查前次修复：新旧开机脚本精确识别、临时接口初次启用、进程 PID 变化、日志读取进程不被误停、空间预算只读取一次、原包失败保留。没有发现需要进一步调整的明确缺陷。未为达到问题数量而改动其他代码，未改界面视觉、默认值或路由器依赖。

## 本轮官方资料依据

重新请求 179 个官方路径：163 份内容取得并核对 SHA-256，16 个为旧版不存在的平台/目录，零剩余传输错误；带已有哈希的固定来源没有变化。记录包含六系列原生无线、维护/重启、BusyBox 默认工具、Linux ABI、UCI/netifd 布尔消费者，以及新增的六系列 UCI 命令行输出源码。

六系列为 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12。25.12 同时检查 shell/ucode 原生路径。UCI 的布尔转换接受 0/1 和 true/false；原生 uci_show_package 按配置段链表遍历，uci_show_option 输出完整“配置.段名.字段名”。没有“所有网卡声明一定在所有信号之前”的保证，不能以常见配置顺序作为解析前提。

官方来源：
- [UCI 布尔解析](https://github.com/openwrt/uci/blob/master/blob.c)
- [19.07 固定 UCI 输出实现](https://github.com/openwrt/uci/blob/415f9e48436d29f612348f58f546b3ad8d74ac38/cli.c)
- [25.12 固定 UCI 输出实现](https://github.com/openwrt/uci/blob/66127cd76c5d0bd46d5a90302cc6110f53a4e2f8/cli.c)
- [25.12 固定 netifd 布尔解析](https://github.com/openwrt/netifd/blob/cbb83a1857407a28a63dc09412a1f209195914ef/examples/utils.uc)
- 其他四系列精确提交、完整链接和哈希在本轮 sources/manifest.json；六系列逐项输出契约在 sources/uci-show-contracts.json。

## 平台与设备边界

仍按支持清单范围核查，未把平台与版本强行作全组合：
- mediatek/filogic、mediatek/mt7622、ramips/mt7621；
- ipq40xx/generic、ipq806x/generic、ipq807x/generic、qualcommax/ipq807x；
- ath79/generic、ath79/nand、bcm53xx/generic；
- bcm27xx/bcm2711、mvebu/cortexa9、mvebu/cortexa53、sunxi/cortexa53、x86/64。
- 重点型号 AX3600、RT-ACRH17（官方 asus_rt-ac42u 定义的别名）、Redmi AX6S、JCG Q30 Pro；Q30 仍不等于 Q30 Pro。

这两处修复属于共用配置语义和文本解析，不依赖 CPU 指令或新增驱动。硬件定义来源本轮重新取得，实际监听、并发、注入能力和存储条件仍由设备探测及真机结果决定。官方定义存在不代表该型号已通过所有功能验收。

OpenWrt 19.07/21.02 的旧 hcxdumptool PMKID 限制、其他工具版本准入、无无线硬件或不具备监听能力时的限制没有变化。本轮没有更改或发布离线依赖包，精确覆盖仍以 docs/offline-packages/support.json 为准。

未进行 Debian 12 实机验证；共用 Python 修复没有新增 Windows 专用接口，不宣称双平台运行通过。路由器实际发射、断线恢复、DFS 与驱动行为仍需在对应授权设备上验证。

## 复核记录

build/compatibility-three-rounds-20261025-r3/：
- before.json：本轮修改前摘要；
- round-results.json 和三轮逐项日志；
- sources/manifest.json、sources/uci-show-contracts.json 和官方原文；
- encoding.json、verification.json。

本轮新增测试为 tests/test_router_policy_boolean_edges.py、tests/test_radio_restore_order.py；修复前已观察到失败，修复后与相关原有用例一并通过。
