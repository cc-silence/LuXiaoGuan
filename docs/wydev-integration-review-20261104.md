# wydev 分支整合说明与复核方法（2026-11-04 更新）

Latest integration status: [2026-11-05](main-integration-20261105.md).

## 当前决定：请先读这里

本文件已根据负责人后续决定更新，不能再按 PR #34 刚合并时的“暂缓”清单判断当前代码。

- 原始主线基线：`8d5406e0b6a042682035de535f52246e9bd8561f`。
- 本轮评审与移植的 wydev 提交：`dab896cdc99c3c003060ae62c4edd22200120eff`。
- 第一轮选择性整合：[PR #34](https://github.com/cc-silence/LuXiaoGuan/pull/34)，主线提交 `81f8b81f16c4a6df77c2e15181db6748edb4c8cd`。
- 负责人后来明确要求接入三组内容：后台控制台、输出重定向和日志裁剪；整条 SSH 命令加锁；旧系统旧工具的兼容性提示策略。这三组现已接入本轮代码，同时补齐下面的收尾与验证。

这是选择性移植，未整体合并 wydev 的全部提交。原分支没有修改。

## 当前已接入

1. hcx 新旧方言探测、无版本帮助回退、空信道参数处理；现代抓包参数继续保持原主线策略。
2. 后台创建监听接口时显式指定实际 PHY：`iw phy <实际 PHY> interface add ...`。
3. 后台“监看终端”和收起按钮、启动日志返回、ANSI 仪表盘最新帧显示，以及可见时复用已有巡检读取日志。收起窗口不停止远端抓包。
4. 手动启动采用 `VIEW=on`，输出写入 `/tmp/.hcx_autocap.stdout.log`，优先读取这个输出；没有输出时回退到持久会话日志。开机服务仍使用静默模式和原有启动方式。
5. 日志定期裁剪、旧版 hcx 的 `--enable_status=1`。官方 6.2.4 帮助中，1 是 EAPOL 状态输出；这不是关闭抓包或减少原始数据保存。
6. 同一个命令 runner 中，整条短命令及原有一次重试串行执行。负责人明确接受独立命令排队的影响。
7. 旧系统已安装官方旧版工具时，依赖列表显示命令可用，不再仅凭旧版标成需要升级；页面只在公共规则判定支持现代工具且仍安装旧工具时提醒升级。这里的“可用”指依赖命令已安装，不是无线驱动或实际抓包已验收。

## 接入时完善的处理

### 整条命令锁保留，并补上排队边界

保留 wydev 的 RLock 设计；没有再次取消串行化。等待锁时可以取消，也会计入命令的超时预算。取消或排队超时的命令不会提交给路由器；执行结束或异常都会释放锁。

原先测试让“另一条命令先完成，再取消长命令”，这个顺序在串行策略下不成立。现在验证的是：长命令运行时其他命令确实排队；取消长命令后排队命令继续成功，登录连接保留。另有排队取消、排队超时和异常释放测试。没有仅通过改掉失败断言掩盖取消问题。

这把锁作用于同一个 runner，不代表跨多个 SSH 客户端、终端或其他管理工具的配置事务锁。也没有保证能消除所有 Channel closed。

### 后台启动和日志容量

- 先检查脚本是否已运行，再清空新一轮的输出文件，避免重复点击启动丢掉现有日志。
- 后台子进程的标准输入仍接到 /dev/null；输出使用追加模式。否则日志被原地截短后，旧文件偏移可能造成空洞或再次膨胀。
- 以字节预算保留文件头尾，避免按固定行数裁剪时被超长行绕过。每路运行日志和会话日志默认 256 KiB，标准输出为 64 KiB。
- 这是约 30 秒一次的检查和退出前收尾，不是拦截每一次写入的绝对硬上限。两次检查之间仍可能超出阈值；截短时也不能保证保留每一条并发写入的日志。
- 单次读取同时限制字节数和行数；重复日志读取请求合并，旧登录连接的结果不向新连接发布，避免命令串行后无界堆积读取线程。
- 脚本版本更新为 v26.11.4.1，便于已部署用户识别本次日志行为变化。

## 仍未接入的内容

- SSH 客户端内部新增的第二层重试：主线 runner 已有一次，继续保留；本轮没有叠加内部第二次重试。
- 第二套占位网卡创建与按名字清理逻辑，以及仅凭接口名字将网卡认作本任务所有。
- 无线配置接管事务的其他改动、空闲网卡判断变化。本轮没有修改这些配置写入或网卡归属规则；它们不是“兼容性提示”的同一项。

“暂缓”不等于判定全部错误；这些应分别评审，不与已批准的日志、锁和提示策略混为一谈。

## 原先为什么暂缓：保留可复核证据

原 wydev 快照的 7 个模块共 83 项测试中，SSH 模块有两项不符合原主线合同；同环境的原主线 SSH 8 项全过：

- `test_exec_rejection_keeps_login_and_next_independent_command_works`：主线观察到 reject → reject → accept；原 wydev 因双层重试变成四次 reject 后 accept。证明了尝试次数变化，没有证明已经发生重复配置写入。
- 原 `test_cancelling_one_channel_does_not_close_other_channel_or_login`：整条命令锁改变独立命令完成与取消的顺序。负责人现已接受串行策略，本轮按新合同验证停止释放、排队取消和超时。
- 占位网卡的两套管理属于静态调用链风险，没有在本轮真机复现昨天的同一故障；不应写成“已证明必然再报错”。

前一轮只避开这些变化，没有完善整条锁；本轮补齐该部分。请复核方按照当前代码和新测试判断，不能继续引用旧暂缓清单当作当前结论。

## 验证入口和范围

本轮针对命令排队、真实本机 SSH、后台日志裁剪、控制台、启动恢复、依赖提示、双网卡与下载失败执行定向回归。完整仓库检查以本轮合并申请的 GitHub 自动检查记录为准。

```text
python -m unittest tests.test_background_capture_integration tests.test_ssh_channel_isolation tests.test_auto_capture_feature tests.test_hcx_policy -v
python scripts/run_compatibility_audit.py --output build/review --timeout 120 --workers 2 tests.test_background_capture_integration tests.test_safe_hcx_integration tests.test_capture_initialization_evidence tests.test_capture_start_preflight tests.test_capture_integration_safety tests.test_capture_takeover_lifecycle tests.test_ssh_channel_isolation tests.test_auto_capture_feature tests.test_radio_usage_feedback tests.test_auto_capture_cleanup tests.test_boot_script_identity tests.test_hcx_policy
```

Windows shell 测试使用 Git Bash，不把 WSL 与 Windows 临时路径混用。新增日志测试实际执行截短函数，检查超长行、保留首尾、追加句柄继续写入无空洞，以及重复启动保留旧日志。

资料核对覆盖官方 OpenWrt 19.07.10、21.02.7、22.03.7、23.05.5、24.10.0、25.12.0 对应 BusyBox 版本与 head/tail 字节选项默认配置。精确 URL 和校验值见 [本轮资料索引](wydev-background-sources-20261104.json)；前轮 hcx/iw 来源见 [原来源索引](wydev-safe-sources-20261104.json)。

上述为组件源码核对、本机 SSH、真实 shell 和 Qt 离屏测试，不是路由器真机认证，也未在 Debian 12 图形会话实测。本轮未操作真实路由器。此前用户确认的现场通过结果继续作为需要保护的基线。

请协作者的 AI 独立复核：可指出具体调用链、重现条件和反例；不要仅凭文件无冲突、测试名称或本说明的判断作结论。
