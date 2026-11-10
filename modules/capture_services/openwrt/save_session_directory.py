"""Locate only our rpcd UCI delta directory, including randomized newer rpcd."""
SAVE_SESSION_DIRECTORY = r'''
local function save_session_directory(config)
 local nonce=req.save_nonce
 if type(nonce)~="string" or #nonce~=32 or not nonce:match("^%x+$") then fail("save_unsupported") end
 local marker="lxg_save_"..nonce
 local global=uci.cursor()
 if not global:load(config) or global:get(config,marker)~=nil then fail("changed") end
 rpc("uci","add",{ubus_rpc_session=sid(),config=config,type="lxg_save_probe",name=marker,values={nonce=nonce}})
 local ok,result=pcall(function()
  local paths=read('n=0; for d in /tmp/run/rpcd/uci-*; do if test -d "$d" && test ! -L "$d"; then n=$((n+1)); test "$n" -le 256 || { printf "overflow\n"; break; }; printf "%s\n" "$d"; fi; done')
  local found=nil
  for path in paths:gmatch("[^\r\n]+") do
   local suffix=path:match("^/tmp/run/rpcd/uci%-(%x+)$")
   if not suffix or #suffix~=32 then fail("save_unsupported") end
   local c=uci.cursor("/etc/config",path)
   if c:get_confdir()~="/etc/config" or c:get_savedir()~=path then fail("save_unsupported") end
   local owned=false
   if c:load(config) then
    owned=c:get(config,marker,"nonce")==nonce
    c:unload(config)
   end
   if owned then
    if found then fail("save_unsupported") end
    found=path
   end
  end
  if not found then fail("save_unsupported") end
  return found
 end)
 local removed=pcall(function()
  rpc("uci","delete",{ubus_rpc_session=sid(),config=config,section=marker})
 end)
 if not removed then fail("save_recovery") end
 if not ok then error(result,0) end
 return result
end
'''
