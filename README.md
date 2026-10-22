# 路小管

OpenWrt 路由器桌面管理工具，提供路由器登录、无线信号配置、无线桥接、抓包、文件管理和终端功能。

## 开发运行

开发环境使用 Windows、Python 3.10 和 PySide6。

```powershell
python -m pip install -r requirements.txt
python main.py
```

打包入口为 `python scripts/build_release.py`，需要准备 PyInstaller 和原生更新程序的构建工具。发布流程见 [发布说明](docs/release-delivery.md)，维护要求见 [AGENTS.md](AGENTS.md)。

## 兼容范围

官方 OpenWrt 19.07、21.02、22.03、23.05、24.10、25.12。具体组件要求、已完成的检查和待真机验证的项目见 [兼容核查报告](docs/openwrt-compatibility.md)。

## 软件包

[下载 1.0.11_alpha 完整包](https://routermanager-updates.routermanager-update-gateway.workers.dev/files/0c5049c011190da0163db09456d7e01507eec8756400050fe6da78d56bbb8f1b/LuXiaoGuan_v1.0.11_alpha_20261022_172112.zip)。已有版本可通过软件内的“检测更新”获取更新。

## 源码范围

仓库包含程序源码、界面资源、测试、原生组件及构建和发布脚本。本机配置、路由器数据、发布凭证、下载的依赖和构建缓存由 `.gitignore` 排除。
