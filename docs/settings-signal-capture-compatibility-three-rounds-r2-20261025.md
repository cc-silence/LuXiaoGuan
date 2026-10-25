# 常用设置、信号管理、跑包：追加三轮复核

日期：2026-10-25。承接上轮报告，本次重新执行资料核查、调用链审阅、失败复现和定向回归。没有连接或写入真实路由器。

## 新发现与修复

1. 临时接口初次启用缺少归属复核。创建和写入归属标记后，若接口被外部操作替换，首次 ip link up 仍可能使用它；恢复、重新启用和重建路径已有检查。新增真实 POSIX 脚本用例，分别替换归属和 phy，修复前失败、修复后通过。初次启用现在复用公共 owner_guard，归属不符时拒绝继续，恢复记录保留，不删除已被替换的接口。正常创建、超时恢复与重连清理保持通过。
2. 遗留进程清理匹配不严，强制终止前未复核启动身份。旧逻辑对命令行做工具名称子串匹配，因此 tail 读取 hcxdumptool.log 也可能被匹配；TERM 后仅检查进程目录存在，PID 已被复用时仍可能继续 KILL。假 procfs 和信号记录已复现这两种情况。脚本移入独立 stale_processes.py 构造器，精确检查首参数的工具名称，保留已结束会话目录范围，并在 TERM 前、等待期间和 KILL 前复核启动标记与首参数。未知工具、日志读取进程、其他会话和身份发生变化的进程不继续处理；正常遗留工具仍按原顺序停止。未对真实本机进程发信号。
3. 进程存活检查重复读取空间。公共 _identity_running 中完全重复拼接了两次资源预算命令，每次执行两次 df/du。实际 shell 计数从两组降为一组；阈值、错误码、调用频率和停止策略不变，没有新增 timer。

没有修改界面布局、主题、默认参数或设备依赖。只调整公共执行层；五个本轮变更文件通过语法、UTF-8、乱码和换行检查，git diff --check 通过。

## 本次三轮

| 轮次 | 核查范围 | 结果 |
| --- | --- | --- |
| 一 | 常用设置、保存回读、策略、新旧网络布局、维护能力、备份、重启和密码 | 114 项通过 |
| 二 | 六系列无线契约、父网卡、字段保留、开关格式、管理连接、扫描、删除、暂存和保存回滚 | 159 项通过 |
| 三 | 跑包接口与进程归属、超时/取消、重连恢复、原包保留、诊断及依赖兼容边界 | 84 项通过 |

本次共 357 项，无跳过。没有把上轮 309 项累加充当本次结果。补跑原项目的 capture_resources 13 项、capture_transport_failure 9 项也通过，并核对全部生产模块来自当前 RouterManager。原项目仅提供这两组测试，不加载其业务实现。上轮另外两组旧测试的差异仍保留在前次报告，没有修改旧断言并宣称全仓库通过。

常用设置和信号参数的本次新增审阅没有发现其他明确缺陷，不为凑数量修改代码。已核对真实服务入口：普通设置通过 ConnectionBoundRunner 绑定原连接；维护操作有设备、配置、权限和存储预检；信号事务保留 revision、回读、确认与回滚检查。资料不能替代真实设备的恢复结果。

## 系统与硬件依据

本次重新请求 166 个官方路径，最终取得 150 份内容、16 个 404，无剩余传输错误；已有哈希的固定来源无变化。首次抓取中有临时超时，原始记录保留在 initial-fetch.json，之后只补取失败项。统计按本次实际文件和 HTTP 结果生成，不继承旧记录中的成功状态。

核查六系列：19.07、21.02、22.03、23.05、24.10、25.12。包括固定提交的无线 shell/ucode 配置、维护和重启契约，以及各系列 BusyBox 的 ash/tr/sed/cut 默认启用情况。新清理脚本使用已有标准工具，不依赖新增二进制、不按 CPU 架构分支；精简固件仍以实际组件为准。

- [19.07 sysupgrade](https://github.com/openwrt/openwrt/blob/openwrt-19.07/package/base-files/files/sbin/sysupgrade)：备份/恢复选项及帮助退出状态需要按真实输出处理。
- [25.12 sysupgrade](https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/sbin/sysupgrade)：使用对应固定来源和现有原生能力探测，不按旧命令猜测。
- [19.07 BusyBox 默认配置](https://github.com/openwrt/openwrt/blob/openwrt-19.07/package/utils/busybox/Config-defaults.in)、[25.12 BusyBox 默认配置](https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/utils/busybox/Config-defaults.in)：新脚本用到的标准小工具在六系列配方中均启用。
- [Linux proc 文档](https://www.kernel.org/doc/html/latest/filesystems/proc.html)：进程参数和启动时间的来源及进程编号复用边界；本次同时保存 Linux 4.14 和 6.6 的对应源码文档。
- [Linux 4.14 ifalias](https://github.com/torvalds/linux/blob/v4.14/Documentation/ABI/testing/sysfs-class-net)、[Linux 6.6 ifalias](https://github.com/torvalds/linux/blob/v6.6/Documentation/ABI/testing/sysfs-class-net)：继续使用既有网络接口归属属性，不新增驱动要求。

平台/型号范围与前次报告一致，本次再次核对该范围的官方路径：
- mediatek/filogic、mediatek/mt7622、ramips/mt7621；
- ipq40xx/generic、ipq806x/generic、ipq807x/generic、qualcommax/ipq807x；
- ath79/generic、ath79/nand、bcm53xx/generic；
- bcm27xx/bcm2711、mvebu/cortexa9、mvebu/cortexa53、sunxi/cortexa53、x86/64。
- AX3600、RT-ACRH17（官方 asus_rt-ac42u 别名）、Redmi AX6S、JCG Q30 Pro；Q30 未并入 Pro。

逐平台的实际无线驱动、存储和资源限制仍沿用动态探测，不将官方不存在的版本/目标组合标为支持。新修复属于共用接口和进程管理，不引入按型号选择命令的分支。官方型号定义存在，不代表该型号监听、并发或注入已真机认证。

## 交付边界与记录

本机完成 Windows 离线验证；没有 Debian 12 实机环境，也没有进行路由器真机测试。内核/驱动的监听和并发能力、DFS、实际断线恢复、低空间条件仍需对应授权设备验证。OpenWrt 19.07/21.02 旧 hcxdumptool 的既有 PMKID 限制未改变，不能把“常规握手可用”写成“所有模式可用”。

当前修改仅在源码中，未重打 1.0.17_alpha 测试包，未发布。记录位于 build/compatibility-three-rounds-20261025-r2/：三轮日志、round-results.json、core/、sources/、encoding.json 和 verification.json。
