"""无线设置的设备端原生事务；只返回白名单，不把密码或配置正文带回电脑。"""
import json
from .command_builder import quote_argument
from .signal_commands import _LUA_BINS
from .wireless_options import IFACE_KEYS, RADIO_KEYS
from .wireless_capabilities import CAPABILITIES_LUA, RADIO_SETTINGS, IFACE_SETTINGS
from .wireless_bridge_commands import BRIDGE
from .wireless_bridge_status import STATUS
from .wireless_bridge_toggle import TOGGLE
from .wireless_delete_commands import DELETE
from .wireless_identity_commands import IDENTITY
from .pending_config_commands import PENDING
from .save_only_commands import SAVE_ONLY
from .common_settings_commands import COMMON_SETTINGS
from .orphan_interface_commands import ORPHANS
from .relocation_commands import RELOCATION
from .wireless_scan_commands import SCAN
from .tool_runtime_probe import TOOL_RUNTIME_SH

BOOT = r'''
local j_ok,json=pcall(require,"luci.jsonc")
local u_ok,uci=pcall(require,"uci")
local b_ok,ubus=pcall(require,"ubus")
if not (j_ok and u_ok and b_ok) then
 io.write('{"ok":false,"code":"dependencies"}'); return
end
local req=json.parse(arg[1])
local bus=ubus.connect()
local function fail(code) error({code=code},0) end
if not bus then io.write('{"ok":false,"code":"runtime"}');return end
local function rpc(object,method,args)
 local value,err=bus:call(object,method,args or {})
 if err then fail("rpc_"..object.."_"..method.."_"..tostring(err)) end
 return value or {}
end
local function optional(object,method,args)
 local value,err=bus:call(object,method,args or {})
 return not err and type(value)=="table" and value or {}
end
local function name(v) return type(v)=="string" and v:match("^[%w_]+$") end
local function q(v) return "'"..tostring(v):gsub("'","'\\''").."'" end
local function read(command)
 local p=io.popen(command.." 2>/dev/null")
 if not p then fail("read") end
 local value=p:read("*a") or "";p:close();return value
end
local function revision()
 local result={}
 for _,config in ipairs({"wireless","network","firewall"}) do
  local path="/etc/config/"..config
  local f=read("test ! -L "..path.." && sha256sum "..path):match("^(%x+)")
  if not f or #f~=64 then fail("read") end
  result[#result+1]=f
 end
 return table.concat(result)
end
local function clean(readonly)
 local c=uci.cursor()
 for _,config in ipairs({"wireless","network","firewall"}) do
  if not c:load(config) then fail("read") end
  local changes=c:changes(config)
  if type(changes)~="table" or (not readonly and next(changes)) then fail("pending") end
 end
 return c
end
local function fields(s,keys)
 local out={}
 for _,k in ipairs(keys) do
  if type(s[k])=="string" or type(s[k])=="table" then out[k]=s[k] end
 end
 return out
end
__CAPABILITIES__
local sid,set,add
__BRIDGE_HELPERS__
__BRIDGE_STATUS__
local function inspect(readonly)
 local c=clean(readonly);local before=revision()
 local status=rpc("network.wireless","status",{})
 local validation=optional("network.wireless","get_validate",{})
 local radios,rows,networks,zones,active={},{},{},{},{}
 local runtime_macs={}
 c:foreach("network","interface",function(s)
  local out=fields(s,{"proto","ipaddr","netmask","gateway","dns","metric","device","type","rm_owner","network","rm_dhcp_backup","hostname"})
  out.name=s[".name"];networks[#networks+1]=out
 end)
 c:foreach("firewall","zone",function(s)
  zones[#zones+1]={section=s[".name"],name=s.name,network=s.network,masq=s.masq,input=s.input,output=s.output,forward=s.forward}
 end)
 c:foreach("wireless","wifi-device",function(s)
  if not name(s[".name"]) then fail("radio") end
  local phy=optional("iwinfo","phyname",{section=s[".name"]}).phyname or ""
  local rs=status[s[".name"]] or {};local ifnames={}
  for _,iface in ipairs(rs.interfaces or {}) do
   if type(iface.ifname)=="string" and iface.ifname:match("^[%w_.:%-]+$") then
    ifnames[#ifnames+1]=iface.ifname
    local ds=optional("network.device","status",{name=iface.ifname})
    if type(iface.section)=="string" and type(ds.macaddr)=="string" and #ds.macaddr==17 then runtime_macs[iface.section]=ds.macaddr end
    if rs.up==true and not rs.pending and ds.up==true then active[#active+1]=iface.section end
   end
  end
  local caps=""
  if type(phy)=="string" and phy:match("^phy%d+$") then caps=read("iw phy "..q(phy).." info") else phy="" end
  local device=ifnames[1] or phy
  local info=device~="" and optional("iwinfo","info",{device=device}) or {}
  local out=fields(s,{"type","band","hwmode","channel","htmode","country","txpower",__RADIO_KEYS__,"disabled"})
  out.name=s[".name"];out.phy=phy;out.interfaces=ifnames;out.runtime_up=rs.up==true;out.runtime_pending=rs.pending==true;out.capabilities=caps:sub(1,32768)
  out.channels=optional("iwinfo","freqlist",{device=device}).results or {}
  out.powers=optional("iwinfo","txpowerlist",{device=device}).results or {}
  out.countries=optional("iwinfo","countrylist",{device=device}).results or {}
  out.htmodes=info.htmodes or {}
  out.option_support=option_support(validation[s[".name"]])
  radios[#radios+1]=out
 end)
 c:foreach("wireless","wifi-iface",function(s)
  local out=fields(s,{"ssid","mode","device","network","encryption","hidden","isolate","maxassoc","description","rm_bridge","rm_wds_backup","rm_suspended","rm_identity_backup","macaddr","disabled","bssid","wmm","ieee80211w","dtim_period",__IFACE_KEYS__})
  out.runtime_mac=runtime_macs[s[".name"]]
  out.section=s[".name"];out.has_key=type(s.key)=="string" and #s.key>0
  rows[#rows+1]=out
 end)
 if before~=revision() then fail("changed") end
 return {revision=before,dhcp_revision=dhcp_revision(),radios=radios,rows=rows,networks=networks,zones=zones,active=active}
end

local function management_path()
 local c=uci.cursor()
 if not c:load("wireless") then fail("read") end
 local out={}
 for radio,rs in pairs(rpc("network.wireless","status",{})) do
  if name(radio) and type(rs)=="table" and rs.up==true and not rs.pending then
   for _,iface in ipairs(rs.interfaces or {}) do
    local row=type(iface.section)=="string" and c:get_all("wireless",iface.section) or nil
    if row and (row.mode=="ap" or row.mode=="sta") and row.device==radio and type(iface.ifname)=="string" and iface.ifname:match("^[%w_.:%-]+$") then
     local device=optional("network.device","status",{name=iface.ifname})
     if device.up==true then
      out[#out+1]={section=iface.section,radio=radio,interface=iface.ifname,mode=row.mode}
      if #out>512 then fail("response") end
     end
    end
   end
  end
 end
 local target=name(req.radio) and optional("iwinfo","phyname",{section=req.radio}).phyname or ""
 return {interfaces=out,target_phy=type(target)=="string" and target or ""}
end
local function session()
 local value=rpc("session","create",{timeout=300})
 local sid=value.ubus_rpc_session
 if type(sid)~="string" or not sid:match("^%x+$") or #sid~=32 then fail("session") end
 local ok,err=pcall(function()
  local objects={{"wireless","read"},{"wireless","write"},{"network","read"},{"network","write"},{"firewall","read"},{"firewall","write"},{"dhcp","read"},{"dhcp","write"}}
  if req.relocation==true then objects[#objects+1]={"system","read"};objects[#objects+1]={"system","write"} end
  rpc("session","grant",{ubus_rpc_session=sid,scope="uci",objects=objects})
 end)
 if not ok then optional("session","destroy",{ubus_rpc_session=sid});error(err,0) end
 return {sid=sid}
end
sid=function()
 if type(req.sid)~="string" or #req.sid~=32 or not req.sid:match("^%x+$") then fail("session") end
 return req.sid
end
set=function(config,section,values)
 if not name(section) then fail("section") end
 rpc("uci","set",{ubus_rpc_session=sid(),config=config,section=section,values=values})
end
add=function(config,kind,section,values)
 if not name(section) then fail("section") end
 rpc("uci","add",{ubus_rpc_session=sid(),config=config,type=kind,name=section,values=values})
end
__IDENTITY__
__PENDING__
__SAVE_ONLY__
__COMMON_SETTINGS__
local function stage()
 local c=clean()
 if revision()~=req.revision then fail("changed") end
 local v=req.values;local radio=v.radio
 if not name(radio) or c:get("wireless",radio)~="wifi-device" then fail("radio") end
 if type(req.token)~="string" or not req.token:match("^%x+$") or #req.token~=16 then fail("session") end
 local section=v.section~="" and v.section or ("wb_"..req.token)
 if not name(section) then fail("section") end
 local old=c:get_all("wireless",section)
 if v.section~="" and (not old or old.mode~=v.mode or old.device~=radio) then fail("changed") end
 if v.section=="" and old then fail("changed") end
 local opts=v.options or {}
 if not req.temporary and old and ((old.rm_bridge and opts.bridge_mode=="wds") or (old.rm_wds_backup and opts.bridge_mode=="relayd")) then fail("bridge_transition") end

 if old and old.rm_wds_backup and not req.temporary and opts.bridge_mode~="wds" then
  if old.rm_suspended~="1" then restore_dhcp(c,old.rm_wds_backup) end;remove("wireless",section,"rm_wds_backup")
 end
 if old and old.rm_bridge and not req.temporary and opts.bridge_mode~="relayd" then restore_bridge(c,old,false) end
 local bridged=v.mode=="sta" and not req.temporary and opts.bridge_mode=="wds"
 local network=(old and old.rm_wds_backup and not bridged and not req.temporary and ("wbnet_"..req.token)) or (bridged and opts.network) or (v.mode=="ap" and opts.network) or (old and old.network) or (v.mode=="ap" and "lan" or ("wbnet_"..req.token))
 if type(network)=="table" and #network==1 then network=network[1] end
 if v.mode=="sta" and old and not bridged then
  if network=="lan" or network=="loopback" then fail("shared_network") end
  c:foreach("wireless","wifi-iface",function(s)
   if s[".name"]~=section then
    local ns=type(s.network)=="table" and s.network or {s.network}
    for _,n in ipairs(ns) do if n==network then fail("shared_network") end end
   end
  end)
 end
 if type(network)=="table" and v.mode=="ap" then
  if #network==0 then fail("network") end
  for _,n in ipairs(network) do if not name(n) or c:get("network",n)~="interface" then fail("network") end end
 elseif type(network)~="string" or not name(network) then fail("network") end
 local wf={ssid=v.ssid,mode=v.mode,device=radio,network=network,disabled=req.save_only==true and "1" or "0"}
 if v.mode=="ap" then
  if opts.macaddr~=nil then
   if not identity_mac(opts.macaddr) then fail("identity") end
   identity_unique(c,section,opts.macaddr)
   wf.macaddr=opts.macaddr
  end
  if type(network)~="table" and c:get("network",network)~="interface" then fail("network") end
  wf.encryption=opts.encryption or (old and old.encryption) or "psk2"
  for _,k in ipairs({"hidden","isolate","maxassoc","wmm","ieee80211w","dtim_period",__IFACE_KEYS__}) do if opts[k]~=nil then wf[k]=opts[k] end end
  if opts.maclist then
   wf.maclist={};for address in opts.maclist:gmatch("%S+") do wf.maclist[#wf.maclist+1]=address end
  end
  local rf={}
  for _,k in ipairs({"channel","htmode","country","txpower",__RADIO_KEYS__}) do if opts[k]~=nil then rf[k]=opts[k] end end
  if next(rf) then set("wireless",radio,rf) end
 elseif bridged then
  if c:get("network",network)~="interface" then fail("network") end
  wf.encryption=opts.encryption or (old and old.encryption) or "psk2"
  wf.description=v.label;wf.wds="1"
  check_downstream(c,network,section,nil)
  if old and old.rm_wds_backup and old.rm_suspended~="1" then verify_dhcp_disabled(c,old.rm_wds_backup) end
  if old and old.rm_wds_backup and old.network~=network then fail("bridge_retarget") end
  wf.rm_wds_backup=old and old.rm_suspended~="1" and old.rm_wds_backup or disable_dhcp(c,network,req.token)
  if req.save_only==true and old and old.rm_wds_backup and old.rm_suspended~="1" then restore_dhcp(c,old.rm_wds_backup) end
  if opts.bssid and opts.bssid~="" then wf.bssid=opts.bssid end
  if opts.channel then set("wireless",radio,{channel=opts.channel}) end
 else
  wf.wds="0"
  if opts.channel then set("wireless",radio,{channel=opts.channel}) end
  wf.encryption=opts.encryption or (old and old.encryption) or "psk2"
  wf.description=v.label
  if opts.bssid and opts.bssid~="" then wf.bssid=opts.bssid end
  local nf={proto=req.temporary and "none" or (opts.proto or (old and c:get("network",network,"proto")) or "dhcp")}
  if not req.temporary then
   for _,k in ipairs({"ipaddr","netmask","gateway","metric"}) do if opts[k] then nf[k]=opts[k] end end
   if opts.dns and opts.dns~="" then
    nf.dns={};for address in opts.dns:gmatch("%S+") do nf.dns[#nf.dns+1]=address end
   end
  end
  if c:get("network",network)=="interface" then set("network",network,nf)
  else nf.rm_owner=section;add("network","interface",network,nf) end
  if not req.temporary then
   if opts.bridge_mode=="relayd" then wf.rm_bridge=stage_relay(c,opts,network,section,req.token,old) end
   local found=false
   c:foreach("firewall","zone",function(z)
    local members=type(z.network)=="table" and z.network or {}
    if type(z.network)=="string" then for n in z.network:gmatch("%S+") do members[#members+1]=n end end
    local updated={};for _,n in ipairs(members) do if n~=network then updated[#updated+1]=n end end
    if z.name==(opts.zone or "wan") then
     found=true;updated[#updated+1]=network
    end
    if #updated~=#members or z.name==(opts.zone or "wan") then set("firewall",z[".name"],{network=updated}) end
   end)
   if not found then fail("zone") end
  end
 end
 if v.password~="" then wf.key=v.password end
 if wf.encryption~="none" and wf.encryption~="owe" and v.password=="" and not (old and type(old.key)=="string" and #old.key>0) then fail("password") end
 if req.save_only==true and (wf.rm_bridge or wf.rm_wds_backup) then wf.rm_suspended="1"
 elseif old and old.rm_suspended and not req.temporary then remove("wireless",section,"rm_suspended") end
 if old then set("wireless",section,wf) else add("wireless","wifi-iface",section,wf) end
 local expected_mac=v.mode=="ap" and (opts.macaddr or (old and identity_mac(old.macaddr) and old.macaddr)) or nil
 if v.mode=="sta" and not req.temporary then expected_mac=stage_identity(c,old,section,network,opts,bridged) end
 if v.mode=="sta" and old and opts.bssid=="" and old.bssid then
  rpc("uci","delete",{ubus_rpc_session=sid(),config="wireless",section=section,option="bssid"})
 end
 if v.mode=="sta" and not req.temporary and not bridged then
  local original=c:get_all("network",network) or {}
  for _,key in ipairs({"ipaddr","netmask","gateway","dns"}) do
   local clear=(key=="dns" and opts.dns=="") or (opts.proto=="dhcp" and key~="dns")
   if clear and original[key] then rpc("uci","delete",{ubus_rpc_session=sid(),config="network",section=network,option=key}) end
  end
  if opts.dns~=nil then set("network",network,{peerdns=opts.dns=="" and "1" or "0"}) end
 end
 if (wf.encryption=="none" or wf.encryption=="owe") and old and old.key then
  rpc("uci","delete",{ubus_rpc_session=sid(),config="wireless",section=section,option="key"})
 end
 -- Verification temporarily stops this radio's business; rollback restores original values.
 c:foreach("wireless","wifi-iface",function(s)
  if req.save_only~=true and s.device==radio and s[".name"]~=section and (req.temporary or (v.mode=="sta" and s.mode=="sta")) then
   set("wireless",s[".name"],{disabled="1"})
  end
 end)
 if req.save_only~=true then set("wireless",radio,{disabled="0"}) end
 if revision()~=req.revision then fail("changed") end
 if req.save_only~=true then rpc("uci","apply",{ubus_rpc_session=sid(),rollback=true,timeout=90}) end
 return {section=section,network=network,expected_mac=expected_mac}
end
local function saved_status()
 local c=clean();local before=revision();local v=req.values or {};local opts=v.options or {}
 local row=c:get_all("wireless",req.section)
 if not row or row.mode~=v.mode or row.device~=v.radio or row.ssid~=v.ssid or row.disabled~=(req.save_only==true and "1" or "0") then fail("unconfirmed") end
 if v.password~="" and row.key~=v.password then fail("unconfirmed") end
 local keys=v.mode=="ap" and {"macaddr","encryption","hidden","isolate","maxassoc","wmm","ieee80211w","dtim_period",__IFACE_KEYS__} or {"encryption","bssid"}
 for _,key in ipairs(keys) do
  if opts[key]~=nil and not (key=="bssid" and opts[key]=="") and key~="maclist" and row[key]~=opts[key] then fail("unconfirmed") end
 end
 if v.mode=="ap" then
  if opts.network and row.network~=opts.network then fail("unconfirmed") end
  if opts.maclist then
   local wanted={};for address in opts.maclist:gmatch("%S+") do wanted[#wanted+1]=address:lower() end
   local actual=type(row.maclist)=="table" and row.maclist or {row.maclist}
   if #wanted~=#actual then fail("unconfirmed") end
   for i,address in ipairs(wanted) do if type(actual[i])~="string" or actual[i]:lower()~=address then fail("unconfirmed") end end
  end
 end
 if v.mode=="sta" then
  if row.description~=v.label or row.network~=req.network then fail("unconfirmed") end
  if opts.bssid=="" and row.bssid then fail("unconfirmed") end
  if req.expected_mac and row.macaddr~=req.expected_mac then fail("unconfirmed") end
  if opts.identity_action=="default" and row.macaddr then fail("unconfirmed") end
  if opts.hostname and c:get("network",req.network,"hostname")~=opts.hostname then fail("unconfirmed") end
  if opts.bridge_mode~="wds" then
   for _,key in ipairs({"proto","ipaddr","netmask","gateway","metric"}) do
    if opts[key]~=nil and c:get("network",req.network,key)~=opts[key] then fail("unconfirmed") end
   end

   local member,invalid=false,false
   c:foreach("firewall","zone",function(zone)
    local members=type(zone.network)=="table" and zone.network or {}
    if type(zone.network)=="string" then
     for item in zone.network:gmatch("%S+") do members[#members+1]=item end
    end
    for _,item in ipairs(members) do
     if item==req.network then
      if zone.name~=(opts.zone or "wan") or member then invalid=true end
      member=true
     end
    end
   end)
   if not member or invalid then fail("unconfirmed") end
   if opts.dns~=nil then
    local net=c:get_all("network",req.network) or {}
    if net.peerdns~=(opts.dns=="" and "1" or "0") then fail("unconfirmed") end
    local wanted,actual={},{}
    for address in opts.dns:gmatch("%S+") do wanted[#wanted+1]=address end
    if type(net.dns)=="table" then actual=net.dns
    elseif type(net.dns)=="string" then
     for address in net.dns:gmatch("%S+") do actual[#actual+1]=address end
    elseif net.dns~=nil then fail("unconfirmed") end
    if #wanted==0 and net.dns~=nil then fail("unconfirmed") end
    if #wanted~=#actual then fail("unconfirmed") end
    for i,address in ipairs(wanted) do if actual[i]~=address then fail("unconfirmed") end end
   end
  end
 end
 for _,key in ipairs({"channel","htmode","country","txpower",__RADIO_KEYS__}) do
  if opts[key]~=nil and c:get("wireless",v.radio,key)~=opts[key] then fail("unconfirmed") end
 end
 clean()
 if before~=revision() then fail("changed") end
 return {saved=true}
end
local function status()
 local rs=rpc("network.wireless","status",{})[req.radio] or {}
 local iface=nil
 for _,v in ipairs(rs.interfaces or {}) do if v.section==req.section then iface=v.ifname end end
 if type(iface)~="string" or not iface:match("^[%w_.:%-]+$") or rs.pending or rs.up~=true then return {ready=false} end
 if req.mode=="ap" then
  local c=uci.cursor()
  if not c:load("wireless") then return {ready=false} end
  local expected=req.expected_mac or c:get("wireless",req.section,"macaddr")
  if identity_mac(expected) then
   local actual=optional("network.device","status",{name=iface}).macaddr
   if type(actual)~="string" or actual:lower()~=expected:lower() then return {ready=false,identity_pending=true} end
  end
  return {ready=ap_ready(iface,c:get("wireless",req.section,"ssid"))}
 end
 if req.expected_mac and req.expected_mac~="" then
  local actual=optional("network.device","status",{name=iface}).macaddr
  if type(actual)~="string" or actual:lower()~=req.expected_mac:lower() then return {ready=false,identity_pending=true} end
 end
 local text=read("wpa_cli -i "..q(iface).." status")
 local state=text:match("\nwpa_state=([^\r\n]+)") or text:match("^wpa_state=([^\r\n]+)")
 local bssid=text:match("\nbssid=([^\r\n]+)") or text:match("^bssid=([^\r\n]+)")
 return bridge_status(iface,state=="COMPLETED",bssid)
end
__BRIDGE_TOGGLE__
__DELETE__
__RELOCATION__
__ORPHANS__
__SCAN__
local function run()
 if req.action=="common_read" then return common_read() end
 if req.action=="common_stage" then return common_stage() end
 if req.action=="common_commit" then return common_commit() end
 if req.action=="move_preview" then return move_preview() end
 if req.action=="move_stage" then return move_stage() end
 if req.action=="move_status" then return move_status() end
 if req.action=="pending_review" then return pending_review() end
 if req.action=="pending_commit" then return pending_commit() end
 if req.action=="inspect" then return inspect(false) end
 if req.action=="scan_environment" then return inspect(true) end
 if req.action=="password" then
  if not name(req.section) then fail("changed") end
  local c=clean();local before=revision()
  if before~=req.revision then fail("changed") end
  local row=c:get_all("wireless",req.section)
  if not row or row[".type"]~="wifi-iface" or (row.mode~="sta" and row.mode~="ap") then fail("changed") end
  local key=type(row.key)=="string" and row.key or ""
  if #key>256 then fail("password") end
  clean();if before~=revision() then fail("changed") end
  return {password=key}
 end
 if req.action=="management_path" then return management_path() end
 if req.action=="session" then return session() end
 if req.action=="delete_stage" then return delete_stage() end
 if req.action=="delete_status" then return delete_status() end
 if req.action=="stage_toggle" then return stage_toggle() end
 if req.action=="toggle_status" then return toggle_status() end
 if req.action=="toggle_saved_status" then return toggle_saved_status() end
 if req.action=="stage" then return stage() end
 if req.action=="status" then return status() end
 if req.action=="saved_status" then return saved_status() end
 if req.action=="orphan_preview" then return orphan_preview() end
 if req.action=="orphan_cleanup" then return orphan_cleanup() end
 if req.action=="save_only_commit" then return save_session_commit(function() saved_status() end) end
 if req.action=="discard_session" then rpc("session","destroy",{ubus_rpc_session=sid()});return {discarded=true} end
 if req.action=="revision" then return {revision=revision(),dhcp_revision=dhcp_revision()} end
 if req.action=="runtime" then
  local rs=rpc("network.wireless","status",{})[req.radio] or {};local active={}
  for _,iface in ipairs(rs.interfaces or {}) do
   if rs.up==true and not rs.pending and optional("network.device","status",{name=iface.ifname}).up==true then active[#active+1]=iface.section end
  end
  return {active=active,pending=rs.pending==true,up=rs.up==true}
 end
 if req.action=="scan" then return scan_native() end
 if req.action=="finish" then
  local method=req.confirm and "confirm" or "rollback"
  local _,err=bus:call("uci",method,{ubus_rpc_session=sid()})
  if err and not (req.unapplied and err==5) then fail("recovery") end
  rpc("session","destroy",{ubus_rpc_session=sid()})
  return {finished=true}
 end
 fail("action")
end
local ok,result=pcall(run)
bus:close()
if ok then io.write(json.stringify({ok=true,result=result}))
else io.write(json.stringify({ok=false,code=type(result)=="table" and result.code or "internal"})) end
'''

BOOT = BOOT.replace("__CAPABILITIES__", CAPABILITIES_LUA).replace("__DEVICE_OPTIONS__", ",".join(json.dumps(k) for k in RADIO_SETTINGS)).replace("__INTERFACE_OPTIONS__", ",".join(json.dumps(k) for k in IFACE_SETTINGS)).replace("__SCAN__", SCAN).replace("__ORPHANS__", ORPHANS).replace("__RELOCATION__", RELOCATION).replace("__PENDING__", PENDING).replace("__SAVE_ONLY__", SAVE_ONLY).replace("__COMMON_SETTINGS__", COMMON_SETTINGS).replace("__IDENTITY__", IDENTITY).replace("__BRIDGE_HELPERS__", BRIDGE).replace("__BRIDGE_STATUS__", STATUS).replace("__BRIDGE_TOGGLE__", TOGGLE).replace("__DELETE__", DELETE).replace("__IFACE_KEYS__", ",".join(json.dumps(k) for k in IFACE_KEYS)).replace("__RADIO_KEYS__", ",".join(json.dumps(k) for k in RADIO_KEYS))

PROBE = TOOL_RUNTIME_SH + r'''
for key in uci ubus iw wpa_cli hostapd wpa_supplicant rpcd; do
 if command -v "$key" >/dev/null 2>&1; then printf '%s=1\n' "$key";else printf '%s=0\n' "$key";fi
done
for key in iw wpa_cli;do
 if rm_tool_runtime "$key";then printf '%s_runtime=1\n' "$key";else printf '%s_runtime=0\n' "$key";fi
done
for key in uci session iwinfo network.wireless;do
 if ubus list "$key" 2>/dev/null | grep -Fx "$key" >/dev/null 2>&1;then printf 'bus_%s=1\n' "$key";else printf 'bus_%s=0\n' "$key";fi
done
module=0;test -f /usr/lib/rpcd/iwinfo.so && module=1;printf 'iwinfo_module=%s\n' "$module"
for feature in sae acs 11ac 11ax 11be 11r eap owe;do
 if hostapd -v"$feature" >/dev/null 2>&1;then printf 'ap_%s=1\n' "$feature";else printf 'ap_%s=0\n' "$feature";fi
done
if wpa_supplicant -vowe >/dev/null 2>&1;then printf 'sta_owe=1\n';else printf 'sta_owe=0\n';fi
if wpa_supplicant -vsae >/dev/null 2>&1;then printf 'sta_sae=1\n';else printf 'sta_sae=0\n';fi
rpcmethods=$(ubus -v list uci 2>/dev/null)
rollback=1
for method in apply confirm rollback;do
 case "$rpcmethods" in *\"$method\"*) :;; *) rollback=0;; esac
done
printf 'rollback=%s\n' "$rollback"
if command -v relayd >/dev/null 2>&1 && test -x /etc/init.d/relayd;then printf 'relayd=1\n';else printf 'relayd=0\n';fi 
'''

def wireless_command(action, lua_bin="lua", **values):
    """JSON 与 shell 双层隔离请求；长脚本由公共执行器经标准输入传输。"""
    if lua_bin not in _LUA_BINS:
        raise ValueError("不支持的 Lua 解释器。")
    payload = json.dumps(dict(action=action, **values), ensure_ascii=True)
    return lua_bin + " - " + quote_argument(payload) + " <<'WINBOX_WIRELESS_SETUP'\n" + BOOT + "\nWINBOX_WIRELESS_SETUP"
