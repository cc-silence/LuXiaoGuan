# 依赖安装与卸载兼容性核对（2026-10-25）

本轮发现并修复 4 类问题。241 项定向回归通过；未连接真路由器执行安装或卸载。

## 修复的问题

1. opkg 的已安装清单可以带软件描述。之前安装解析可能把描述混入版本，检测和上轮卸载解析也可能拒绝正常清单。现在共用 package_inventory.py，同时兼容无描述、完整描述和空描述。
2. 信号依赖安装还有固定 1MB 空间门槛，可能误拦小包。已取消固定门槛，仍按完整安装计划、安装体积和安全预留检查空间。
3. 电脑代下载信号依赖时，旧路径依赖 SFTP。现在 IPK 和 APK 共用上传器，SFTP 不可用时回退到 SSH 命令分片传输，不强求先装 SFTP 。上传前检查取消状态。
4. APK 卸载预演可能返回 OK，但因为依赖关系保留目标包。现在必须看到目标包的删除计划才执行真实卸载，避免包没删却改动 world 安装保留记录。

## 系统范围

逐一获取 50 个精确小版本的官方构建文件，实际对应 7 种 opkg 和 2 种 APK 源码修订。包管理器仍按设备实际探针选择，不靠版本号猜测。

| 系统 | 包管理器 | 抓包离线组合 | 常用离线组合 |
|---|---|---:|---:|
| 19.07 | opkg | 121 | 36 |
| 21.02 | opkg | 95 | 95 |
| 22.03 | opkg | 96 | 96 |
| 23.05 | opkg | 98 | 98 |
| 24.10 | opkg | 126 | 126 |
| 25.12 | APK | 98 | 98 |

小版本范围：19.07.0–19.07.10、21.02.0–21.02.7、22.03.0–22.03.7、23.05.0–23.05.6、24.10.0–24.10.8、25.12.0–25.12.6。这不表示每个版本都有所有硬件组合。

## 平台与型号

两个当前离线包的全部 1,183 个清单组合与 support.json 一致；共校验 3,640 个存储对象的长度和 SHA-256，并校验两份 ZIP 的完整哈希。用清单中的真实包名和版本验证预演解析，不等于在路由器安装过。

| 平台 | 已登记架构 |
|---|---|
| ath79/generic | mips_24kc |
| ath79/nand | mips_24kc |
| bcm27xx/bcm2711 | aarch64_cortex-a72 |
| bcm53xx/generic | arm_cortex-a9 |
| ipq40xx/generic | arm_cortex-a7_neon-vfpv4 |
| ipq806x/generic | arm_cortex-a15_neon-vfpv4 |
| ipq807x/generic | aarch64_cortex-a53 |
| mediatek/filogic | aarch64_cortex-a53 |
| mediatek/mt7622 | aarch64_cortex-a53 |
| mvebu/cortexa53 | aarch64_cortex-a53 |
| mvebu/cortexa9 | arm_cortex-a9_vfpv3, arm_cortex-a9_vfpv3-d16 |
| qualcommax/ipq807x | aarch64_cortex-a53 |
| ramips/mt7621 | mipsel_24kc |
| sunxi/cortexa53 | aarch64_cortex-a53 |
| x86/64 | x86_64 |

对所有 15 个平台重新读取官方首尾样本的 30 份 config.buildinfo。精简构建配置可能省略默认子平台和派生架构字段，不用字段缺失推断不兼容。运行时架构以设备包管理器输出为准。

| 指定型号 | 官方镜像设备名 | 结论 |
|---|---|---|
| Xiaomi AX3600 | xiaomi_ax3600 | 首尾镜像样本与架构匹配，未真机 |
| ASUS RT-ACRH17 | asus_rt-ac42u | 官方 titles 明确包含 RT-ACRH17，未真机 |
| Redmi AX6S | xiaomi_redmi-router-ax6s | 首尾镜像样本与架构匹配，未真机 |
| JCG Q30 Pro | jcg_q30-pro | 首尾镜像样本与架构匹配，未真机 |
| JCG Q30 | 未确认 | 不与 Q30 Pro 混同 |

## 保留的限制

- 抓包包有 50 个上游未提供的组合；常用包另有 85 个依赖不可用的组合，均保留原清单记录。19.07.3–19.07.7 不在当前常用离线包的有效组合内；19.07.0–19.07.2 也有部分平台缺依赖。不强制替换基础库补齐。
- 安装路线保持为已下载离线包、路由器联网、电脑代下载。仅明确联网失败才换路，签名或依赖错误不通过换路绕过。
- 卸载仍是逐包处理，遇到占用或错误会停止，不会自动装回已卸的包。跳过占用后继续清理其他包并非本轮新增行为。
- APK 会自动清理孤立依赖；计划包含未确认的包时仍拒绝执行。不使用强制选项。
- 软件包记录只能识别已登记依赖，无法保证自写脚本或未登记的插件不使用该包。不根据现有包记录猜测是谁安装的。
- 本轮安装卸载没有新增驱动、内核或网络配置改动；swconfig/DSA、firewall3/4 不改变包命令选择。依赖安装成功不代表无线驱动支持所有抓包功能，原清单 216 个 hcxdumptool 运行时未适配组合仍然保留。
- 读取清单有输出上限和超时，上传可取消、传输失败可报错、临时目录清理独立收尾。未在页面打开时新增探测或无界缓存。

## 官方依据与本地证据

- [OpenWrt 19.07.0 opkg build](https://raw.githubusercontent.com/openwrt/openwrt/v19.07.0/package/system/opkg/Makefile)
- [opkg native print_pkg](https://github.com/openwrt/opkg-lede/blob/dcbc142e51f5f5f2fb9e4e44657e013d3c36a52b/libopkg/opkg_cmd.c)
- [OpenWrt 25.12.0 APK build](https://raw.githubusercontent.com/openwrt/openwrt/v25.12.0/package/system/apk/Makefile)
- [APK native delete and world handling](https://github.com/alpinelinux/apk-tools/blob/d093f7c198a64bff0cd58afeaf638909fda24ca8/src/app_del.c)
- [APK native info formatting](https://github.com/alpinelinux/apk-tools/blob/d093f7c198a64bff0cd58afeaf638909fda24ca8/src/app_info.c)
- [ASUS 22.03 official image metadata](https://downloads.openwrt.org/releases/22.03.0/targets/ipq40xx/generic/profiles.json)
- [ASUS 25.12 official image metadata](https://downloads.openwrt.org/releases/25.12.6/targets/ipq40xx/generic/profiles.json)
- Exact release/source URLs and SHA-256: `build/dependency-compatibility-20261025/version-sources.json`, `native-contracts.json`.
- All platform/model URLs and hashes: `build/dependency-compatibility-20261025/platform-model-sources.json`.
- Archive/object checks: `build/dependency-compatibility-20261025/bundle-audit.json`.
- Final counts and validation limits: `build/dependency-compatibility-20261025/summary.json`.
