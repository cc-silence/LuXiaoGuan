"""Explicit initial-setup apply without a remote rollback timer."""
INITIAL_RECONNECT = r"""
local function initial_deferred_apply()
 if type(req.hashes)~="table" or not next(req.hashes) then fail("values") end
 for _,config in ipairs(configs) do
  if pending_hash("/etc/config/"..config)~=req.hashes[config] then fail("changed") end
 end
 -- A fresh SID must see NO_DATA. Never interfere with another pending rollback.
 local _,active=bus:call("uci","confirm",{ubus_rpc_session=sid()})
 if active~=5 then fail("pending") end
 local _,err=bus:call("uci","apply",{ubus_rpc_session=sid(),rollback=false})
 if err and err~=5 then fail("rpc_uci_apply_"..tostring(err)) end
 return {submitted=true,unchanged=err==5}
end
"""
