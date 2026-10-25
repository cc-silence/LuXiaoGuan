"""Isolated, bounded UCI transactions for reviewed router policies."""
import json
from .command_builder import quote_argument
from .signal_commands import _LUA_BINS
from .pending_config_commands import PENDING
from .save_only_commands import SAVE_ONLY
from .wireless_identity_commands import IDENTITY
from .router_policy_files import FILES_LUA
from .wireless_capabilities import CAPABILITIES_LUA

CONFIGS = ("system", "network", "wireless", "firewall", "dhcp", "dropbear", "uhttpd", "luci", "fstab")
FILES = ("/www/index.html", "/etc/rc.button/failsafe", "/etc/init.d/luxiaoguan-wifi-policy")
BOOT = r'''
local json=require("luci.jsonc")
local uci=require("uci")
local ubus=require("ubus")
local req=json.parse(arg[1])
local bus=ubus.connect()
local function fail(code) error({code=code},0) end
local function q(v) return "'"..tostring(v):gsub("'","'\\''").."'" end
local function read(command)
 local p=io.popen(command.." 2>/dev/null")
 if not p then fail("read") end
 local value=p:read("*a") or "";p:close()
 if #value>1048576 then fail("large") end
 return value
end
local function name(v) return type(v)=="string" and v:match("^[%w_]+$") end
local function rpc(object,method,args)
 local value,err=bus:call(object,method,args or {})
 if err then fail("rpc_"..object.."_"..method) end
 return value or {}
end
local function optional(object,method,args)
 local value,err=bus:call(object,method,args or {})
 return not err and type(value)=="table" and value or {}
end
local function fields(s,keys)
 local out={}
 for _,key in ipairs(keys) do
  if type(s[key])=="string" or type(s[key])=="table" then out[key]=s[key] end
 end
 return out
end
local sid,set,add
sid=function()
 if type(req.sid)~="string" or not req.sid:match("^%x+$") or #req.sid~=32 then fail("session") end
 return req.sid
end
set=function(config,section,values)
 rpc("uci","set",{ubus_rpc_session=sid(),config=config,section=section,values=values})
end
add=function(config,kind,section,values)
 rpc("uci","add",{ubus_rpc_session=sid(),config=config,type=kind,name=section,values=values})
end
local function remove(config,section,option)
 rpc("uci","delete",{ubus_rpc_session=sid(),config=config,section=section,option=option})
end
__PENDING__
__SAVE__
__IDENTITY__
local configs=__CONFIGS__
local fixed_files=__FILES__
local visible={
 system={"hostname","timezone","zonename","enabled","server","enable_server","log_proto","conloglevel","cronloglevel","log_ip"},
 network={"proto","auto","disabled","device","type","ifname","ipaddr","netmask","gateway","broadcast","macaddr","hostname","name","network","rm_owner","ports"},
 wireless={"mode","ssid","device","network","encryption","macaddr","disabled","band","hwmode","channel","htmode","rm_identity_backup","rm_wds_backup","rm_bridge","lxg_boot_disabled"},
 firewall={"name","device","subnet","extra_src","extra_dest","src","dest","proto","icmp_type","family","target","dest_port","enabled","network","input","output","forward","src_ip","dest_ip","src_port","extra"},
 dhcp={"interface","quietdhcp","ra","dhcpv6","start","limit","ignore","dhcp_option"},dropbear={"Interface","DirectInterface","Port","enable"},
 uhttpd={"home","listen_http","listen_https","redirect_https"},luci={"lang"},fstab={"auto_mount","anon_swap"}
}
local function snapshot()
 local out={configs={},hashes={},files={},pages={},capabilities={},order={}}
 local c=uci.cursor()
 local count=0
 for _,config in ipairs(configs) do
  local hash=pending_hash("/etc/config/"..config)
  out.hashes[config]=hash
  if hash~="absent" then
   if not c:load(config) then fail("read") end
   local dirty=c:changes(config)
   if type(dirty)~="table" or next(dirty) then fail("pending") end
   local rows={};out.order[config]={}
   c:foreach(config,nil,function(s)
    count=count+1;if count>2048 or not name(s[".name"]) then fail("large") end
    local row=fields(s,visible[config]);row[".type"]=s[".type"];out.order[config][#out.order[config]+1]=s[".name"]
    rows[s[".name"]]=row
   end)
   out.configs[config]=rows
  end
 end
 for _,path in ipairs(fixed_files) do out.files[path]=pending_hash(path) end
 out.board=rpc("system","board",{})
 if type(out.board.release)~="table" or out.board.release.distribution~="OpenWrt" then fail("official") end
 local pages={
  wireless={"/www/luci-static/resources/view/network/wireless.js","/usr/lib/lua/luci/model/cbi/admin_network/wifi.lua"},
  interfaces={"/www/luci-static/resources/view/network/interfaces.js","/usr/lib/lua/luci/model/cbi/admin_network/network.lua"},
  overview={"/www/luci-static/resources/view/status/include/10_system.js","/usr/lib/lua/luci/view/admin_status/index.htm"},
  system={"/www/luci-static/resources/view/system/system.js","/usr/lib/lua/luci/model/cbi/admin_system/system.lua"}}
 for page,paths in pairs(pages) do
  for _,path in ipairs(paths) do
   if pending_shell("test -f "..q(path)) then out.pages[#out.pages+1]=page;break end
  end
 end
 out.capabilities.chinese=pending_shell("test -f /usr/lib/lua/luci/i18n/base.zh-cn.lmo -o -f /usr/lib/lua/luci/i18n/base.zh_Hans.lmo")
 out.capabilities.firewall3=pending_shell("test -x /sbin/fw3 && grep -Eq '(^|[^[:alnum:]_])fw3([^[:alnum:]_]|$)' /etc/init.d/firewall")
 out.capabilities.firewall4=pending_shell("test -x /sbin/fw4 && grep -Eq '(^|[^[:alnum:]_])fw4([^[:alnum:]_]|$)' /etc/init.d/firewall")
 out.capabilities.time=pending_shell("test -x /etc/init.d/sysntpd")
 out.capabilities.attended=pending_shell("test -x /etc/init.d/attendedsysupgrade")
 out.capabilities.mount=pending_shell("command -v block >/dev/null")
 out.capabilities.old_watch=pending_shell("sed '/^[[:space:]]*#/d' /etc/rc.local | grep -E 'watch_wifi[.]sh|keep_wifi[.]sh'")
 out.capabilities.boot_hook_enabled=pending_shell("test -L /etc/rc.d/S18luxiaoguan-wifi-policy && test \"$(readlink /etc/rc.d/S18luxiaoguan-wifi-policy)\" = ../init.d/luxiaoguan-wifi-policy")
 out.runtime={interface={}}
 for _,iface in ipairs(optional("network.interface","dump",{}).interface or {}) do
  local row=fields(iface,{"interface"});row["ipv4-address"]={};row.route={}
  for _,address in ipairs(iface["ipv4-address"] or {}) do
   row["ipv4-address"][#row["ipv4-address"]+1]={address=address.address,mask=address.mask}
  end
  for _,route in ipairs(iface.route or {}) do row.route[#row.route+1]={target=route.target,mask=route.mask} end
  out.runtime.interface[#out.runtime.interface+1]=row
  if #out.runtime.interface>512 then fail("large") end
 end
 out.devices={}
 for device,status in pairs(optional("network.device","status",{})) do
  if type(status)=="table" then out.devices[device]=fields(status,{"macaddr"}) end
 end
 for _,config in ipairs(configs) do
  if pending_hash("/etc/config/"..config)~=out.hashes[config] then fail("changed") end
 end
 return out
end
local allowed={
 system={hostname=true,timezone=true,zonename=true,enabled=true,server=true,enable_server=true,log_proto=true,conloglevel=true,cronloglevel=true},
 network={macaddr=true,hostname=true,ipaddr=true,netmask=true,broadcast=true},
 wireless={ssid=true,macaddr=true,disabled=true,device=true,mode=true,network=true,encryption=true,key=true,lxg_boot_disabled=true},
 firewall={name=true,src=true,dest=true,proto=true,icmp_type=true,family=true,target=true,dest_port=true,enabled=true},
 dhcp={quietdhcp=true},dropbear={Interface=true,DirectInterface=true},uhttpd={listen_http=true,listen_https=true,redirect_https=true},
 luci={lang=true},fstab={auto_mount=true,anon_swap=true}
}
local function check_values(config,values)
 if type(values)~="table" or not allowed[config] then fail("values") end
 for key,value in pairs(values) do
  if not allowed[config][key] then fail("values") end
  local function valid(v)
   if type(v)~="string" or #v>2048 then return false end
   -- Check ASCII control bytes explicitly; locale classes can reject UTF-8 bytes.
   for i=1,#v do local b=v:byte(i); if b<32 or b==127 then return false end end
   return true
  end
  if type(value)=="table" then
   if #value>64 then fail("values") end
   for _,v in ipairs(value) do if not valid(v) then fail("values") end end
  elseif not valid(value) then fail("values") end
 end
end
local function stage()
 local current=snapshot()
 if type(req.hashes)~="table" then fail("values") end
 for _,config in ipairs(configs) do if current.hashes[config]~=req.hashes[config] then fail("changed") end end
 if type(req.operations)~="table" or #req.operations>512 then fail("values") end
 local c=uci.cursor()
 for _,op in ipairs(req.operations) do
  if not name(op.section) or not allowed[op.config] then fail("values") end
  if op.action=="order" then
   if op.config~="firewall" or type(op.sections)~="table" or #op.sections>1024 then fail("values") end
   rpc("uci","order",{ubus_rpc_session=sid(),config="firewall",sections=op.sections})
  elseif op.action=="identity_group" then
   if op.config~="network" then fail("values") end
   stage_identity_group(c,op.section,op.members,op.hostname)
  elseif op.action=="identity" then
   if op.config~="wireless" then fail("values") end
   local old=c:get_all("wireless",op.section)
   identity_station_network(c,old,op.section,op.network,true)
   stage_identity(c,old,op.section,op.network,op.values,false)
  elseif op.action=="delete_section" then
   local old=c:get_all(op.config,op.section)
   if op.config~="wireless" or not old or old.mode~="ap" or old.ssid~="OpenWrt" or not op.section:match("^default_radio%d+$") then fail("values") end
   remove(op.config,op.section)
  elseif op.action=="delete" then
   if not allowed[op.config][op.option] then fail("values") end
   if c:get(op.config,op.section,op.option)~=nil then remove(op.config,op.section,op.option) end
  elseif op.action=="add" then
   check_values(op.config,op.values)
   if op.config~="firewall" and op.config~="wireless" then fail("values") end
   if c:get(op.config,op.section) then fail("changed") end
   add(op.config,op.kind,op.section,op.values)
  elseif op.action=="set" then
   check_values(op.config,op.values)
   if not c:get(op.config,op.section) then fail("changed") end
   set(op.config,op.section,op.values)
  else fail("values") end
 end
 return {staged=true}
end
local function commit()
 local c=uci.cursor()
 for _,config in ipairs(configs) do if pending_hash("/etc/config/"..config)~=req.hashes[config] then fail("changed") end end
 local result=save_session_commit(function()
  for _,op in ipairs(req.operations or {}) do
   if op.action=="order" then
    local actual={};local check=uci.cursor()
    check:foreach("firewall",nil,function(s) actual[#actual+1]=s[".name"] end)
    if not save_equal(actual,op.sections,0) then fail("save_commit") end
   end
  end
 end,configs,req.hashes)
 result.hashes={}
 for _,config in ipairs(configs) do result.hashes[config]=pending_hash("/etc/config/"..config) end
 return result
end
__FILES_LUA__
__AP_HELPERS__
local function policy_ap_ready()
 local ap=req.ap
 if type(ap)~="table" or not name(ap.radio) or not name(ap.section) then fail("values") end
 local radio=optional("network.wireless","status",{})[ap.radio] or {}
 if not radio.up or radio.pending then return {ready=false} end
 for _,iface in ipairs(radio.interfaces or {}) do
  if iface.section==ap.section and type(iface.ifname)=="string" then
   local dev=optional("network.device","status",{name=iface.ifname})
   local info=optional("iwinfo","info",{device=iface.ifname})
   if dev.macaddr and dev.macaddr:lower()==ap.mac:lower() and info.frequency and info.frequency>=2400 and info.frequency<2500 and ap_ready(iface.ifname,ap.ssid) then return {ready=true} end
  end
 end
 return {ready=false}
end
local function run()
 if req.action=="ap_ready" then return policy_ap_ready() end
 if req.action=="files_apply" then return policy_file_apply() end
 if req.action=="files_restore" then return policy_file_restore(req.backup_dir,policy_file_journal(req.backup_dir)) end
 if req.action=="snapshot" then return snapshot() end
 if req.action=="session" then
  local id=rpc("session","create",{timeout=300}).ubus_rpc_session
  if type(id)~="string" or #id~=32 or not id:match("^%x+$") then fail("session") end
  req.sid=id
  local objects={}
  for _,config in ipairs(configs) do objects[#objects+1]={config,"read"};objects[#objects+1]={config,"write"} end
  local ok,err=pcall(function() rpc("session","grant",{ubus_rpc_session=id,scope="uci",objects=objects}) end)
  if not ok then optional("session","destroy",{ubus_rpc_session=id});error(err,0) end
  return {sid=id}
 end
 if req.action=="stage" then return stage() end
 if req.action=="commit" then return commit() end
 if req.action=="discard" then rpc("session","destroy",{ubus_rpc_session=sid()});return {discarded=true} end
 fail("action")
end
local ok,result=pcall(run)
if bus then bus:close() end
io.write(json.stringify(ok and {ok=true,result=result} or {ok=false,code=type(result)=="table" and result.code or "internal"}))
'''
BOOT = BOOT.replace("__AP_HELPERS__",CAPABILITIES_LUA.replace("__DEVICE_OPTIONS__","").replace("__INTERFACE_OPTIONS__","")).replace("__FILES_LUA__",FILES_LUA).replace("__PENDING__",PENDING).replace("__SAVE__",SAVE_ONLY).replace("__IDENTITY__",IDENTITY).replace("__CONFIGS__", "{" + ",".join(json.dumps(c) for c in CONFIGS) + "}").replace("__FILES__", "{" + ",".join(json.dumps(p) for p in FILES) + "}")


def policy_command(action, lua_bin="lua", **values):
    if lua_bin not in _LUA_BINS:
        raise ValueError("Invalid Lua executable")
    payload=json.dumps(dict(action=action,**values),ensure_ascii=True,separators=(",",":"))
    return lua_bin+" - "+quote_argument(payload)+" <<'LXG_POLICY'\n"+BOOT+"\nLXG_POLICY"
