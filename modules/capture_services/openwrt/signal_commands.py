"""受控 OpenWrt Lua 事务；原始配置与备份始终留在路由器。

依赖 lua、uci、ubus、luci.jsonc；缺少时拒绝，不安装依赖或解析敏感原始输出。
Lua 在设备内过滤字段，Python 只收到 JSON 白名单。所有动态参数由 JSON 编码和
POSIX 引用双重隔离；不拼接 SSID、密码或用户提供的命令。
"""

import json
from .takeover_config import TAKEOVER_LUA
from .uci_booleans import NETIFD_BOOL_LUA
from .signal_detail_commands import DETAIL_LUA

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.signal_package_feed import is_safe_filename


_LUA_BINS = ("lua", "lua5.4", "lua5.3", "lua5.1")
# 信号功能所需的路由器软件包；写死白名单，不接受任何外部输入。
SIGNAL_PACKAGES = ("lua", "libuci-lua", "libubus-lua", "luci-lib-jsonc")


_LUA = r'''
local ok_u, uci = pcall(require, "uci")
local ok_b, ubus = pcall(require, "ubus")
local ok_j, json = pcall(require, "luci.jsonc")
if not (ok_u and ok_b and ok_j) then
    io.write('{"ok":false,"code":"dependencies"}')
    return
end
local request = json.parse(arg[1])
local function fail(code) error({code=code}, 0) end
local function shell(command)
    local a = os.execute(command .. " >/dev/null 2>&1")
    return a == 0 or a == true
end
local function hash()
    local p = io.popen("sha256sum /etc/config/wireless 2>/dev/null")
    if not p then fail("read") end
    local text = p:read("*a") or ""
    p:close()
    local value = text:match("^(%x+)%s")
    if not value or #value ~= 64 then fail("read") end
    return value
end
local function cursor(savedir)
    local c = uci.cursor(nil, savedir)
    if not c or not c:load("wireless") then fail("read") end
    return c
end
local function clean()
    local c = cursor()
    local changes = c:changes("wireless")
    if type(changes) ~= "table" then fail("read") end
    if next(changes) then fail("pending") end
end
local function valid_name(value)
    return type(value) == "string" and value:match("^[%w_]+$") ~= nil
end
__NETIFD_BOOL__
local function disabled(value)
    return netifd_bool(value, false)
end
local function all(c)
    local values = {}
    if not c:foreach("wireless", nil, function(s) values[s[".name"]] = s end) then
        fail("read")
    end
    return values
end
local function same(a, b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= "table" then return a == b end
    for k,v in pairs(a) do if not same(v, b[k]) then return false end end
    for k in pairs(b) do if a[k] == nil then return false end end
    return true
end
-- WINBOX_SIGNAL_DETAILS
local function inventory()
    local revision = hash()
    local c = cursor()
    local pending = c:changes("wireless")
    if type(pending) ~= "table" then fail("read") end
    local configs = all(c)
    local bus = ubus.connect()
    if not bus then fail("runtime") end
    local status = bus:call("network.wireless", "status", {})
    if type(status) ~= "table" then bus:close(); fail("runtime") end
    local rows = {}
    c:foreach("wireless", "wifi-iface", function(s)
        local radio = configs[s.device]
        local d = disabled(s.disabled)
        local rd = radio and disabled(radio.disabled)
        local reason = ""
        if s.mode ~= "ap" and s.mode ~= "sta" then reason = "mode"
        elseif s[".anonymous"] or not valid_name(s[".name"]) then reason = "anonymous"
        elseif not radio or radio[".type"] ~= "wifi-device" or not valid_name(s.device) then reason = "radio"
        elseif d == nil or rd == nil then reason = "disabled" end
        local runtime_ssid, runtime_band = "", ""
        local state = "unknown"
        local rs = status[s.device]
        if type(rs) == "table" and type(rs.up) == "boolean" and not rs.pending then
            state = "inactive"
            local matches = 0
            for _, iface in ipairs(rs.interfaces or {}) do
                if iface.section == s[".name"] then
                    matches = matches + 1
                    if type(iface.ifname) == "string" and rs.up then
                        local dev = bus:call("network.device", "status", {name=iface.ifname})
                        if type(dev) == "table" and dev.up == true then
                            state = "active"
                            local info = runtime_info(bus, iface.ifname)
                            local active_config = type(iface.config)=="table" and iface.config or {}
                            local ssid = info and info.ssid or active_config.ssid
                            if type(ssid)=="string" and #ssid<=256 then runtime_ssid=ssid end
                            local freq = info and info.frequency
                            if type(freq)=="number" then
                                if freq>=2400 and freq<2500 then runtime_band="2g"
                                elseif freq>=4900 and freq<5925 then runtime_band="5g"
                                elseif freq>=5925 and freq<=7125 then runtime_band="6g" end
                            end
                        else state = "unknown" end
                    end
                end
            end
            if matches > 1 then state = "unknown"; runtime_ssid=""; runtime_band="" end
        end
        local runtime_note = ""
        if type(rs) == "table" then
            if rs.pending then runtime_note="pending"
            elseif rs.retry_setup_failed then runtime_note="setup_failed" end
        end
        local detail = nil
        if request.details == true then detail = signal_details(bus, s, radio, rs) end
        local band = radio and radio.band or ""
        if band == "" and radio then
            if radio.hwmode == "11a" then band = "5g"
            elseif radio.hwmode == "11g" or radio.hwmode == "11b" then band = "2g" end
        end
        rows[#rows+1] = {label=type(s.description)=="string" and s.description:sub(1,128) or "", section=s[".name"], ssid=type(s.ssid)=="string" and s.ssid or "",
            managed_bridge=type(s.rm_bridge)=="string" or type(s.rm_wds_backup)=="string", mode=type(s.mode)=="string" and s.mode or "", radio=type(s.device)=="string" and s.device or "",
            runtime_ssid=runtime_ssid, runtime_band=runtime_band, band=band, disabled=d==true, radio_disabled=rd==true, running=state, reason=reason, details=detail, runtime_note=runtime_note}
    end)
    bus:close()
    if not same(pending, cursor():changes("wireless")) or hash() ~= revision then fail("changed") end
    return {rows=rows, revision=revision, pending_changes=next(pending)~=nil}
end
local function change()
    clean()
    if hash() ~= request.revision then fail("changed") end
    local c = cursor()
    local before = all(c)
    local s = before[request.section]
    if not s or s[".type"] ~= "wifi-iface" or s[".anonymous"] or
       not valid_name(request.section) or (s.mode ~= "ap" and s.mode ~= "sta") then fail("unsupported") end
    if s.rm_bridge or s.rm_wds_backup then fail("managed_bridge") end
    local radio = before[s.device]
    if not radio or radio[".type"] ~= "wifi-device" or not valid_name(s.device) then fail("unsupported") end
    if disabled(s.disabled) == nil or disabled(radio.disabled) == nil then fail("unsupported") end
    if request.enabled and disabled(radio.disabled) then fail("parent_disabled") end
    if request.enabled == not disabled(s.disabled) then fail("changed") end
    -- 备份只留在设备的受限目录；验证副本一致后才创建隔离的 UCI 暂存区。
    local base = "/etc/winbox-wireless-backups"
    local dir = base .. "/" .. request.token
    if not shell("test ! -L /etc/config/wireless && test -f /etc/config/wireless && " ..
        "test ! -L " .. base .. " && umask 077 && mkdir -p " .. base .. " && chmod 700 " .. base ..
        " && mkdir " .. dir .. " && cp /etc/config/wireless " .. dir .. "/wireless && chmod 600 " ..
        dir .. "/wireless && cmp -s /etc/config/wireless " .. dir .. "/wireless && mkdir " .. dir .. "/delta") then
        fail("backup")
    end
    clean()
    if hash() ~= request.revision then fail("changed") end
    local isolated = cursor(dir .. "/delta")
    if not same(all(isolated), before) then fail("changed") end
    local value = request.enabled and "0" or "1"
    if not isolated:set("wireless", request.section, "disabled", value) then fail("write") end
    -- 比较整个配置的语义，允许的唯一差异是当前 wifi-iface 的 disabled。
    before[request.section].disabled = value
    if not same(all(isolated), before) then fail("changed") end
    clean()
    if hash() ~= request.revision then fail("changed") end
    if not isolated:commit("wireless") then fail("commit") end
    clean()
    if not same(all(cursor()), before) then fail("unconfirmed") end
    -- wifi reload 在部分版本会重载整个网络；主线程确认必须说明可能断开 SSH。
    if not shell("/sbin/wifi reload " .. s.device) then fail("unconfirmed") end
    return true
end
'''
_LUA_RESULT = r'''
local ok, result = pcall(function()
    if request.action:match("^takeover_") then return takeover_config() end
    if request.action == "change" then change(); return {committed=true} end
    return inventory()
end)
if ok then io.write(json.stringify({ok=true, result=result}))
else io.write(json.stringify({ok=false, code=type(result)=="table" and result.code or "internal"})) end
'''


def signal_command(action, lua_bin="lua", **values):
    """生成只接收内部白名单参数的命令；返回字符串，不执行或保存文件。

    远端 mkdir 锁只协调本工具；外部管理器不遵守该锁，因此仍需版本及全配置比较。
    锁遗留时拒绝继续，由用户确认没有任务后处理，不自动删除未知锁。
    lua_bin 只允许白名单解释器名，用于兼容版本化二进制；非法值直接拒绝。
    """
    if lua_bin not in _LUA_BINS:
        raise ValueError("不支持的 lua 解释器。")
    payload = json.dumps(dict(action=action, **values), ensure_ascii=True)
    takeover = action in {"takeover_prepare", "takeover_disable", "takeover_restore"}
    body = _LUA.replace("__NETIFD_BOOL__", NETIFD_BOOL_LUA).replace("-- WINBOX_SIGNAL_DETAILS", DETAIL_LUA) + (TAKEOVER_LUA if takeover else "") + _LUA_RESULT
    script = lua_bin + " - " + quote_argument(payload) + " <<'WINBOX_SIGNAL_LUA'\n" + body + "\nWINBOX_SIGNAL_LUA"
    return (
        "command -v " + lua_bin + " >/dev/null 2>&1 || { printf '%s' '{\"ok\":false,\"code\":\"dependencies\"}'; exit 0; }; "
        "mkdir /tmp/winbox-signal-management.lock 2>/dev/null || "
        "{ printf '%s' '{\"ok\":false,\"code\":\"locked\"}'; exit 0; }; "
        "trap 'rmdir /tmp/winbox-signal-management.lock 2>/dev/null' EXIT; "
        + script
    )


def signal_probe_command():
    """纯 shell 依赖探针；不依赖 lua 也能运行，只输出固定键的 0/1。

    返回的 JSON 信封与 Lua 事务一致，方便同一套解析。所有候选解释器名写死，
    输出的 lua_bin 只可能是白名单或空字符串，不携带外部输入。
    """
    return (
        "lua_bin=\"\"; "
        "for c in lua lua5.4 lua5.3 lua5.1; do "
        "command -v \"$c\" >/dev/null 2>&1 && { lua_bin=\"$c\"; break; }; done; "
        "uci=0; ubus=0; jsonc=0; "
        "if [ -n \"$lua_bin\" ]; then "
        "probe_out=$(\"$lua_bin\" -e 'local a=pcall(require,\"uci\");"
        "local b=pcall(require,\"ubus\");local c=pcall(require,\"luci.jsonc\");"
        "io.write((a and \"1\" or \"0\")..(b and \"1\" or \"0\")..(c and \"1\" or \"0\"))' "
        "2>/dev/null) || probe_out=\"\"; "
        "case \"$probe_out\" in [01][01][01]) "
        "uci=${probe_out%??}; rest=${probe_out#?}; ubus=${rest%?}; jsonc=${probe_out#??};; "
        "esac; fi; "
        "lua=0; [ -n \"$lua_bin\" ] && lua=1; "
        "sha=0; command -v sha256sum >/dev/null 2>&1 && sha=1; "
        "opkg=0; command -v opkg >/dev/null 2>&1 && opkg=1; "
        "apk=0; command -v apk >/dev/null 2>&1 && apk=1; "
        "root=0; [ \"$(id -u 2>/dev/null)\" = \"0\" ] && root=1; "
        "overlay_kb=$(df -k /overlay 2>/dev/null | awk 'NR==2 {print $4}'); "
        "case \"$overlay_kb\" in ''|*[!0-9]*) overlay_kb=$(df -k / 2>/dev/null | awk 'NR==2 {print $4}');; esac; "
        "case \"$overlay_kb\" in ''|*[!0-9]*) overlay_kb=-1;; esac; "
        "case \"$lua_bin\" in lua|lua5.4|lua5.3|lua5.1) bin=\"\\\"$lua_bin\\\"\";; *) bin='\"\"';; esac; "
        "printf '{\"ok\":true,\"result\":{\"lua\":%s,\"uci\":%s,\"ubus\":%s,\"jsonc\":%s,"
        "\"sha256sum\":%s,\"lua_bin\":%s,\"opkg\":%s,\"apk\":%s,\"root\":%s,\"overlay_kb\":%s}}' "
        "\"$lua\" \"$uci\" \"$ubus\" \"$jsonc\" \"$sha\" \"$bin\" \"$opkg\" \"$apk\" \"$root\" \"$overlay_kb\""
    )


def signal_update_command():
    """刷新 opkg 软件源；失败非零退出，由调用方按阶段报错。"""
    return "opkg update"


REMOTE_PACKAGE_DIR = "/tmp/winbox-signal-pkgs"


def signal_list_installed_command():
    """只读已装软件包清单，用于离线闭包求差；不修改任何状态。"""
    return "opkg list-installed"


def signal_offline_install_command(filenames):
    """安装已传到路由器固定目录的离线包；文件名逐个白名单校验。

    只接受落在本目录的 .ipk，不接受路径、空格或 shell 元字符。
    """
    names = [str(name or "") for name in filenames]
    if not names:
        raise ValueError("离线安装包列表为空。")
    for name in names:
        if not is_safe_filename(name):
            raise ValueError(f"离线包文件名非法：{name}。")
    parts = " ".join(
        quote_argument(REMOTE_PACKAGE_DIR + "/" + name) for name in names
    )
    return "opkg install " + parts


def signal_cleanup_command():
    """删除本次离线传包目录；固定路径，不接受参数。"""
    return "rm -rf " + quote_argument(REMOTE_PACKAGE_DIR)


def signal_install_command():
    """opkg 安装白名单信号依赖；包名写死，不接受任何外部输入。

    只做安装不更新源，更新由独立命令先行；源失败时这里不再背锅。
    """
    parts = " ".join(quote_argument(package) for package in SIGNAL_PACKAGES)
    return "opkg update && opkg install " + parts
