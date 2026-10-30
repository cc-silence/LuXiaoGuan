# RouterManager 维护规则

本文件适用于 `RouterManager_python/` 及子目录。在本仓库内同时遵守根 `AGENTS.md`。

## 项目入口和边界

- 启动：`python main.py`；无控制台入口：`launch.pyw`。窗口、导航、设置、生命周期分别在 `router_app/` 中维护，不往入口文件堆业务。
- 页面层→服务层→平台层，服务不反向导入页面。不修改生成资源 `modules/resources_rc.py`，除非确无替代方案。
- 只修改独立项目，不顺手改原 WinBox。保留用户已有差异；未要求时不提交、推送或调用子代理。
- 从根因修复，禁止 timer、重复轮询或吞异常补丁。长任务有限时、取消和最终清理，持续工作每 60 秒内反馈进度。
- 修改 `modules/capture_services/` 或 `modules/main_pages/packet_capture/` 前，完整读取各目录的专项规则。

## Windows 与 Debian 12 双平台开发要求

- 软件的桌面运行目标为 Windows 和 Debian 12。此要求适用于后续新增功能、问题修复、依赖升级、界面、测试、打包和自动更新，不等到最终打包时才适配 Linux。Debian 12 指运行本软件的电脑系统，不替代下文的 OpenWrt 路由器兼容范围。
- 维护同一套源码与公共业务逻辑，不复制两套长期维护的项目。动手前沿实际调用链检查两个系统的影响；交付时说明共用逻辑、系统差异及各自验证结果。
- 操作系统差异集中在平台服务和构建入口，通过公共接口及能力探测选择实现；页面和业务服务不散落平台分支。不在 Debian 导入链上无条件加载 winreg、Windows DLL、COM 或仅 Windows 可用的依赖。
- 本机路径使用 pathlib 或系统目录接口，遵守 Linux 文件名大小写和执行权限；不硬编码盘符、反斜杠、用户目录或可执行文件后缀。路由器远端路径仍按 POSIX 规则处理，不能混用本机路径。
- 外部进程按平台选择工具与参数，优先参数列表，不依赖 Windows shell、PowerShell 或批处理。依赖按平台声明并延迟加载；核实 Python、Qt/PySide6、系统库及外部工具在 Debian 12 上的可用版本，涉及实现时查阅官方资料，不凭记忆推断。
- 配置、日志、缓存、凭据、单实例、桌面快捷方式、文件打开与回收站、终端、网络探测、防火墙、权限提升、托盘和自启动均通过对应平台实现。凭据不得因 Linux 适配退回明文；不得要求整个软件以 root 身份运行或关闭防火墙来代替适配。
- Windows 专项功能和限制仅适用于 Windows。Debian 12 缺少对应能力时，必须明确说明原因并与用户协调替代方案，不用空实现返回成功，不暗中删减功能，也不改变已验证的 Windows 行为。
- 界面复用公共 Qt 组件和主题；同时检查两个系统的中文字体、图标、明暗主题、缩放、布局、文件对话框、窗口拖动与缩放和托盘行为。Debian 12 按实际桌面环境及 X11/Wayland 会话验证，不把某一种会话的通过结果推广为全部支持。
- 共用逻辑保留定向回归；平台接口分别覆盖实现、缺依赖、取消和失败收尾。受改动影响的平台都要验证，且 Debian 12 必须实际检查依赖安装、导入与启动、相关核心功能及退出清理。模拟平台分支、Windows 测试或离屏绘制不等于 Debian 12 实机运行通过。
- 当前没有 Debian 12 验证环境或必要设备时，可继续完成不依赖它的工作，但须明确记录待验证项和验收步骤；不得宣称双平台已兼容。Debian 12 的处理器架构已确认为 Intel/AMD x86-64；不擅自扩展为 ARM 或其他架构支持。
- Windows 和 Debian 12 使用同一产品版本、分别在对应系统的受控环境构建与验收，不能将 Windows 产物改名当作 Linux 版。Windows 保留现有 EXE/ZIP 契约；Debian 安装包格式在发布前确定，并检查系统依赖、安装、启动、升级、回滚与卸载后的用户数据保留。
- 更新清单、下载包和签名必须校验产品、系统、处理器架构及发布通道，拒绝跨系统或跨架构更新。远端按 Windows/Linux 和正式版/alpha 四个槽各保留一版；本地最新可用构建保留按系统和架构分别计算，同时遵守现有总存储上限，不让一个平台的发布清理另一个平台的可用版本。
- 后续 CI 应覆盖 Windows 和 Debian 12 的适用检查。CI 尚未配置或某平台尚未通过时，如实记录差距，不通过跳过检查宣称双平台验收完成；远端路由器操作仍遵守专项确认与回滚要求。

## 主题与公共组件

- 新增界面组件前，必须先搜索公共组件和相似页面；已有公共实现时优先复用，不另造一套。进度弹窗和进度条优先使用 TaskProgressDialog / RoundedProgressBar；确实缺少时再抽成公共组件，同步更新说明和验证。
- 主窗口样式由 `router_app/appearance.py` 组合，外框使用 `themes/router_chrome_*.qss`；业务页使用 `CapturePageStyleManager`；设置和弹窗各自使用 `build_base_stylesheet()`。
- 禁止把整套设置窗口 QSS 套到主窗口，防止通用按钮对齐、字体和 `QScrollArea QWidget` 背景规则泄漏到业务页。不逐个控件叠加反向补丁。
- 颜色、边框和背景写入 QSS。同时支持明暗主题；缩放从原始尺寸计算，不重复缩放当前值。标题栏与导航保留图标入口和左下角设置。
- 复用 `common_widgets/`、`CommonComboBox`、公共复选框片段、`InfoPanel` 和 `FramelessDialog`，不另造一套同类控件。本地文件选择优先使用所在系统的原生对话框；Debian 12 不可用时使用公共 Qt 对话框，不无条件调用 Windows 接口。
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

## 官方 OpenWrt 系统与硬件长期兼容要求

- 本软件用户仅通过局域网登录路由器，不以“经上级网络管理”作为设计前提。修改上级 Wi-Fi 身份不应主动退出 LAN 登录；真实无线重载引起的短暂中断需如实说明，恢复连接必须验证原设备身份，不覆盖用户主动断开或新登录。

- 强制适用范围：凡是新增、修改或修复路由器操作，必须先做兼容性资料核对再实现。包括路由器登录、读取状态、命令和参数、返回值解析、依赖安装卸载、常用设置、信号、桥接、抓包、文件、终端、备份恢复、重启和重置等。界面或公共组件的改动如果会改变这些操作的实际执行，同样适用，不能以只改了界面为由跳过。
- 豁免范围：确认不会影响路由器操作的纯电脑本地功能，不要扩大为 OpenWrt 资料核对。例如电脑本地防火墙、窗口关闭、托盘、主题等，只做其自身所需的平台核对和测试。路由器自身的防火墙、网络规则仍属于路由器操作，不能豁免。动手前按实际调用链记录本次适用或豁免的简短理由。
- 资料核对流程：必须阅读并对照本次功能对应的官方文档、源码或驱动资料，不凭记忆或以前的通过次数代替。优先核对已下载到本地的对应精确版本资料及其来源索引；本地资料缺失、版本不匹配、来源无法核实、过时或不足以支持结论时，必须实际上网查阅官方来源并补齐，不能只列一个未读取的链接。新资料保留 URL、精确版本或固定提交、文件校验值、核对日期和结论。
- 逐功能核对实际调用的命令、参数、输入输出字段、组件能力和保存/应用/回滚语义。不得用系列分支最新代码代替该系列的旧小版本，也不得用某一版本或型号的成功代替其他支持组合。已确认的差异补充新旧格式回归样例，先证实原因再修改公共实现。
- 本地资料入口：`docs/full-openwrt-compatibility-audit-20261029.md`、`build/compatibility-audit-20261029/sources/manifest.json`、同目录的固定组件、无线实现和型号索引，以及 `tests/fixtures/official_hostapd_exact_releases.json` 等回归合同。这些是查询入口，不是永久通过证明；缓存被清理或本次范围新增时必须实际上网补查。
- 系统范围包括官方 OpenWrt 19.07（2020 年发布）、21.02、22.03、23.05、24.10、25.12 系列，精确小版本以软件支持清单为准。新功能和修改后的功能必须覆盖软件支持范围，不得只适配当前设备或某一系列。
- 硬件范围包括清单中的 mediatek/filogic、mediatek/mt7622、ramips/mt7621、ipq40xx/generic、ipq806x/generic、ipq807x/generic、qualcommax/ipq807x、ath79/generic、ath79/nand、bcm53xx/generic、bcm27xx/bcm2711、mvebu/cortexa9、mvebu/cortexa53、sunxi/cortexa53、x86/64 及其对应 CPU 架构。型号核查至少包括 Xiaomi AX3600、ASUS RT-ACRH17、Redmi AX6S、JCG Q30 Pro。后续支持清单扩展时同步扩展核查范围，不用这份列举限制新支持硬件。JCG Q30 仍待实际固件确认，不得与 Q30 Pro 混同。
- 兼容性必须同时考虑系统版本、固件实际组件、目标平台、CPU 架构、网络配置结构、无线芯片与驱动、存储布局和资源限制。重点覆盖 opkg/apk、swconfig/DSA、旧式与新式网络设备配置、firewall3/firewall4、iwinfo/ubus/rpcd、无线工具参数、重置和备份恢复差异。
- 用户要求核对路由器功能兼容性时，默认覆盖上述所有支持系统和硬件范围，逐项给出资料依据、条件和限制，不能只跑离线测试。纯电脑本地功能按上述豁免规则处理。对硬件无关的公共功能说明判断依据，不生造型号差异。
- 离线依赖包的真实范围以 `docs/offline-packages/support.json` 为准，`support.txt` 为可读版。必须按具体固件版本 + target/subtarget + 软件包架构核对主依赖及传递依赖，涉及内核模块时还要核对内核匹配。不得把平台与版本作全组合推导：官方不存在的组合、缺依赖组合和明确排除平台必须如实列明。包已覆盖不等于该型号全部功能已通过真机验收，也不得把离线包未覆盖直接等同于软件所有基础功能不支持。
- 优先依据设备实际能力选择公共执行路径，不按版本号、型号或 radio 编号猜测。硬件、驱动或固件缺少必需能力时，必须明确说明原因并与用户协调方案，不得暗中缩减支持范围或宣称全部兼容；不强装驱动、不替换核心无线组件、不绕过确认和回滚。
- 共性兼容问题修在公共探测、解析和执行层，同步所有相关入口。交付时分别标明资料核实、离线测试、真机验证、需要组件、固件/硬件不支持和待验证的范围；没有证据不能写成已验证。
- 衍生固件不在本规则的适配与验收范围；不因本规则放宽官方离线包的身份校验。

## Cloudflare 发布存储上限

- Before every publication, count all Cloudflare objects and incoming peak bytes against the user-defined 1,000,000,000-byte limit. Stop when safe headroom cannot be established.
- Retention and cleanup follow the detailed release rules below; shared update objects require reference checks.


## 发布版本保留规则

- 云端软件完整包最多四份：Windows 正式版、Windows alpha、Linux 正式版、Linux alpha 各只保留当前一版，超额旧包必须实际删除；离线依赖包按常用依赖、抓包依赖分别只保留当前一版。
- 正常发布先完整上传并校验公开下载文件和签名，再切换更新清单；确认客户端可读取后，自动删除超额旧包。正式版不得提前删除当前可用版。alpha 只有在存储上限不足时允许先删除同系统旧 alpha，再上传新版；不得删除正式版或另一系统当前包腾空间。
- 上传前计算整个仓库和本次上传峰值，保持低于 1,000,000,000 字节。alpha 配额例外必须先保存可恢复的签名准备记录、将该 alpha 通道切为“正在发布”、复查并确认旧包删除，再上传；中断可用同一份已验证准备结果续传，未验收新版前不得宣称发布成功。即使删除旧 alpha 后仍不足则停止，不触碰其他有效包。
- 应用采用完整 ZIP 自动更新：软件内下载完整包，下载验签和校验后准备文件，必须等待用户点击重启并安装更新且确认后，才能备份、替换并重启；不得在下载完成回调中自动安装或退出，保留用户配置，失败回滚。云端只上传完整包和签名版本通知，禁止生成或上传 blobs/catalog 增量文件。本地安装清单必须由已验证 ZIP 派生，安装前重新校验，不信任可被篡改的缓存清单。 仅允许用户已明确授权的一次性旧版过渡发布使用 --legacy-bridge 生成一套旧更新文件；普通后续发布仍仅上传完整包。过渡版独立通道、安装包和引用的增量文件必须保护，不得随下一版自动清理，待用户确认结束过渡后再删除。 过渡期间明确允许在四个常规发布槽之外临时额外保留一份受保护的过渡完整包，全局最多一套过渡入口。
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


## Owner-authorized hcx automatic capture exception (2026-10-30)

- The owner explicitly permits the designated hcxdumptool 6.3.4 installation path for automatic capture, with force options restricted to the hcx package itself. Existing libpcap1/libopenssl3 must be retained; missing libraries use the standard dependency installer. Do not force-install the whole bundle. This is a narrow exception to the general dependency-installation restrictions; it must not be generalized to other features or packages.
- A cross-release hcx package is not by itself a merge blocker under this authorization. This authorization is not proof of compatibility: verify the exact CPU/package architecture, package identity and hashes, runtime library requirements, available space, cancellation, failure reporting and post-install execution. Do not claim all supported OpenWrt versions are verified.
- The Python path must install only the hcx archive, verify its hash and execution, and retain existing shared-library versions; apk must pin those installed versions and reject plans that change another package. Do not treat permission for hcx as permission to replace arbitrary system components.
- The owner accepts the existing automatic-capture hcxmon interface-name cleanup for this merge and does not require that concern to block it. This exception does not authorize broad process killing or unrelated interface cleanup elsewhere.
- Unresolved or conflicting radio-to-phy ownership remains a blocker: do not guess the physical radio from its number.
