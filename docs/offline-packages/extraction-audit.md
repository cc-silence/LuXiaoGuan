# 提取依赖：两轮官方资料核对

日期：2026-10-25。范围为电脑端读取本地签名清单、筛选及提取软件包，不执行路由器安装。

## 第一轮：依赖与版本

实际联网读取并下载：
- OpenWrt opkg 依赖及版本检查：https://raw.githubusercontent.com/openwrt/opkg-lede/master/libopkg/pkg_depends.c
- OpenWrt opkg 版本实现：https://raw.githubusercontent.com/openwrt/opkg-lede/master/libopkg/pkg.c
- APK 版本条件位：https://raw.githubusercontent.com/alpinelinux/apk-tools/master/src/apk_version.h
- APK 版本规则：https://raw.githubusercontent.com/alpinelinux/apk-tools/master/src/version.c
- APK 虚拟提供者：https://raw.githubusercontent.com/alpinelinux/apk-tools/master/src/package.c
- APK 上游测试数据：https://raw.githubusercontent.com/alpinelinux/apk-tools/master/test/unit/version.data

发现并修复：
1. 原依赖图只核对包名，丢弃最低版本、精确版本等条件。将已有 OpenWrt 离线版本比较器移入运行时公共模块；APK 独立按官方语法核对。
2. 首个候选缺少配套依赖时没有尝试后续候选，虚拟提供者仅保留一个。现在保留候选并在失败时回退，回退会撤销失败分支的已选包和固件条件。
3. APK 的负依赖之前只是不导出，没有在多选时检查已知冲突。现在检查清单内可见的冲突；内核模块也不能通过虚拟别名绕过保护。

官方 APK 数据保留在 tests/fixtures/official_apk_versions.data，附原网址和 SHA256。756 条有效版本相关样例通过；29 条无效版本被严格拒绝（上游旧比较样例包含无效的完整版本，提取不把它们作为合法包版本接受）。

真实正式离线清单：常用包 549 个组合、抓包包 634 个组合。每个主依赖单选加各组合全选，共 12,039 次检查，无失败。

## 第二轮：文件与任务收尾

实际联网查阅：
- Python 3.10 清理异常：https://docs.python.org/3.10/library/shutil.html#shutil.rmtree
- Python 3.10 重命名行为：https://docs.python.org/3.10/library/pathlib.html#pathlib.Path.rename
- Windows 文件名、保留名、大小写规则：https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
- Qt 非阻塞窗口生命周期：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QDialog.html

发现并修复：
1. 原来只防止导出到当前源包目录，仍可选到另一种离线包资源目录。现在统一阻止导出到软件管理的离线资源区域。
2. 临时目录删除失败可能覆盖最初的复制或校验错误。现在同时保留原始原因及未清理的绝对路径；超时提示也不再提前保证清理成功。
3. 固件自带组件之前只记录名称，现在额外保留原始依赖表达式和版本条件，避免导出清单丢失这些要求。

继续核对的既有保护：原名和扩展名、Windows 大小写冲突、同盘暂存后发布、已有文件不覆盖、复制中取消、磁盘空间、损坏对象、清单版本变化、工作线程和窗口回收。没有改变界面外观。

## 验证结果

- 修复后 73 项定向回归通过，包括完整 Qt 读取到导出的非阻塞链路。
- 正式签名与真实文件重新导出 12 套：常用/抓包两组，各覆盖 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12，包含 IPK/APK。
- 中文 UTF-8、py_compile、git diff --check 通过。
- 证据位于 build/bundle-export-review：two-round-tests.log、round1-real-catalogs.json、round2-real-files.json、audit-sources/manifest.json。
- 本次不替代设备安装器的最终冲突、固件组件及硬件能力验证。只解释清单中已有的依赖条件，不猜测缺失元数据。
