# OpenWrt 全功能兼容性复查（2026-10-29）

范围：官方 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12，按支持清单展开为 50 个精确小版本。不把系列分支的最新代码当作该系列的所有旧版本。表格按实际共用执行路径分组，同路径的按钮不重复算为不同实现。

## 已确认的结果

- 21.02.0、21.02.1、21.02.2 的 hostapd 状态接口缺少 SSID（热点名称）；原软件会误判未生效并回滚。公共核验已修复：必须已启动，并由同一实际接口的 iwinfo 名称和频率交叉确认。显式返回错误名称、禁用或通信失败时不降级判成功。
- 开机策略恢复历史标记导致信号被自动启用的副作用已修正，现在保留所有当前开关。这是软件逻辑问题，不是路由器性能结论。
- 用户确认清理遗留接口后是开关本身变禁用。该问题仍未定位：离线清理测试保留了无线配置和运行状态，原日志没有该次操作的完整记录。需要更新日志，不标记为已修复。
- 215 个隔离测试模块无失败；1832 次测试执行中有 1 项需要真实 Windows 显示后端而跳过。此外新增 4 项精确版本回归通过，覆盖全部 50 个返回格式。数量包含模块导入的复用用例，不是真机通过数。

## 明确的功能限制

19.07/21.02 的部分官方 hcxdumptool 不在当前已适配的 API 范围，支持清单共记录 216 个这类组合，PMKID 会被阻止启动。不从其他系统串包、不强换驱动。其他条件包括：SFTP 需服务组件；桥接需实际驱动能力；非标准存储可能无法通过普通恢复出厂入口；不存在安全通用的按键救援限制方式。

## 逐功能网页资料与实现对照

下表的结论是资料和执行路径核对，不等于所有设备已验收。细节条件保留原始技术名词，便于后续维护核对。

| 功能 | 共用入口 | 官方网页资料 | 条件和边界 |
| --- | --- | --- | --- |
| 登录、主机指纹、重连 | `modules\capture_services\openwrt\ssh_client.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/dropbear) | Host-key confirmation and password/key authentication; no router-model shortcut. |
| 设备信息与网卡对应 | `modules\capture_services\openwrt\device_probe.py` | [source 1](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Actual iwinfo phy ownership; never assume radio0 means 2.4 GHz. |
| 在线依赖安装 | `modules\capture_services\openwrt\dependency_manager.py` | [source 1](https://openwrt.org/docs/guide-user/additional-software/opkg) / [source 2](https://openwrt.org/docs/guide-user/additional-software/apk) | Actual package-manager detection; native simulated plan and dependency closure; no forced upgrades. |
| 离线依赖安装 | `modules\capture_services\openwrt\common_bundle_installer.py` | [source 1](https://openwrt.org/docs/guide-user/additional-software/opkg) / [source 2](https://openwrt.org/docs/guide-user/additional-software/apk) | Exact release + target + package architecture; inventory exclusions retained; package availability is not feature support. |
| 兼容工具与卸载 | `modules\capture_services\openwrt\compatible_tool.py` | [source 1](https://openwrt.org/docs/guide-user/additional-software/opkg) / [source 2](https://openwrt.org/docs/guide-user/additional-software/apk) | Tool API and package identity checked; protected system/driver packages are not replaced. |
| 修改登录密码 | `modules\capture_services\openwrt\router_password.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/dropbear) | Native passwd prompts over encrypted stdin; separate pinned new-password verification. |
| 主机名与时区 | `modules\capture_services\openwrt\common_settings.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/system_configuration) | Saved values and actual kernel timezone/hostname are distinct checks. |
| LAN 保存、应用、强制应用 | `modules\capture_services\openwrt\checked_apply.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c) | Old ifname/type bridge and new device/ports layouts; apply/confirm/rollback are capability gated. |
| 自动校时 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/advanced/ntp_configuration) | Existing sysntpd/timeserver required; internet/time server availability is a runtime condition. |
| 管理网页中文 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/luci/luci.essentials) | Installed translation and actual LuCI configuration required; no guessed package installation. |
| 仅允许 LAN 登录 SSH | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/dropbear) | Interface=lan is common; DirectInterface priority is removed only if present. |
| 限制上级网页访问 | `modules\capture_services\openwrt\upstream_firewall_zones.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) / [source 2](https://openwrt.org/docs/guide-user/services/webserver/uhttpd) | Actual fw3/fw4 engine and unique upstream zones; shared LAN zones are refused. |
| 上级 Ping 与 IGMP 规则 | `modules\capture_services\openwrt\upstream_firewall_zones.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) | Logical zone rules rather than hardcoded physical ports or version-only DSA assumptions. |
| HTTP / HTTPS 设置 | `modules\capture_services\openwrt\router_web_config.py` | [source 1](https://openwrt.org/docs/guide-user/services/webserver/uhttpd) | Actual uhttpd listener and LAN management address; custom roots and ambiguous servers are protected. |
| 按键救援限制 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/troubleshooting/failsafe_and_factory_reset) | No safe general implementation: currently skipped, not advertised as successful. |
| DHCP 日志 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) | quietdhcp exists in all 50 reviewed dnsmasq init scripts; actual daemon reload still matters. |
| 磁盘自动接入 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/storage/fstab) | auto_mount changes hotplug behavior; no unmount or deletion of existing storage. |
| 匿名交换分区 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/storage/fstab) | anon_swap is distinct from explicitly configured swap; existing explicit configuration retained. |
| 升级辅助服务 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/uci) | Attended-upgrade service only when present; does not disable all update mechanisms. |
| 替换出厂热点 | `modules\capture_services\openwrt\router_policy.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Create and verify the replacement before deleting confirmed default AP sections; capacity checked on actual radio. |
| 路由器名称与 LAN MAC | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) / [source 2](https://openwrt.org/docs/guide-user/base-system/system_configuration) | Existing logical bridge required; physical port/radio identity not rewritten. |
| 默认打开的管理网页 | `modules\capture_services\openwrt\router_web_config.py` | [source 1](https://openwrt.org/docs/guide-user/services/webserver/uhttpd) / [source 2](https://openwrt.org/docs/guide-user/luci/luci.essentials) | index_page precedence and installed route checked; no visible review required for this local preference. |
| 系统日志参数 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/system_configuration) | Cron defaults differ between old/new BusyBox; numeric reference setting is not a claim of equal log volume. |
| 批量更换上级身份 | `modules\capture_services\openwrt\station_identity_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://openwrt.org/docs/guide-user/network/network_configuration) | Shared DHCP hostname ownership validated; current switches and credentials preserved. |
| 批量更换自建 Wi-Fi 和地址 | `modules\capture_services\openwrt\wireless_capabilities.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Fixed early 21.02.0/1/2 status lacking SSID; same-interface live name/frequency cross-check; explicit bad state remains failure. |
| 开机连接策略 | `modules\capture_services\openwrt\router_policy_plan.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/uci) / [source 2](https://openwrt.org/docs/guide-user/network/wifi/basic) | Fixed old-marker restoration side effect; changing startup policy leaves current switches intact. |
| 信号列表与开关 | `modules\capture_services\openwrt\signal_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Revision-bound changes and radio ownership; configured disabled is distinct from inactive runtime. |
| 信号扫描 | `modules\capture_services\openwrt\wireless_scan_results.py` | [source 1](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Old scan replies may omit frequency; only unique actual-radio channel-table mapping is allowed. |
| 编辑自建热点 | `modules\capture_services\openwrt\wireless_setup_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) | Actual get_validate schema; reject unsupported options; 25.12 ucode preserves interface validation shape. |
| 桥接上级、WDS、relay | `modules\capture_services\openwrt\wireless_bridge_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://openwrt.org/docs/guide-user/network/network_configuration) | Actual driver/relayd capability, DHCP ownership and backup; no blanket promise of WDS across chips. |
| 启用上级与父网卡 | `modules\capture_services\openwrt\wireless_bridge_toggle.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c) | Only explicitly selected interface/radio; managed bridge DHCP restoration remains under same transaction. |
| 删除信号 | `modules\capture_services\openwrt\wireless_delete_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c) | Known owner/reference checks; shared or uncertain network sections retained. |
| 遗留接口检查清理 | `modules\capture_services\openwrt\orphan_interface_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) / [source 2](https://openwrt.org/docs/guide-user/base-system/uci) | Offline mixed-switch test preserves wireless bytes/runtime, no reload; reported real disabled flip still unresolved. |
| 搬家清理 | `modules\capture_services\openwrt\relocation_commands.py` | [source 1](https://openwrt.org/docs/guide-user/network/network_configuration) / [source 2](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c) | Verified session/fingerprint, native rollback and original-device reconnection; only owned configuration removed. |
| 待提交配置 | `modules\capture_services\openwrt\save_only_commands.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/uci) | Direct isolated libuci commit avoids applying unrelated pending values and config.change reloads. |
| 客户端、流量、上网状态 | `modules\capture_services\openwrt\wireless_status_commands.py` | [source 1](https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/iwinfo.c) / [source 2](https://openwrt.org/docs/guide-user/network/network_configuration) | Association/traffic/internet are distinct evidence; missing driver information stays unknown. |
| 文件列表、上传、下载 | `modules\capture_services\openwrt\router_management.py` | [source 1](https://docs.paramiko.org/en/stable/api/sftp.html) / [source 2](https://openwrt.org/docs/guide-user/base-system/dropbear) | Dropbear alone does not provide SFTP; openssh-sftp-server required; streaming/bounded transfers. |
| 文件删除、改名、权限 | `modules\capture_services\openwrt\router_file_delete.py` | [source 1](https://docs.paramiko.org/en/stable/api/sftp.html) | SFTP semantics and object identity, symlink and cancellation guards. |
| 路由器终端 | `modules\capture_services\router_terminal_service.py` | [source 1](https://raw.githubusercontent.com/tmux/tmux/3.0a/tmux.1) / [source 2](https://openwrt.org/docs/guide-user/base-system/dropbear) | PTY on SSH; tmux optional persistent history; desktop Qt DLL compatibility is separate from OpenWrt. |
| 常规握手抓包 | `modules\capture_services\openwrt\handshake_capture.py` | [source 1](https://github.com/aircrack-ng/aircrack-ng) / [source 2](https://openwrt.org/docs/guide-user/network/wifi/basic) | Actual monitor/capacity/channel and tool capability required; driver injection cannot be certified offline. |
| 指定信道抓包 | `modules\capture_services\openwrt\channel_capture.py` | [source 1](https://github.com/aircrack-ng/aircrack-ng) / [source 2](https://openwrt.org/docs/guide-user/network/wifi/basic) | Classic PCAP framing and supported real channel; bounded artifact handling. |
| PMKID 抓包 | `modules\capture_services\openwrt\pmkid_capture.py` | [source 1](https://github.com/ZerBea/hcxdumptool) / [source 2](https://openwrt.org/docs/guide-user/network/wifi/basic) | Existing whitelist only 6.2.4/6.3.4/6.3.5; inventory records 216 unadapted old-version profiles in 19.07/21.02. |
| WPS / PIN | `modules\capture_services\openwrt\wps_capture.py` | [source 1](https://github.com/t6x/reaver-wps-fork-t6x) / [source 2](https://openwrt.org/docs/guide-user/network/wifi/basic) | Reaver arguments, WPS support/lock and driver capability required; no successful PIN/PSK outcome promised; hardware acceptance pending. |
| 猜密码与配置恢复 | `modules\capture_services\openwrt\password_verification.py` | [source 1](https://openwrt.org/docs/guide-user/network/wifi/basic) / [source 2](https://openwrt.org/docs/guide-user/base-system/uci) | Temporary configuration and restoration are bounded; no live state-changing verification performed. |
| 配置备份、手动恢复 | `modules\capture_services\openwrt\router_backup.py` | [source 1](https://raw.githubusercontent.com/openwrt/openwrt/v19.07.0/package/base-files/files/sbin/sysupgrade) | All 50 sysupgrade scripts expose create/restore backup; archive integrity/board/storage are separately checked. |
| 恢复出厂 | `modules\capture_services\openwrt\router_backup.py` | [source 1](https://openwrt.org/docs/guide-user/troubleshooting/failsafe_and_factory_reset) | jffs2reset/factoryreset actual detection and supported squashfs-overlay layout; ext4/nonstandard layouts may refuse. |
| 重启路由器 | `modules\capture_services\openwrt\router_reboot.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/uci) | Reboot command completion is not proof of completed boot; must reconnect and identify same device. |
| 重新获取电脑网络地址 | `modules\platform_services\lan_network.py` | [source 1](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/renewdhcplease-method-in-class-win32-networkadapterconfiguration) / [source 2](https://manpages.debian.org/bookworm/network-manager/nmcli.1.en.html) | Desktop capability only; disabled throughout active router transaction; static interfaces not silently rewritten. |
| 导出日志与诊断 | `modules\capture_services\openwrt\snapshot_bundle.py` | [source 1](https://openwrt.org/docs/guide-user/base-system/uci) | Bounded metadata export, redaction and task provenance; latest snapshot cannot prove prior switch transitions. |

## 版本和源码记录

`build/compatibility-audit-20261029/sources/manifest.json` 记录了 900 份精确版本服务源码的 URL、SHA-256 和下载结果。还跟随了 14 个 rpcd、11 个 netifd、7 个 UCI、9 个 fstools 实际固定提交。文件改名与布局变化的 404 记录不算成功证据：fstools 新旧重置文件互为替代；25.12 无线实现转入 wifi-scripts 的 ucode，已另行下载核对 get_validate/status、参数 schema 与配置返回结构。

可重现下载入口：`python scripts/audit_openwrt_release_contracts.py`。固定回归样例：`tests/fixtures/official_hostapd_exact_releases.json`，数值为根据官方字段生成的离线样例，不是真机实测报文。

## 硬件与平台

对支持清单中的 15 种 target/subtarget 保留精确版本与架构对应，不作版本与平台的全组合假设。官方 profiles.json 查询 77 个代表性组合：56 个存在，21 个不存在，没有传输错误。记录在 `hardware-profiles.json`，含每条官方下载 URL、哈希与实际型号标识。

- AX3600：已查的 23.05 是 ipq807x/generic，24.10/25.12 是 qualcommax/ipq807x。
- RT-ACRH17：已查的 22.03 及以后在 ipq40xx/generic，官方实际标识 asus_rt-ac42u。
- AX6S：已查的 22.03 及以后在 mediatek/mt7622。
- Q30 Pro：本次已查的 23.05.6 及以后有 jcg_q30-pro，不把 Q30 当作 Q30 Pro。
- TL-WDR7500 v3 与两款 AC2100：已查的清单从 21.02.0 开始有对应型号，不代表 19.07 也有官方型号适配。

硬件清单证据只证明官方固件与型号对应，不证明无线监听、注入、WDS、复位或低内存环境都已真机验收。

本次未修改路由器、未释放或重置真实网卡、未打包发布。Windows 下执行的离线测试不等于 Debian 12 实机验证；本机 WSL 查询未得到可用的 Debian 环境，该平台仍待验证。

## Additional direct implementation checks

Password modification: all six BusyBox versions used by the 50-release inventory were checked against upstream passwd.c: 1.30.1, 1.33.1, 1.33.2, 1.35.0, 1.36.1, 1.37.0. New-password/retype prompts match the shared implementation. The 1.37.0 mirror tag was unavailable; the upstream tarball was fetched instead and its SHA-256 matched OpenWrt v25.12.0 PKG_HASH before extracting only passwd.c in memory. Evidence: sources/password-sources.json. The initial failed mirror lookup remains recorded and is not counted as verified evidence.

Firewall engine implementation references: https://raw.githubusercontent.com/openwrt/firewall3/master/options.h and https://raw.githubusercontent.com/openwrt/firewall4/master/root/usr/share/firewall4/templates/ruleset.uc . These supplement the exact-release service/configuration review, rather than substituting master for an old firmware. 25.12 network.wireless get_validate maps iface to interface and returns string descriptors; status retains up/pending/config/interfaces. The corresponding new ucode implementations were checked directly, including their schema source, and do not require a new product version branch.

After the two previously confirmed fixes, this broader audit did not establish another new deterministic product defect. That is a review result, not a promise of zero remaining bugs. The unresolved real orphan-cleanup switch transition and physical driver/storage verification remain explicitly open.
