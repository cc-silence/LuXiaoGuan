"""Compare netifd's loaded wireless configuration before declaring readiness."""
WIRELESS_RUNTIME = r'''
local function runtime_scalar(value)
 if value==true then return "1" end
 if value==false then return "0" end
 return value~=nil and tostring(value) or nil
end
local function wireless_loaded(c,rs,section)
 local row=nil
 for _,iface in ipairs(rs.interfaces or {}) do if iface.section==section then row=iface;break end end
 if not row or type(row.config)~="table" or type(rs.config)~="table" then return false end
 for _,key in ipairs({"mode","ssid","encryption","key","macaddr"}) do
  local expected=c:get("wireless",section,key)
  if expected~=nil and runtime_scalar(row.config[key])~=runtime_scalar(expected) then return false end
 end
 local radio=c:get("wireless",section,"device")
 for _,key in ipairs({"channel","htmode","country","txpower"}) do
  local expected=c:get("wireless",radio,key)
  if expected~=nil and runtime_scalar(rs.config[key])~=runtime_scalar(expected) then return false end
 end
 return true
end
'''
