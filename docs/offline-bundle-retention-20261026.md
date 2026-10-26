# 离线依赖包旧版本清理核查（2026-10-26）

## 本次处理
- 常用依赖与抓包依赖共用清理规则：下载、签名、容量、完整文件校验全部通过并原子启用新版后，回收同类旧版本和指纹匹配的旧 ZIP。
- 下载失败、校验失败、启用失败或启用前取消，不清理旧版。清理失败只警告，不把已经启用的新版报成下载失败；后续成功下载会再尝试。
- 下载、安装、提取与版本检查使用同一资源目录锁。跨线程、跨进程冲突立即返回明确提示，不在界面线程等待；嵌套调用可重入，进程退出后系统释放锁。
- 清理只枚举受管目录并逐个删除清单登记的文件，不递归删除未知目录；保留当前版本、用户自建文件、外部 ZIP 和导出结果，不跟随符号链接或 Windows 目录联接。
- 旧版目录按既有清单识别，不要求旧客户端从未生成过的新标记文件。清理最多占用 30 秒，支持取消；归档校验使用流式读取。
- 没有改界面、下载线路、路由器命令、包选择、MAC 功能、默认值或打包版本。

## 验证
Python 3.10.11 / Windows：Python 语法、UTF-8 编码及差异格式检查通过。9 个测试模块共 74 项定向回归通过：
test_bundle_version_cleanup、test_bundle_archive_cleanup、test_common_offline_bundle、
test_bundle_updates、test_bundle_export、test_bundle_export_audit、
test_bundle_export_ui、test_offline_cleanup、test_package_install_guards。

新增验证覆盖两类包的升级回收、重复执行、失败与取消保留旧版、启用失败、文件占用后的重试、用户文件保护、目录联接隔离、跨线程/跨进程占用、进程异常退出释放，以及安装和提取实际调用期间禁止并发更新。

本次只在临时测试目录执行回收，没有主动删除用户当前软件中的离线资源；后续使用修复后的源码成功下载时自动执行。现有已打包 EXE 尚未更新。

## 兼容性判断
本次属于电脑本地文件生命周期管理，不改远端安装协议、软件包、系统匹配清单或架构判断，因此不新增 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12 的系统/硬件差异分支。测试同时保留 opkg 和 apk 的本地安装与提取覆盖；不等于上述版本或型号已通过真机验收。
Windows 使用 msvcrt 非阻塞文件锁；Unix 分支使用 fcntl.flock 并延迟导入。没有 Debian 12 实机环境，Unix 实际运行与退出释放仍需在该环境验证，不能宣称双平台验收完成。

依据：
- https://docs.python.org/3.10/library/msvcrt.html#msvcrt.locking
- https://docs.python.org/3.10/library/fcntl.html#fcntl.flock
- https://docs.python.org/3.10/library/pathlib.html

## 用户补充的路径长度问题
版本目录和对象文件名均为 64 字符 SHA-256，用于定位及内容校验。示例从 LuXiaoGuan 到对象文件为 189 字符；放到 C:/Users/CC/Desktop 下为 209 字符。放入更深目录后，确有超过传统 Windows 260 字符限制的可能；程序支持长路径不代表复制工具同样支持。

后续已按用户确认的方向实现日期加序号的短目录，并保留新版读取旧格式及清理旧资源的能力。旧版未升级程序无法读取新日期目录。详见 docs/offline-bundle-short-paths-20261026.md。

微软依据：
https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
