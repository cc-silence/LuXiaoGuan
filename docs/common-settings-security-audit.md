# 常用设置扩展核对记录

源码已增加开机连接策略、批量更换上级 Wi-Fi 身份、批量更换自建热点名称和 BSSID、路由器初始安全设置。页面使用三列卡片，按钮同行并在卡片底部对齐；复杂操作提供详情与最终预览。

## 公共组件与后台执行

新增组件前必须先搜索公共组件和相似页面，已有实现优先复用；规则已写入项目 AGENTS.md。
进度弹窗复用 TaskProgressDialog / RoundedProgressBar。适配层只转换阶段消息和取消状态，不另绘进度条。备份分别报告检查、生成、下载、校验和保存，只有实际总量明确才显示百分比。
开机连接状态在登录、设备切换时由后台读取，不额外轮询。写操作带连接核验、超时与清理；结果不明确时提示未确认，不自动重试。旧任务不能改动新连接。

## 功能边界

- 初始设置详情说明首次使用前恢复初始状态；复位键按压时间以具体设备说明为准，不能统一写成五秒。执行前预览，提供“直接应用”和“备份并应用”；后者先生成并校验电脑上的备份，失败不执行修改。直接应用不要求电脑备份，保留事务校验与失败恢复。
- 地址、无线名称和地址变化以及重启均提前提醒，完成后显示新信息。默认新热点为 2.4G，密码 luxiaoguan123 可修改。先验证能力并创建、确认新热点，再删除确认属于出厂默认的热点。创建失败保留原热点；核实阶段断线可能已保存新热点，会明确提示和列出备份位置。
- 名称与 MAC 独立随机，不视作真实厂商认证；不改物理 LAN 网口。复杂共享网络、WDS、relayd 等情况排除并说明。
- 开机连接开关影响后续开机，不立刻断开当前连接。恢复时只恢复本功能停用的连接，保留用户手动停用状态。
- 不关闭 LAN 的 IPv6，不统一修改网页并发请求数。
- HTTP 选项按讨论保留，明确提示管理网页不再加密、旧 HTTPS 地址失效。
- 磁盘选项关闭插入时自动处理，不卸载现有磁盘，已配置的开机挂载仍可能执行。交换分区选项只阻止自动使用未配置的交换分区。
- 升级辅助服务存在才停用，不把网页升级插件当作自启动服务，不宣称关闭所有更新。
- 日志使用所讨论的具体参数，不承诺所有版本都能因此减少空间占用。
- 救援按键限制禁用并说明原因：官方救援判断早于配置覆盖层挂载，修改配置文件不能保证生效；不改启动器或固件，不伪报成功。

## 三轮核对

第一轮检查业务线路、基本界面与后台执行，针对性回归 72 项通过。
第二轮对照官方实现，修正 Dropbear 的 DirectInterface 优先级、挂载选项范围、救援按键无法安全通用限制等问题。增加热点两阶段创建、外部密码变更、提交不明确和真实 Qt 线程续接等检查。
第三轮使用公用进度弹窗，检查明暗主题及 100% / 150% 缩放，增加实际 shell 语法与开机函数测试，执行相关模块最终回归。最终相关模块 185 项通过，独立主窗口主题检查 1 项通过；详见 build/common-settings-extension-review/final-regression-v2.log 和 theme-final.log，不累加重复测试次数。

## 官方依据与验证限制

核对 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12。tests/fixtures/official_policy_contracts.json 保存六系列共 90 条源码地址与 SHA256；rpcd、fstools 按各系列 Makefile 指定提交固定版本。
检查 network、rc.common、system、sysntpd、cron、dnsmasq、uhttpd、Dropbear、默认防火墙、rpcd 会话与排序、fstools 和早期救援启动顺序。无线按实际接口和能力判断频段，不按 radio0 / radio1 猜测。
可用 scripts/audit_common_settings_sources.py 下载，scripts/build_common_settings_contracts.py 生成并验证记录。

时间服务器采用 ntp1.tencent.com、ntp2.tencent.com、ntp1.aliyun.com、ntp2.aliyun.com，参考提供方说明：

- [腾讯云时间同步](https://intl.cloud.tencent.com/document/product/213/39161?lang=en)
- [阿里云 NTP](https://help.aliyun.com/zh/ecs/user-guide/alibaba-cloud-ntp-server/)

域名核对不等于测试了用户网络中的 UDP 可达性。

build/common-settings-extension-review/ 保存源码、日志和截图。Lua 5.1 用于离线配置交易，Qt 真实线程用于界面响应和清理，Git Bash 执行实际开机函数检查处理范围。本次没有连接路由器执行写操作，没有真机认证全部硬件、驱动与衍生固件。运行时仍需通过权限、工具、配置结构和无线能力检测，缺少能力则停止或跳过并说明。本次未打包发布。

## 应用方式与名称输入

- 发射信号和初始安全设置复用同一个随机名称输入组件；随机按钮只填写名称，不执行远程操作。手动输入的新热点名称原样进入预览和执行，校验 32 字节上限及重名。
- 读取、预览及应用共用任务进度弹窗；失败显示错误弹窗，关闭时释放进度窗口。
- 开机连接采用简短的开启/关闭确认，执行后重新读取脚本内容与启动链接核实结果；当前无线不主动断开，从下次开机生效。

RandomNameInput 位于无线设置页面模块，edit 保留原输入框接口，random_button 只替换 edit.text()；不读写路由器、不触碰密码或无线地址。发射信号的新建和编辑窗口、初始化热点名称共用此实现。

## Settings group presentation

`SettingsCardGroups` reuses each existing action card. Full-width title bars and scaled spacing separate the three groups. Title bars have no left stripe; the section body stays transparent and cards are retained during resizing. `captureSettingsGroup` and `captureSettingsGroupTitle` are shared selectors in both page themes.

## Fixed hcxdumptool installation requirement

User-requested policy: login and common bulk installation always include missing hcxdumptool, independently of the saved PMKID switch. The common tool catalog is the policy source for checks, consent and execution; existing package deduplication and three-source installation remain in place. A bounded `-h` probe only checks that the binary starts; it never starts capture or implies driver compatibility. Existing six-series package matching, supported-version capture allowlists and BPF requirements remain unchanged, including the 6.0.1 capture rejection. No router or Debian desktop was tested. Upstream help contract: https://github.com/ZerBea/hcxdumptool/blob/master/man/hcxdumptool.1 . Official package definition and six-series evidence remain in docs/offline-packages/support.json and docs/openwrt-compatibility.md.
