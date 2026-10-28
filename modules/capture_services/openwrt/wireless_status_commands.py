"""Read-only OpenWrt runtime observations; never scan, commit UCI or restart interfaces."""
from .signal_commands import _LUA_BINS

STATUS_LUA = r"""
local okj,json=pcall(require,'luci.jsonc')
local oku,ubus=pcall(require,'ubus')
local okc,uci=pcall(require,'uci')
if not okj or not oku or not okc then io.write('{"ok":false,"code":"dependencies"}'); return end
local bus=ubus.connect()
if not bus then io.write('{"ok":false,"code":"disconnected"}'); return end
local function call(object,method,args)
    local ok,r=pcall(function() return bus:call(object,method,args or {}) end)
    if ok and type(r)=='table' then return r end
end
local function text(v)
    if type(v)=='string' and #v<=256 and not v:find('[%z\1-\31]') then return v end
    return ''
end
local function name(v) return type(v)=='string' and #v<=64 and v:match('^[%w_.:-]+$') end
local function mac(v) return type(v)=='string' and v:match('^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$') end
local function counter(iface,kind)
    local f=io.open('/sys/class/net/'..iface..'/statistics/'..kind..'_bytes','r')
    if not f then return nil end
    local value=f:read(32); f:close(); return tonumber(value)
end
local hints={}
local f=io.open('/tmp/dhcp.leases','r')
if f then
    for i=1,1024 do
        local line=f:read('*l'); if not line then break end
        if #line<=1024 then
            local m,ip,host=line:match('^%S+%s+(%x%x:%x%x:%x%x:%x%x:%x%x:%x%x)%s+(%S+)%s+(%S+)')
            if m then hints[m:lower()]={ip=text(ip),name=host~='*' and text(host) or ''} end
        end
    end
    f:close()
end
local rich=call('luci-rpc','getHostHints') or {}
local count=0
for m,h in pairs(rich) do
    count=count+1; if count>1024 then break end
    if mac(m) and type(h)=='table' then
        local old=hints[m:lower()] or {}
        local ips=type(h.ipaddrs)=='table' and h.ipaddrs or {}
        local v6=type(h.ip6addrs)=='table' and h.ip6addrs or {}
        hints[m:lower()]={ip=text(ips[1] or v6[1] or old.ip),name=text(h.name or old.name)}
    end
end
local configs={}
local cursor=uci.cursor()
cursor:foreach('wireless','wifi-iface',function(s)
    configs[s['.name']]={mode=s.mode,ssid=s.ssid,device=s.device}
end)
local runtime=call('network.wireless','status')
if not runtime then io.write(json.stringify({ok=false,code='unsupported'})); bus:close(); return end
local dump=call('network.interface','dump') or {}
local networks,total,limited={},0,false
for radio,state in pairs(runtime) do
    if type(state)=='table' and state.up==true then
        for _,iface in ipairs(state.interfaces or {}) do
            local device=iface.ifname
            local cfg=configs[iface.section] or iface.config or {}
            if name(device) and (cfg.mode=='ap' or cfg.mode=='sta') then
                if #networks>=64 then limited=true; break end
                local info=call('iwinfo','info',{device=device}) or {}
                local devstate=call('network.device','status',{name=device})
                if not devstate or devstate.up~=false then
                    local assoc=call('iwinfo','assoclist',{device=device})
                    local peers,available={},assoc~=nil and type(assoc.results)=='table'
                    if available then
                        for _,p in ipairs(assoc.results) do
                            if mac(p.mac) then
                                if #peers>=128 or total>=512 then limited=true; break end
                                local h=hints[p.mac:lower()] or {}
                                peers[#peers+1]={mac=p.mac:lower(),ip=text(h.ip),name=text(h.name),
                                    signal=p.signal,noise=p.noise,rx=p.rx,tx=p.tx}
                                total=total+1
                            end
                        end
                    elseif cfg.mode=='ap' then
                        local hosts=call('hostapd.'..device,'get_clients')
                        if hosts and type(hosts.clients)=='table' then
                            available=true
                            for m,p in pairs(hosts.clients) do
                                if mac(m) and type(p)=='table' and (p.assoc==true or p.authorized==true) then
                                    if #peers>=128 or total>=512 then limited=true; break end
                                    local h=hints[m:lower()] or {}
                                    peers[#peers+1]={mac=m:lower(),ip=text(h.ip),name=text(h.name),signal=p.signal}
                                    total=total+1
                                end
                            end
                        end
                    end
                    local bssid=mac(info.bssid) and info.bssid:lower() or ''
                    local valid_bssid=bssid~='' and bssid~='00:00:00:00:00:00' and tonumber(bssid:sub(1,2),16)%2==0
                    local associated=cfg.mode=='ap' or valid_bssid
                    local association_known=cfg.mode=='ap' or valid_bssid or bssid=='00:00:00:00:00:00'
                    -- rpcd returns an empty results array even when the driver query fails.
                    -- Missing station details cannot negate a live upstream BSSID.
                    if cfg.mode=='sta' and not association_known and available and #peers==1 then
                        local candidate=peers[1].mac
                        if candidate~='00:00:00:00:00:00' and tonumber(candidate:sub(1,2),16)%2==0 then
                            associated,association_known,bssid=true,true,candidate
                        end
                    end
                    local routes={}
                    for _,n in ipairs(dump.interface or {}) do
                        if n.up==true and name(n.l3_device) and (n.device==device or n.l3_device==device) then
                            local addresses=routes[n.l3_device] or {}
                            for _,key in ipairs({'ipv4-address','ipv6-address'}) do
                                for _,a in ipairs(n[key] or {}) do
                                    if #addresses<32 and type(a.address)=='string' then addresses[#addresses+1]=a.address end
                                end
                            end
                            routes[n.l3_device]=addresses
                        end
                    end
                    local probe,ips,nroutes='',{},0
                    for dev,addresses in pairs(routes) do probe,ips,nroutes=dev,addresses,nroutes+1 end
                    if nroutes~=1 then probe,ips='',{} end
                    networks[#networks+1]={interface=device,section=text(iface.section),radio=text(radio),mode=cfg.mode,
                        ssid=text(info.ssid or cfg.ssid),bssid=bssid,channel=info.channel,frequency=info.frequency,
                        signal=info.signal,noise=info.noise,associated=associated,association_known=association_known,peers_available=available,peers=peers,
                        probe_interface=probe,source_ips=ips,rx_bytes=counter(device,'rx'),tx_bytes=counter(device,'tx')}
                end
            end
        end
    end
end
bus:close()
io.write(json.stringify({ok=true,result={schema=1,networks=networks,limited=limited}}))
"""


def status_command(lua_bin='lua'):
    if lua_bin not in _LUA_BINS:
        raise ValueError('Unsupported Lua interpreter')
    return ("command -v " + lua_bin + " >/dev/null 2>&1 || { printf '%s' '{\"ok\":false,\"code\":\"dependencies\"}'; exit 0; }; " + lua_bin + " - <<'LUXIAOGUAN_WIRELESS_STATUS'\n" + STATUS_LUA + "\nLUXIAOGUAN_WIRELESS_STATUS")
