# 依赖流程资料复核与 KWrt 用户日志（2026-10-26）

## 用户日志结论
输入为用户提供的 LuXiaoGuan_路由器登录_20261026_204442.zip。程序版本1.0.20_alpha；设备 Redmi Router AX6000，Kwrt 24.10-SNAPSHOT，revision 05.14.2025，mediatek/filogic，aarch64_cortex-a53，opkg，kernel6.6.89。
实际缺项只有wpa-cli。未匹配本地官方离线包后，dependency_update（opkg update）运行约7.219秒，退出码6；随后电脑下载因normalize_profile的SNAPSHOT限制终止。没有进入dependency_package_plan或dependency_install步骤，因此不能归因于包安装预演、单项按钮或卸载逻辑。

日志只包含联网/DNS分类摘要，没有原生软件源输出。diagnostic-index.json只登记operations和signals，没有package-manager.log。不能根据退出码6确定具体域名、DNS、404或TLS原因。已请求用户提供路由器网页“更新列表”的完整报错。

## 确认并修正的问题
统一install_with_sources原来在网络错误回退时保留online异常的package_diagnostic，但computer再次失败后直接抛出后一个异常，导致服务无法保存前一个异常的原生输出。
公共入口现在合并两条线路的失败信息与脱敏诊断，每段最多8192字符；SignalError保留最后阶段code，取消与其他异常保留原类型、对象和语义。不改变线路顺序、重试次数、代理/直连、安装包选择、默认值、卸载或固件身份核验。

此改动修复诊断丢失，不等同于修复用户设备的软件源连接；旧日志已丢失的原文无法重建。没有以猜测替换固件源，也没有把衍生固件标记为官方离线包适配通过。

## 对新增分组与操作的资料核对
- 官方hostapd Makefile中wpa-cli是wpa_supplicant命令行客户端；实际程序名wpa_cli、包名wpa-cli。分到常用工具并从抓包确认范围排除正确。
- 官方aircrack-ng Makefile将airmon-ng分包；本软件通过已有iw能力管理无线接口，不因包名相似额外安装airmon-ng。
- 官方reaver包同时安装reaver和wash。此次分组复用已有功能根包定义，没有另造安装命令或改变抓包实现。
- opkg源码在非强制卸载时检查反向依赖，支持noaction预演；apk del可顺带清除不再需要的包，不能假定只删一个。现有预演拒绝清单外删除，具名包删除后复查；没有为实现单项按钮添加force或autoremove。
- 新按钮继续进入同一服务和管理器；常用批量不混入抓包功能，抓包单项刷新共用功能状态。未发现需修改的新分组或按钮执行错误。

## 来源
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-24.10/package/network/services/hostapd/Makefile
- https://raw.githubusercontent.com/openwrt/packages/openwrt-24.10/net/aircrack-ng/Makefile
- https://raw.githubusercontent.com/openwrt/packages/openwrt-24.10/net/reaver/Makefile
- https://raw.githubusercontent.com/openwrt/opkg-lede/master/libopkg/opkg_cmd.c （opkg_update_cmd返回累计failures，并非固定DNS错误枚举）
- https://raw.githubusercontent.com/openwrt/opkg-lede/master/libopkg/opkg_remove.c
- https://raw.githubusercontent.com/openwrt/opkg-lede/master/src/opkg-cl.c
- https://raw.githubusercontent.com/alpinelinux/apk-tools/v3.0.0/doc/apk-del.8.scd
- https://github.com/kiddin9/Kwrt
- https://raw.githubusercontent.com/kiddin9/Kwrt/master/devices/common/diy.sh （当前源码用于确认存在组件差异，不能替代用户2025年固件的实际软件源配置）

## 兼容性和验证边界
公共异常交接不依赖CPU、无线驱动或型号，不改变19.07/21.02/22.03/23.05/24.10/25.12的包管理器适配。现有精确版本、架构和型号范围以support.json为准；本次不会通过降低身份检查扩展离线支持。opkg/apk路径继续由真实能力探测选择，相关回归覆盖两种格式。Kwrt不是本项目官方OpenWrt兼容清单的已验收固件。
Windows运行定向离线回归；新增测试覆盖SNAPSHOT回退失败、双诊断、长度限制、脱敏、取消、其他异常以及服务导出。没有连接用户路由器；Debian12没有实机环境，本次公共纯Python逻辑无新增平台分支，不宣称Linux真机通过。没有打包、发布或改动依赖资源。

最终定向回归130项通过，50.165秒，进程退出码0。Python语法、UTF-8与乱码扫描、git diff --check通过。测试中的清理告警来自故意注入的失败样例，不是用户路由器的新结果。
