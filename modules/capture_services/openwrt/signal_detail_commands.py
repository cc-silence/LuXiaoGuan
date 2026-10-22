"""只读信号详情；不读密码，不扫描、不改配置。"""
DETAIL_LUA = r'''
local detail_count, client_count = 0, 0
local leases = nil
local function text(value)
    if type(value) == "string" and #value <= 256 then return value end
    if type(value) == "number" then return tostring(value) end
    return ""
end
local function call(bus, object, method, args)
    local ok, result = pcall(function() return bus:call(object, method, args or {}) end)
    return ok and type(result) == "table" and result or nil
end
local runtime_infos = {}
local function runtime_info(bus, interface)
    if runtime_infos[interface] == nil then
        runtime_infos[interface] = call(bus, "iwinfo", "info", {device=interface}) or false
    end
    return runtime_infos[interface] or nil
end
local function dhcp_leases()
    if leases then return leases end
    leases = {}
    local file = io.open("/tmp/dhcp.leases", "r")
    if not file then return leases end
    for n=1,1024 do
        local line = file:read("*l")
        if not line then break end
        if #line <= 1024 then
            local mac, ip, name = line:match("^%S+%s+(%x%x:%x%x:%x%x:%x%x:%x%x:%x%x)%s+(%S+)%s+(%S+)")
            if mac then leases[mac:lower()] = {ip=text(ip), name=name ~= "*" and text(name) or ""} end
        end
    end
    file:close()
    return leases
end
local function signal_details(bus, s, radio, rs)
    detail_count = detail_count + 1
    local result = {interface="", channel="", bandwidth="", encryption=text(s.encryption),
                    clients={}, clients_state="unavailable", note="", configured=true}
    if detail_count > 64 then result.note="limit"; return result end
    result.bandwidth = radio and text(radio.htmode) or ""
    result.channel = radio and text(radio.channel) or ""
    local matches = {}
    if type(rs) == "table" then
        for _, iface in ipairs(rs.interfaces or {}) do
            if iface.section == s[".name"] then matches[#matches+1] = iface end
        end
    end
    if #matches ~= 1 or type(matches[1].ifname) ~= "string" or
       not matches[1].ifname:match("^[%w_.:-]+$") then
        result.note = #matches > 1 and "ambiguous" or "no_interface"
        return result
    end
    result.interface = text(matches[1].ifname)
    local info = runtime_info(bus, result.interface)
    if info and type(info.channel) == "number" then
        result.channel = tostring(info.channel)
        result.configured = false
    end
    if s.mode ~= "ap" then result.clients_state="not_ap"; return result end
    local clients = call(bus, "hostapd." .. result.interface, "get_clients", {})
    if not clients or type(clients.clients) ~= "table" then return result end
    result.clients_state = "ready"
    for mac, client in pairs(clients.clients) do
        if type(mac) == "string" and mac:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") and
           type(client) == "table" and (client.assoc == true or client.authorized == true) then
            if #result.clients >= 128 or client_count >= 512 then
                result.clients_state="limited"; break
            end
            client_count = client_count + 1
            local lease = dhcp_leases()[mac:lower()] or {}
            local signal = type(client.signal) == "number" and client.signal or nil
            result.clients[#result.clients+1] = {mac=mac:lower(), ip=lease.ip or "",
                                               name=lease.name or "", signal=signal}
        end
    end
    return result
end
'''
