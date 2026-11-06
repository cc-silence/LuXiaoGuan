"""Optional online-only router features; never required by capture/login installs."""
from .common_tool_dependencies import CommonTool

OTHER_TOOLS = (
    CommonTool('other_mounts', '磁盘挂载管理', 'block-mount', '补充挂载点管理组件；不会自动分区、格式化或挂载磁盘。', removable=False, summary='让路由器识别和使用 U 盘、硬盘'),
    CommonTool('other_terminal', '网页终端', 'luci-app-ttyd', '在路由器网页中使用交互式终端，与 tmux 会话保留不同。', packages=('ttyd', 'luci-app-ttyd'), summary='在浏览器里输入命令管理路由器'),
    CommonTool('other_files', '网页文件管理', 'luci-app-filebrowser', '补充官方网页文件浏览插件；不等同于第三方 NAS 文件助手的所有功能。', summary='在路由器网页里浏览和管理文件'),
    CommonTool('other_uhttpd', '网页服务管理', 'luci-app-uhttpd', '添加 uHTTPd 管理页面；不修改已有网页端口或访问配置。', summary='设置路由器管理网页的端口和加密访问'),
    CommonTool('other_upnp', 'UPnP 管理', 'luci-app-upnp', '安装 UPnP 管理页面及配套服务；是否启用及允许的端口需在路由器网页配置。', summary='让游戏、下载等软件自动申请开放网络端口'),
    CommonTool('other_dnscrypt', 'DNSCrypt 服务', 'dnscrypt-proxy2', '安装 DNSCrypt 加密解析主程序，不自动改写 DHCP/DNS；不含网页管理插件。', summary='加密查询网址的请求，装好后还需配置'),
    CommonTool('other_dnscrypt_ui', 'DNSCrypt 网页插件', 'luci-app-dnscrypt-proxy', '仅当前固件软件源提供时可安装；不保证管理 DNSCrypt v2，不混装其他固件的插件。', summary='用网页设置 DNSCrypt，需软件源提供配套插件'),
    CommonTool('other_zerotier', '异地设备组网', 'zerotier', '安装虚拟组网主程序，不自动加入网络或修改防火墙；不含网页插件。', summary='把异地设备连成虚拟局域网，装好后还需配置'),
    CommonTool('other_zerotier_ui', '异地组网网页管理', 'luci-app-zerotier', '仅当前固件软件源提供时可安装；官方源可能没有此插件，不添加第三方源。', summary='用网页管理 ZeroTier，需软件源提供配套插件'),
)
OTHER_KEYS = frozenset(tool.key for tool in OTHER_TOOLS)
