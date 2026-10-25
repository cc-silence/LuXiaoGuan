# 常用设置三轮兼容性与潜在问题审计


> 修复更新（2026-10-25）：下述 F1/F2/F3 已按用户授权修复。最终 131 项相关回归通过，包含 14 项新增专项测试；仍未执行真机写入。下文保留修复前审计与复现记录，不代表问题仍未处理。

日期：2026-10-25。结论：尚不能宣称所有支持系统和硬件上的常用设置全部兼容。三轮定向回归共 197 项通过，另有 3 类未被原测试覆盖的问题已离线复现。本次未修改功能代码，未向真实路由器写入配置。

## 三轮核查

1. 系统与官方接口：重新联网下载 19.07、21.02、22.03、23.05、24.10、25.12 六个系列的源码。156 个路径查询中 126 个存在，30 个为新旧目录差异对应的 404，无传输错误。另下载 12 个固定提交的 rpcd/fstools 包源码记录，重建 90 条带哈希的系统契约。配置、密码、备份、重置与策略回归 67 项通过。
2. 硬件与执行边界：对支持清单中 15 个平台所属的 12 个目标家族，查阅六个代表发行版的官方目标定义，并核对四个型号的设备定义。92 个源码路径中 76 个存在，16 个为旧版尚未有该目标或目录已改名的 404，无传输错误。清理、共享身份、批量编辑及 LAN 应用回归 71 项通过，使用真实 Lua 事务与模拟设备。
3. 异常与界面：注入提交超时、LAN 不自启动和自定义防火墙区域条件，复现下述问题。真实 Qt 界面、工作线程、取消、连接切换、确认和进度回归 59 项通过。

发行版代表样本：19.07.10、21.02.7、22.03.7、23.05.5、24.10.0、25.12.0。没有逐一重新下载清单中所有小版本的全部依赖包，不将代表样本核查写成全组合实机认证。

## 修复前的问题复现记录

### F1 / P1：关闭开机连接上级时，漏检 LAN 的开机启动状态

- 触发：LAN 仍是静态地址，地址分配服务开启，但 `network.lan.auto=0`。
- 观察：策略快照没有返回 `auto`，关闭上级自启动的预览仍通过。下次启动可能同时没有可用的 LAN 管理地址和上级连接；不等于所有设备必然断联。
- 代码：`router_policy_commands.py:66`；`router_policy_plan.py:285-296`。
- 依据：已从官方 19.07.10 和 25.12.0 指定的 netifd 提交中核对 `interface.c`，`auto` 决定 `autostart`。建议在公共快照返回该字段，关闭前核对独立管理入口。

### F2 / P1：LAN/主机名时区保存超时后仍销毁会话

- 触发：`common_commit` 已发出，但因超时、传输中断或响应不完整无法确认结果。
- 观察：两类设置均依次调用 `session -> common_stage -> common_commit -> discard_session`。官方 rpcd 销毁会话时会清除对应临时配置目录，因此存在干扰远端未结束保存的竞争风险。未在真机上实验破坏性时序，不宣称已证实数据损坏。
- 代码：`common_settings.py:98-107`。对比同项目 `router_policy.py:130` 已有“提交结果不确定时不销毁会话”的处理，应统一事务收尾策略。

### F3 / P2：自定义上级区域可能漏掉网页防护

- 触发：上级 `wwan` 放在 `uplink_custom` 区域，系统中仍有另一个 `wan` 区域。
- 观察：初始安全设置的“阻止上级打开管理网页”预览仅生成 `src=wan` 规则，不覆盖真正的上级区域。同一处组装逻辑也用于 ping/IGMP 规则。
- 代码：`router_policy_plan.py:223-240`。这是自定义网络布局的适配缺口，不是默认网络布局下的普遍失效。当前界面要求用户确认初始状态，但没有技术校验这一声明。建议按实际关联生成规则，或在不支持的布局下明确阻止此项执行。

## 系统与硬件覆盖

| 平台 | 核查结果和限制 |
| --- | --- |
| mediatek/filogic | 查阅目标与设备定义；JCG Q30 Pro 在 23.05.5/24.10.0/25.12.0 的样本中均有定义。旧系列不能生硬套用。 |
| mediatek/mt7622 | Redmi AX6S 在 22.03.7 及以后的代表样本中有定义。 |
| ramips/mt7621 | 目标定义已查阅；无线和交换机仍按实际驱动与配置判断。 |
| ipq40xx/generic | ASUS RT-ACRH17 对应官方 asus_rt-ac42u 定义，22.03.7 及以后代表样本已核对。 |
| ipq806x/generic | 目标定义已查阅；不将架构相同等同于无线 MAC 能力相同。 |
| ipq807x/generic、qualcommax/ipq807x | Xiaomi AX3600 在 23.05.5/24.10.0/25.12.0 的样本中均有定义，目录在系列间变更。 |
| ath79/generic、ath79/nand | 目标定义已查阅；须区分存储布局及小内存设备资源。 |
| bcm53xx/generic | 目标定义已查阅；不保证所有博通无线驱动都接受随机 MAC。 |
| bcm27xx/bcm2711 | 目标定义已查阅；网卡和存储形式与传统路由器不能混同。 |
| mvebu/cortexa9、mvebu/cortexa53 | 目标定义已查阅；无线功能仍需按具体驱动判断。 |
| sunxi/cortexa53 | 目标定义已查阅；是否具备无线硬件不能仅凭 CPU 推断。 |
| x86/64 | 基础配置功能按组件检测；没有无线网卡时不能使用热点相关功能；ext4 可写根文件系统不能使用当前标准清空覆盖层的重置路径。 |

以上是官方源码与本地回归的覆盖，不是所有芯片、型号和驱动的真机认证。JCG Q30 仍需读取实际固件信息，未并入 Q30 Pro 结论。

## 功能适用性

- 密码、主机名、时区：主要依赖系统组件，没有 CPU 特定二进制编码；需要注意 F2。
- LAN、搬家、遗留接口：已跑新旧网络配置回归，仍依赖正确读取当前布局和占用状态。
- 开机连接策略：六系列启动顺序和脚本契约已核对，仍需修复 F1。
- 批量无线身份：已验证配置保存与共享恢复逻辑，但实际无线 MAC 是否被驱动接受需真机检查；批量路径不会在重启后自动证实发射地址。
- 初始化热点：会检查能力、频段和并发接口数量，并在删除旧默认热点前检查新热点实际状态。无线硬件缺失、无 2.4G 或并发容量不足时会停止，这不是全硬件适用的功能。
- 备份恢复、重置：已验证原生 sysupgrade 备份/恢复接口和重置工具改名；归档格式合法不等于跨型号或跨固件备份可恢复，现有界面已要求使用本机相同固件的可信备份。
- 离线依赖：只对 `docs/offline-packages/support.json` 列出的精确组合有包覆盖依据，不将未提供或缺依赖的组合当作支持。

## 官方依据和复现文件

- [OpenWrt 网络配置](https://openwrt.org/docs/guide-user/network/network_configuration)
- [OpenWrt 无线配置](https://openwrt.org/docs/guide-user/network/wifi/basic)
- [OpenWrt 重置与恢复限制](https://openwrt.org/docs/guide-user/troubleshooting/failsafe_and_factory_reset)
- [官方 rpcd 会话销毁处理](https://github.com/openwrt/rpcd/blob/d99f703e4035d03fbcfbada3cb697db346c2b984/uci.c#L1760)
- [JCG Q30 Pro 设备定义](https://github.com/openwrt/openwrt/blob/v23.05.5/target/linux/mediatek/image/filogic.mk)
- [Redmi AX6S 设备定义](https://github.com/openwrt/openwrt/blob/v22.03.7/target/linux/mediatek/image/mt7622.mk)
- [ASUS RT-ACRH17 设备资料](https://openwrt.org/toh/hwdata/asus/asus_rt-acrh17)
- [Xiaomi AX3600 设备定义](https://github.com/openwrt/openwrt/blob/v23.05.5/target/linux/ipq807x/image/generic.mk)

完整源码链接与哈希、离线复现脚本、结果均保存在 `build/common-settings-three-rounds-20261025/`：

- `sources/manifest.json`、`sources/pinned-packages.json`
- `official-policy-contracts.json`
- `hardware/manifest.json`
- `netifd/manifest.json`
- `probe_findings.py`、`findings-reproduced.json`

有些官方 Wiki 页面直接打开时返回反自动化验证页。本次不将验证页当资料使用，型号与实现结论主要依据成功下载的官方源码。不对衍生固件作支持承诺。

## 修复后的行为

- F1：公共策略快照返回 LAN 的 `auto` 字段，关闭上级开机自动连接前检查 LAN 自启动；未开启时明确提示原因并停止，不暗中修改 LAN。
- F2：LAN 和主机名/时区共用保存路径，提交结果不确定或回滚未确认时保留会话，不重试提交。成功、提交前取消、已确认回滚的失败仍正常清理。
- F3：新增 `upstream_firewall_zones.py` 统一解析上级区域，结合标准 WAN、上级 Wi-Fi 关联和运行时默认路由，按实际区域生成网页/ping/IGMP 规则。支持 UCI 列表、旧式空格分隔值和同名默认关联，多上级区域分别覆盖，重复执行不新增重复规则。不属于任何区域、多重归属、混用本地热点网络或含无法确认的物理设备/子网/额外匹配时，停止该项，防止误阻本地管理。

修复不依赖 CPU 型号，未新增路由器二进制组件，沿用六系列已核对的 UCI/netifd/rpcd 路径。官方依据补充：[防火墙区域与规则](https://openwrt.org/docs/guide-user/firewall/firewall_configuration)、[网络 auto 选项](https://openwrt.org/docs/guide-user/network/network_configuration)。实际第三方固件和所有硬件真机行为仍不在本次已验证结论内。
