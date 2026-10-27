# 依赖分组与单项操作（2026-10-26）

## 完成内容
- 抓包安装确认清单原先遍历所有依赖，只排除 SFTP，因此会混入常用工具 wpa-cli。后台 capture 安装范围本已过滤常用项。本次让确认、任务范围和执行使用同一组定义，修正的是确认清单混组，不是把 wpa-cli 改成抓包包。
- 抓包行显示原始软件包名；每行支持安装、卸载或重新检查。功能开关及 PMKID 当前显示策略保持原样。系统自带的 uci、wifi、ip 标明为系统命令，仅提供检查，不卸载系统组件。
- 常用工具增加“检查并安装全部常用依赖”，包含本组文件传输、终端、无线工具、网页功能与信号管理项。安装前显示范围，只补实际缺项。
- 两组行共用 dependency_row.py，继续使用已有 QSS、按钮和确认弹窗。离线包说明移至按钮下方，以修复150%窄窗口的换行裁切；未改配色与主题。
- 主页面只保留动作分发；确认逻辑移到 dependency_actions.py 与 common_dependency_actions.py，避免继续往大页面加实现。

## 统一执行线路
所有新增安装按钮都调用 OpenWrtDependencyService.install_async，再进入现有 OpenWrtDependencyManager.install_all：
匹配的本地离线资源 → 路由器在线软件源 → 电脑下载转装（仅在网络失败时回退）。
现有代理、直连、重试、opkg/apk预演、禁止升级替换、安全校验和缓存回收继续由原公共实现负责，没有新增另一套下载/安装命令。
common_all只扩大常用分组的检查范围；capture单项任务仅安装所选功能的根包和实际缺少的共用基础工具。复测会刷新其他已启用抓包功能，避免 aircrack-ng 共用时状态过期；只读单项检查只合并本项，避免将其他功能误改为关闭。

卸载仍调用原 uninstall_packages 和包管理器预演，不使用force/autoremove。具名抓包包现在同常用工具一样按实际已装包清单选择并复测删除结果，不因运行命令缺少另一项而漏掉已装包，也不删除别的包提供的命令。共享 aircrack-ng 的影响在确认框说明，用户可逐包勾选；发生其他包依赖或计划超出范围时停止。

“停止”绑定原任务会话，即使旧按钮点击时任务刚结束，也不会误触发新安装/卸载。页面销毁不停止后台任务，重建后回放任务状态。

## 公共组件说明
create_dependency_row(parent, key, title, package, position, clicked, toggle=False, observer=None) 返回行容器、名称控件、状态标签和操作按钮。
普通名称可换行，抓包功能使用原复选框；交替背景与选中状态沿用 captureDependencyRow，紧凑按钮沿用 compactDependency。函数只建控件，不连接路由器、不安装软件；调用者按任务状态更新操作。
公共范围数据位于 dependency_groups.py。增加工具时更新原 TOOLS；常用批量范围自动同步。抓包功能根包使用同一 CAPTURE_PACKAGES 映射生成行名称、服务约束和管理器清单。

## 验证
- 本次新增16项回归：确认清单不混组、单项与批量调用同一管理器、opkg/apk具名安全卸载、共享命令仍由其他包提供时的复测、未知状态、取消、只读检查不污染其他项、原任务停止、按钮忙碌状态等。
- 最终相关回归156项通过，89.347秒，进程正常退出（退出码0）。覆盖统一安装线路、代理、来源回退、官方格式样例、诊断、离线安装、安全卸载、取消、页面重建和软件启动退出。
- 明暗主题，100%、150%、200%缩放，共12张完整软件内实际卡片绘制；检查文字/按钮不重叠、说明换行不裁切。截图在 build/review/dependency-controls/，测试状态是本机模拟，不是实际设备检测结果。
- Python语法、UTF-8、乱码扫描与git diff --check通过。此前一次测试命令误写不存在的测试类名，已改为真实FolderTests并完成最终回归，不将该失败算为通过。
- 未操作真实路由器，未重新打包、发布或改变离线依赖包内容。

## 兼容性与官方依据
本次涉及安装卸载入口，按实际opkg/apk能力选择继续复用原执行器；没有按型号、radio编号或版本号增加猜测分支，不扩展包覆盖范围。现有软件支持清单仍决定具体固件、平台、架构及离线包是否可用。
已核对官方19.07、21.02、22.03、23.05、24.10、25.12分支：wpa-cli均是wpa_supplicant命令行控制客户端，名称保持wpa-cli。该包在本软件用于无线连接验证，归常用工具；抓包根包保持原aircrack-ng/hcxdumptool/reaver映射，iw/ip为共用能力，已有提供者可用时复用。包管理器和架构适配仍由原公共层判定，不把CPU匹配等同于型号真机通过。
Windows完成本地回归和实际绘制。Debian12未提供实机环境，本次复用Qt公共控件及既有平台接口，不能宣称Debian或全部OpenWrt型号真机验收通过。OpenWrt文档站本轮返回防爬页面，未把该页面作为安装规则证据，改读官方源码。

官方包定义：
- 19.07：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-19.07/package/network/services/hostapd/Makefile
- 21.02：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-21.02/package/network/services/hostapd/Makefile
- 22.03：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-22.03/package/network/services/hostapd/Makefile
- 23.05：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-23.05/package/network/services/hostapd/Makefile
- 24.10：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-24.10/package/network/services/hostapd/Makefile
- 25.12：https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/network/services/hostapd/Makefile
