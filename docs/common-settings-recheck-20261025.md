# 常用设置再次三轮复核（2026-10-25）

> 更正：本文的三轮是三组定向回归，不是全平台完整兼容性验收。后续资料核查又确认三处遗漏，见 [深入核查报告](common-settings-compatibility-deep-20261025.md)。

结果：三轮共 209 次定向检查通过（各轮含重叠用例），最终导出与回调复查 7 项通过。未向真实路由器写入。

## 发现与修复

- 功能回归：上次日志包装只接受一个回调参数，而搬家、遗留清理传入“消息和级别”两个参数，导致实际执行失败。现在公共包装原样转发参数和返回值，不保留消息原文。已测试真实 Lua 清理执行器和 Qt 工作线程。
- 日志缺口：清理线程启动失败时补齐最终失败记录；保留“新状态未确认”原生错误码；补充预览项数、清理数量、修改地址数量。
- 未改动界面视觉效果，未增加路由器依赖或按照 CPU 区分的执行分支。

## 三轮范围

1. 核心功能与事务：LAN、主机名时区、初始策略、开机策略、批量身份、搬家和遗留清理，98 项通过。
2. 系统契约与异常路径：密码、备份重置能力、界面、线程、连接切换，65 项通过。
3. 日志与导出：失败注入、隐私、有界保留、备份恢复重启、登录页真实 ZIP 导出，46 项通过。

## 系统与硬件证据

重新联网下载官方 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12 六系列源码：156 个路径中 126 个存在，30 个为新旧目录差异，无传输错误。另核对 12 个固定提交的 rpcd/fstools 包记录，重建 90 条源码契约，与现有测试样本一致。

硬件按支持清单的 15 个平台、12 个目标家族和 AX3600、RT-ACRH17、Redmi AX6S、JCG Q30 Pro 核对：92 个官方源码路径中 76 个存在，16 个为旧版不存在或目录差异，无传输错误。未将平台与版本作全组合支持推断。

详细平台和功能限制见 [前次审计](common-settings-compatibility-audit-20261025.md)：无线芯片驱动、存储布局和实际固件组件仍决定能力；源码核对不代表所有硬件已真机认证。

## 日志结论和限制

登录页可导出常用设置日志，即使信息框已清空。实际 ZIP 已核对保存未确认错误、清理数量、未应用状态和隐私脱敏。详见 [日志说明](common-settings-diagnostics.md)。

日志仍为本次运行的有界内存记录，请出错后先导出再退出；不保证仅凭日志就能确定每种硬件故障。

## 官方资料与复核记录

- [OpenWrt network configuration](https://openwrt.org/docs/guide-user/network/network_configuration)
- [OpenWrt reset and storage limitations](https://openwrt.org/docs/guide-user/troubleshooting/failsafe_and_factory_reset)
- [Official device definitions](https://github.com/openwrt/openwrt/blob/v23.05.5/target/linux/mediatek/image/filogic.mk)

全部来源链接、哈希、测试记录与导出样本保存于 `build/common-settings-recheck-20261025/`，总记录为 `verification.json`。
