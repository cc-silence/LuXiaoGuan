# wydev 分支选择性整合说明（2026-11-04）

## 目的与固定版本

用户确认：11 月 3 日修复后的前台自动抓包已经通过用户实测。本次以该行为为保护基线，接入可独立验证的改进，不把文件能自动合并等同于运行行为没有变化。

- 主线基线：`8d5406e0b6a042682035de535f52246e9bd8561f`。
- 评审的 wydev 提交：`dab896cdc99c3c003060ae62c4edd22200120eff`。
- 本次代码整合提交：`599a2061e73eb5240f85586cd8e0b52e3cb9c945`。
- 合并申请：[PR #34](https://github.com/cc-silence/LuXiaoGuan/pull/34)。

这是选择性移植，不代表整个 wydev 分支均被采纳。原分支不修改，未接入部分可以后续单独复核。结论仅针对上述提交；分支继续变化后应重新比较。

## 为什么不直接整体合并

### 1. 双层重试：已复现执行次数变化，不等于已证明重复写入

主线的 `OpenWrtCommandRunner.run()` 已对 Channel closed 保留一次重试。wydev 又在 `OpenWrtSshClient.exec_command()` 的内部 `open_command()` 增加一次重试。两层嵌套后，同一次请求可能从最多两次变成最多四次。

使用相同的本机真实 SSH 测试服务：

- 主线观察到：`reject → reject → accept`。
- wydev 观察到：`reject → reject → reject → reject → accept`。

测试：`tests.test_ssh_channel_isolation.ChannelIsolationTests.test_exec_rejection_keeps_login_and_next_independent_command_works`。

需要区分：测试明确证明次数变化；该测试模拟的是执行请求拒绝，没有证明路由器已经重复修改了配置。但在真实连接中，通道异常不自动等于命令从未执行，因此不能在未分析执行阶段、幂等性和总超时前，默认四次更安全。

用户曾要求保留重试。本次保留主线原有重试，不额外叠加第二层。若后续决定接受四次，应明确调整合同并补上执行前失败、执行后丢回复、取消、资源释放和超时预算测试，而不是仅修改次数断言。

### 2. 整条命令加锁：已复现并发取消回归

wydev 在 `command_runner.py` 中新增 `_command_lock`，锁包住整个 `_run()` 和重试，不只是通道打开。等待长命令结果和输出期间，同一 runner 的其他短命令也会等待。获取该锁本身没有独立的超时或可取消等待。

测试：`tests.test_ssh_channel_isolation.ChannelIsolationTests.test_cancelling_one_channel_does_not_close_other_channel_or_login`。

该测试先发出等待中的命令，再执行另一条独立命令，随后取消前者。主线通过；wydev 的等待命令未得到预期取消结果，断言失败。不能据此宣称发生永久死锁或所有抓包都会失败，但原有并发和取消语义确实改变了。

本次保留主线实现。若要串行化，应说明必须保护的共享资源，限定临界区，并验证等待时间、取消和连接变化，不能仅凭 Channel closed 的文字推断整条命令必须串行。

### 3. 两套占位网卡管理：代码风险已确认，昨天同一报错尚未真机复现

主线 `capture_placeholders.py` 已统一处理占位网卡。删除前检查应用标记、实际 PHY，并结合当前连接和会话登记验证所有者。

wydev 在 `auto_capture_service.py` 新增：

- `_build_sweep_stale_script()`：按名字拼出 hcxstub，执行 down/delete，缺少上述标记和实际 PHY 核实。
- `_build_ensure_phy_stubs_script()`：新增一条创建与登记路径。

新创建调用之后仍有主线的 `prepare_placeholders(self)`；新增调用也没有放进原公共创建锁。这里应检查双网卡争用、删除后的所有者记录，以及停止、重试和恢复的一致性。

这是重复实现及保护边界差异，不是“已经证明会重现 failed to arm interface”。本次继续使用主线公共实现，不带入第二套管理。用户此前接受的 hcxmon 名称清理例外，也不应自动扩大为任何 hcxstub 的无条件所有权证明。

## 本次实际接入

1. 改进 hcx 旧版识别：识别工具名称后的版本及无版本帮助选项；不把任意驱动版本数字当成 hcx 版本。帮助为空时使用有界的版本查询。
2. 旧版信道参数清洗为空时，不生成无效的空 -c；原来的默认信道策略保留。
3. 后台脚本创建接口时显式使用 `iw phy <实际 PHY> interface add ...`，脚本版本为 v26.11.4。

前台并非完全没有修改：`_probe_dialect()` 和 `_build_attempt_commands()` 两个方法发生变化。已比较现代命令分支的语法树，保持不变；其余会话方法与基线原文一致。

逐字节核对保持不变的模块包括：SSH runner/client、占位管理、启动协调、接管与恢复、设备探测、业务占用、配置事务、后台启动管理器和自动抓包页面。

后台新控制台、启动输出重定向、日志裁剪、仅按固件判断兼容性的文案及其他配置变化本次暂缓。暂缓不代表全部已证明错误，而是没有必要与本次小修正绑定；尤其不能从能安装工具推导为驱动一定支持抓包。

## 验证与复核入口

评审原 wydev 快照：7 个模块共 83 项测试；其中 SSH 模块 8 项有上述 2 项失败，其余所选模块通过。主线同一 SSH 模块 8 项全部通过。

选择性整合后：10 个模块共 119 项定向测试通过，包含启动、重试状态隔离、旧 worker 退出、双网卡协调、占位清理、恢复、下载及 SSH 边界。新增 `tests/test_safe_hcx_integration.py` 覆盖版本识别、空信道、现代参数隔离和实际 shell 中的后台 phy 命令成功/失败。

可在两个独立检出目录，用相同环境对两个固定提交分别运行：

```text
python -m unittest tests.test_ssh_channel_isolation -v
```

当前整合代码可运行：

```text
python -m unittest tests.test_safe_hcx_integration tests.test_capture_initialization_evidence tests.test_capture_start_preflight tests.test_capture_integration_safety tests.test_capture_takeover_lifecycle tests.test_ssh_channel_isolation tests.test_auto_capture_feature tests.test_radio_usage_feedback tests.test_auto_capture_cleanup tests.test_boot_script_identity -v
```

Windows 的 shell 测试应使用 Git Bash，避免将 WSL shell 与 Windows 临时路径混用。完整仓库检查仍以 PR 的 CI 为准，不用上述定向通过冒充全仓库通过。

## 官方资料与边界

- [hcxdumptool 6.2.4 源码](https://raw.githubusercontent.com/ZerBea/hcxdumptool/6.2.4/hcxdumptool.c)：核对 -o 和版本查询。
- [hcxdumptool 6.3.4 源码](https://raw.githubusercontent.com/ZerBea/hcxdumptool/6.3.4/hcxdumptool.c)：核对 -w 和版本查询。
- iw：从官方 OpenWrt 19.07.10、21.02.7、22.03.7、23.05.5、24.10.0、25.12.0 的 Makefile 解析对应版本或固定提交，再读取官方 iw 的 interface.c，核对 CIB_PHY 的接口创建注册。精确来源和校验值见 [来源清单](wydev-safe-sources-20261104.json)。

这些是组件源码核对和离线/本机 SSH 验证。没有在本轮操作真实路由器，没有重新进行用户现场验收，也没有证明所有小版本、型号及 Debian 图形环境都已实测。用户报告昨天已经通过的现场结果，作为应保护的事实保留。

请复核方独立判断：若能证明某项风险有额外的身份、阶段或资源保证，请指出具体调用链，并在相同提交与环境复现；不要仅凭测试名称、注释或文件无冲突作结论。

补充验证：`test_download_socket_failure_keeps_error_state_and_removes_partial_file` 模拟下载已写入部分数据后发生 Socket is closed，确认最终状态为 error、原始异常仍保留、本地半成品删除且不发送 success 提示。该测试不连接真实路由器。
