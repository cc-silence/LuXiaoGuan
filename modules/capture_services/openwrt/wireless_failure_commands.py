"""Read-only, bounded wireless failure scene; secrets are removed on the router."""
import json
from .ap_readiness import AP_READINESS_LUA

SCENE_LUA = r"""
local json=require('luci.jsonc')
local c=require('uci').cursor()
local bus=require('ubus').connect()
local request=json.parse(__REQUEST__)
if not bus then io.write(json.stringify({schema=1,collection_status='disconnected'}));return end
__READINESS__
local secrets,configs,interfaces={},{},{}
local function remember(value)
 if type(value)=='string' and #value>0 then secrets[#secrets+1]=value
 elseif type(value)=='table' then for _,v in ipairs(value) do remember(v) end end
end
c:foreach('wireless','wifi-iface',function(s)
 for _,key in ipairs({'ssid','key','key1','key2','key3','key4','password','auth_secret','acct_secret','sae_password','private_key_passwd'}) do remember(s[key]) end
end)
c:foreach('system','system',function(s) remember(s.hostname) end)
table.sort(secrets,function(a,b) return #a>#b end)
local function scrub(text)
 for _,secret in ipairs(secrets) do
  local start,out=1,{}
  while true do
   local a,b=text:find(secret,start,true)
   if not a then out[#out+1]=text:sub(start);break end
   out[#out+1]=text:sub(start,a-1);out[#out+1]='[identity omitted]';start=b+1
  end
  text=table.concat(out)
 end
 text=text:gsub('%x%x:%x%x:%x%x:%x%x:%x%x:%x%x','[mac omitted]')
 text=text:gsub('%d+%.%d+%.%d+%.%d+','[address omitted]')
 return text
end
local runtime,wireless_error=bus:call('network.wireless','status',{})
runtime=type(runtime)=='table' and runtime or {}
local selected=c:get_all('wireless',request.radio) or {}
local radio={}
for _,key in ipairs({'band','channel','htmode','country','disabled'}) do
 local v=selected[key]
 if key=='channel' then radio[key]=v=='auto' and 'auto' or tonumber(v)
 elseif key=='band' and (v=='2g' or v=='5g' or v=='6g') then radio[key]=v
 elseif key=='disabled' then radio[key]=v=='1' or v=='true'
 elseif key=='country' and type(v)=='string' and v:match('^%u%u$') then radio[key]=v
 elseif key=='htmode' and type(v)=='string' and #v<=8 and (v:match('^HT%d+$') or v:match('^VHT%d+$') or v:match('^HE%d+$') or v:match('^EHT%d+$')) then radio[key]=v end
end
local index=0
c:foreach('wireless','wifi-iface',function(s)
 if s.device~=request.radio or #configs>=32 then return end
 index=index+1
 local cfg={index=index,selected=s['.name']==request.section,mode=s.mode=='ap' and 'ap' or s.mode=='sta' and 'sta' or 'other',
 disabled=s.disabled=='1' or s.disabled=='true',has_name=type(s.ssid)=='string' and #s.ssid>0,
 has_key=type(s.key)=='string' and #s.key>0,has_network=s.network~=nil}
 local encryption=s.encryption
 local base=type(encryption)=='string' and encryption:match('^[^+]+') or ''
 local allowed={none=true,psk=true,psk2=true,['psk-mixed']=true,sae=true,['sae-mixed']=true,owe=true,wpa=true,wpa2=true,wpa3=true}
 cfg.encryption=allowed[base] and base or 'other'
 for _,key in ipairs({'hidden','wds','isolate','ieee80211w'}) do
  if s[key]=='0' or s[key]=='1' or s[key]=='2' then cfg[key]=tonumber(s[key]) end
 end
 configs[#configs+1]=cfg
 local rs=runtime[request.radio] or {}
 for _,iface in ipairs(rs.interfaces or {}) do
  if iface.section==s['.name'] and type(iface.ifname)=='string' and iface.ifname:match('^[%w_.:%-]+$') and #interfaces<32 then
   local proof={index=index,radio_up=rs.up==true,runtime_pending=rs.pending==true,interface_present=true}
   if s.mode=='ap' then proof.ready=ap_ready(bus,iface.ifname,s.ssid,proof) end
   local flags=ap_file(iface.ifname,'flags')
   flags=type(flags)=='string' and flags:match('^0x(%x+)%s*$') or nil
   flags=flags and tonumber(flags,16)
   proof.kernel_flags_readable=flags~=nil
   if flags then proof.kernel_interface_up=flags%2==1 end
   local carrier=ap_file(iface.ifname,'carrier')
   if type(carrier)=='string' and carrier:match('^[01]%s*$') then proof.ap_carrier=carrier:match('^1')~=nil end
   proof.kernel_name_present=false
   -- Inspect kernel data even when carrier is absent; do not let a failed predicate hide the scene.
   local pipe=io.popen("iw dev '"..iface.ifname.."' info 2>/dev/null")
   if pipe then
    local text=pipe:read(8193);local closed=pipe:close()
    proof.kernel_info_available=(closed==true or closed==0) and type(text)=='string' and #text<=8192
    if proof.kernel_info_available then
     for line in text:gmatch('[^\r\n]+') do
      local mode=line:match('^%s*type%s+(.+)$')
      if mode then proof.ap_mode_matches=mode=='AP' end
      local ssid=line:match('^%s*ssid (.*)$')
      if ssid then proof.kernel_name_present=true;proof.ssid_matches=ap_unescape_ssid(ssid)==s.ssid end
      local ch,freq=line:match('^%s*channel%s+(%d+)%s+%((%d+)%s+MHz%)')
      if ch then proof.channel=tonumber(ch);proof.frequency=tonumber(freq) end
     end
    end
   end
   interfaces[#interfaces+1]=proof
  end
 end
end)
local logs={}
local log_status='unavailable'
local pipe=io.popen('logread 2>/dev/null | tail -n 160')
if pipe then
 local text=pipe:read(65537);local closed=pipe:close()
 if (closed==true or closed==0) and type(text)=='string' then
  log_status=#text>65536 and 'truncated' or 'completed'
  for line in text:sub(-65536):gmatch('[^\r\n]+') do
   local lower=line:lower()
   if (lower:find('hostapd',1,true) or lower:find('netifd',1,true) or lower:find('wpa_supplicant',1,true) or
       lower:find('mt76',1,true) or lower:find('mt79',1,true) or lower:find('nl80211',1,true) or lower:find('dropbear',1,true)) then
    if lower:find('password',1,true) or lower:find('passphrase',1,true) or lower:find('psk',1,true) or lower:find('private key',1,true) or lower:find('key=',1,true) then
     logs[#logs+1]='[sensitive wireless line omitted]'
    else logs[#logs+1]=scrub(line):sub(1,512) end
    if #logs>32 then table.remove(logs,1) end
   end
  end
 end
end
bus:close()
io.write(json.stringify({schema=1,collection_status='completed',wireless_error=wireless_error,radio_config=radio,
 configs=configs,interfaces=interfaces,service_log=logs,service_log_status=log_status}))
"""


def failure_scene_command(radio, section):
    payload=json.dumps(dict(radio=radio,section=section),ensure_ascii=True)
    body=SCENE_LUA.replace('__REQUEST__',json.dumps(payload)).replace('__READINESS__',AP_READINESS_LUA)
    return ("for c in lua lua5.4 lua5.3 lua5.1; do\n"
            "if command -v \"$c\" >/dev/null 2>&1; then\n"
            "\"$c\" - <<'ROUTERMANAGER_FAILURE_SCENE'\n"+body+
            "\nROUTERMANAGER_FAILURE_SCENE\nexit $?\nfi\ndone\nexit 127")
