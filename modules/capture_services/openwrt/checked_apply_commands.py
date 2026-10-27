"""Shared rpcd safety primitives; file/service edits are outside this transaction."""
CHECKED_APPLY = r"""
local function apply_capabilities()
 local methods=read("ubus -v list uci")
 for _,method in ipairs({"apply","confirm","rollback"}) do
  if not methods:find('"'..method..'"',1,true) then return {supported=false} end
 end
 return {supported=true}
end
local function checked_apply()
 if type(req.hashes)~="table" or not next(req.hashes) then fail("apply_changed") end
 for config,digest in pairs(req.hashes) do
  if not name(config) or pending_hash("/etc/config/"..config)~=digest then fail("apply_changed") end
 end
 local _,err=bus:call("uci","apply",{ubus_rpc_session=sid(),rollback=true,timeout=90})
 if err==5 then return {unchanged=true} end
 if err then fail("rpc_uci_apply_"..tostring(err)) end
 return {applied=true}
end
local function checked_confirm()
 local _,err=bus:call("uci","confirm",{ubus_rpc_session=sid()})
 if err==5 then return {confirmed=false,inactive=true} end
 if err then fail("rpc_uci_confirm_"..tostring(err)) end
 optional("session","destroy",{ubus_rpc_session=sid()})
 return {confirmed=true}
end
local function checked_rollback()
 local _,err=bus:call("uci","rollback",{ubus_rpc_session=sid()})
 if err and err~=5 then fail("recovery") end
 if type(req.hashes)~="table" or not next(req.hashes) then fail("recovery") end
 for config,digest in pairs(req.hashes) do
  if not name(config) or pending_hash("/etc/config/"..config)~=digest then return {restored=false} end
 end
 optional("session","destroy",{ubus_rpc_session=sid()})
 return {restored=true}
end
"""
CHECKED_ACTIONS = r"""
 if req.action=="checked_network_load" then rpc("network","reload",{});return {loaded=true} end
 if req.action=="apply_capabilities" then return apply_capabilities() end
 if req.action=="checked_apply" then return checked_apply() end
 if req.action=="checked_confirm" then return checked_confirm() end
 if req.action=="checked_rollback" then return checked_rollback() end
"""
