# RouterManager 维护规则

本文件适用于 `RouterManager_python/` 及子目录。在本仓库内同时遵守根 `AGENTS.md`。

## 项目入口和边界

- 启动：`python main.py`；无控制台入口：`launch.pyw`。窗口、导航、设置、生命周期分别在 `router_app/` 中维护，不往入口文件堆业务。
- 页面层→服务层→平台层，服务不反向导入页面。不修改生成资源 `modules/resources_rc.py`，除非确无替代方案。
- 只修改独立项目，不顺手改原 WinBox。保留用户已有差异；未要求时不提交、推送或调用子代理。
- 从根因修复，禁止 timer、重复轮询或吞异常补丁。长任务有限时、取消和最终清理，持续工作每 60 秒内反馈进度。
- 修改 `modules/capture_services/` 或 `modules/main_pages/packet_capture/` 前，完整读取各目录的专项规则。

## 主题与公共组件

- 主窗口样式由 `router_app/appearance.py` 组合，外框使用 `themes/router_chrome_*.qss`；业务页使用 `CapturePageStyleManager`；设置和弹窗各自使用 `build_base_stylesheet()`。
- 禁止把整套设置窗口 QSS 套到主窗口，防止通用按钮对齐、字体和 `QScrollArea QWidget` 背景规则泄漏到业务页。不逐个控件叠加反向补丁。
- 颜色、边框和背景写入 QSS。同时支持明暗主题；缩放从原始尺寸计算，不重复缩放当前值。标题栏与导航保留图标入口和左下角设置。
- 复用 `common_widgets/`、`CommonComboBox`、公共复选框片段、`InfoPanel` 和 `FramelessDialog`，不另造一套同类控件。本地文件选择使用 Windows 原生对话框。
- 页面打开不同步扫描、探测或解析大文件；样式不反复刷新整棵控件树。静态缓存有界，日志和列表不无界增长。
- 界面验收必须看实际绘制，检查按钮居中、文字裁切、复选框背景条、明暗图标和缩放，不能只看样式字符串或模型值。

## 独立身份和更新

- 配置、凭据、单实例和防火墙 owner 使用 RouterManager 身份；不读写 WinBox 用户数据。配置存储和路径以现有配置管理器为准。
- 更新必须校验 RouterManager 产品身份和签名，拒绝 WinBox 更新包。独立线上通道以 modules/app_update_services/channel.py 为准，不得接回 WinBox 更新地址、发布密钥或存储桶。
- 更新替换前备份，失败回滚；用户配置和自建文件不得被无条件覆盖。私钥和密码不写入日志、命令参数或仓库。

## LocalGPO 防火墙

- 持久规则统一通过 `modules/platform_services/local_gpo_firewall.py` 的公共接口维护；禁止业务代码直接写 Policies 运行键、调用 `netsh` 或 `New-NetFirewallRule` 创建持久规则。
- 流程为 IGroupPolicyObject 写策略源 → `Save()` → `gpupdate` → ActiveStore 验证；仅看到注册表字符串不算生效。
- 调用 `ensure_persistent_allow_rules()` / `remove_persistent_allow_rules()` 时使用稳定且唯一的 owner、解析后的绝对路径，并确认 EXE 存在。
- 防火墙修改需验证策略源、`gpupdate`、ActiveStore、UAC 取消/超时和旧规则清理。
- 发布包不依赖旧版外置 PowerShell helper。

## 编码、验证与交付

- Python/QSS/JSON/Markdown 使用 UTF-8；Windows shell 不直接嵌入中文写文件，使用 Python Unicode 转义或 Base64。修改 Python 后立即 `py_compile`，并扫描替换字符和乱码。
- 小改动只做定向验证；主窗口外壳与样式可运行 `python -m unittest tests.test_theme_scope.ThemeScopeTests tests.test_chrome_firewall -v`。新设置检查保存、取消、重开回显和执行一致。本机使用 CPU/离屏验证。
- 离线模拟不等于真路由器验收；超时、崩溃、没有汇总不算通过。不自动扩大到全仓库测试或完整构建。
- 打包必须有完整 ZIP、版本一致性和完整性校验，版本从 `main.py` 读取；每次新打包递增小版本，同次失败重试不重复升版。未建立本项目发布入口前，不得调用 WinBox 发布命令代替。

## Offline package support inventory

- Source of record: `docs/offline-packages/support.json`; readable companion: `docs/offline-packages/support.txt`.
- Every offline package delivery or publication must refresh these files through `scripts/openwrt_packages/support_inventory.py`. Record exact releases, targets, architectures, primary/transitive packages, official unavailable profiles, hashes, download endpoints, and verified model evidence.
- Never mark an unknown router model or third-party firmware as verified merely because its CPU architecture matches. Keep JCG Q30 distinct from Q30 Pro until actual firmware identity is known.

## 官方 OpenWrt 长期兼容要求

- 所有后续新增或修改的功能，都必须核查官方 OpenWrt 19.07（2020 年发布）、21.02、22.03、23.05、24.10、25.12 系列的兼容性，包括登录、依赖、信号管理、抓包、文件、终端、配置恢复和软件外围功能。
- 只要改动涉及路由器接口、返回格式、命令参数或依赖，必须先核对对应官方源码或文档，补充新旧格式样例与相关回归。重点覆盖 opkg/apk、旧式与新式网络设备配置、firewall3/firewall4、iwinfo/ubus/rpcd 和无线工具参数差异。
- 优先依据实际能力选择公共执行路径，不按版本号、型号或 radio 编号猜测。当硬件、驱动或固件缺少必需能力时，提前明确说明原因并安全停止，不强装驱动、不替换核心无线组件、不绕过确认和回滚。
- 共性兼容问题修在公共探测、解析和执行层，同步所有相关入口。交付时记录已验证、需要组件、固件不支持和待真机验证；离线模拟不得写成全版本真机认证。
- 衍生固件不在本规则的适配与验收范围；不因本规则放宽官方离线包的身份校验。

## Cloudflare 发布存储上限

- Before every publication, count all Cloudflare objects and incoming peak bytes against the user-defined 1,000,000,000-byte limit. Stop when safe headroom cannot be established.
- Retention and cleanup follow the detailed release rules below; shared update objects require reference checks.


## 发布版本保留规则

- 软件完整包按发布通道只保留最近两版：当前版和上一版；离线依赖包按常用依赖、抓包依赖分别只保留当前一版。
- 新版必须先完整上传、校验公开下载文件和签名，再切换更新清单；确认客户端可读取新版后，自动清理超出保留数量的旧包和对应旧离线清单。失败不得提前删除当前可用版。
- 上传前仍须计算整个仓库的占用和本次上传峰值，保持低于 1,000,000,000 字节。空间不足时可先清理已过期且无引用的历史包；不能删除当前正在服务的版本来腾空间，不足则停止发布。
- 自动更新压缩文件供按文件增量更新使用；只要仍被保留版本引用就不得删除，不按目录整体清空。删除操作必须验签、核对项目归属、复查全部通道引用并记录清单。
- 主程序文件名属于已发布客户端的更新契约，固定使用 LuXiaoGuan.exe；中文产品显示名称独立维护。发布前必须用上一代已发布客户端核验新清单和目录，不得只用当前源码自测。


## Local build disk retention

- Keep exactly one latest verified local application build under build/releases. A failed or incomplete build must never replace the retained usable build.
- scripts/build_cleanup.py owns local artifact cleanup. The application builder runs it after the complete ZIP is verified. Application publication keeps one local blobs cache after client verification; signed manifests, reports and isolated publisher credentials remain intact.
- After a verified offline ZIP is delivered into dist, delete its reproducible staging objects, downloaded index cache and byte-identical staged ZIP copies. Keep the delivered ZIP and support records.
- Never weaken source-bytecode verification or disable clean builds to save disk. Never remove outside build, follow directory links, delete running build data, or treat source, credentials and user data as disposable cache.
- This local one-build policy does not change the independent online channel retention rules.


## GitHub 协作流程

- 在独立分支修改，通过 PR 合入 main；提交前阅读 CONTRIBUTING.md。协作者的改动由 @cc-silence 审核，最终由负责人合并。
- PR 必须通过 collaboration-gate，包括源码检查和 Windows 完整离线回归。CI 不持有路由器或发布凭证，不执行正式发布；真实屏幕绘制和路由器真机验证按交付范围另外记录。
- main 禁止强制覆盖和删除。负责人自己的 PR 可免去自己审核自己的要求，但仍须通过全部自动检查。
- GitHub 规则的版本化配置位于 .github/rulesets/；修改后由负责人核对并同步后台设置，不能只改文档宣称规则已生效。
