"""Read-only orphan inventory and explicit, revision-bound configuration cleanup."""
ORPHANS = r'''
local function orphan_words(value)
 if type(value)=="table" then return value end
 local out={};for word in tostring(value or ""):gmatch("%S+") do out[#out+1]=word end
 return out
end
local function orphan_token(c)
 clean()
 if not c:load("dhcp") then fail("read") end
 local changes=c:changes("dhcp")
 if type(changes)~="table" or next(changes) then fail("pending") end
 local dh=dhcp_revision();if #dh~=64 then fail("read") end
 return revision()..dh
end
local function orphan_preview()
 local c=clean();local token=orphan_token(c)
 local dump=rpc("network.interface","dump",{})
 if type(dump.interface)~="table" then fail("orphan_runtime") end
 local count=0
 for key in pairs(dump.interface) do
  count=count+1
  if count>512 or type(key)~="number" or key%1~=0 or key<1 then fail("orphan_runtime") end
 end
 if count~=#dump.interface then fail("orphan_runtime") end
 local runtime={}
 for i,state in ipairs(dump.interface) do
  if i>512 or type(state)~="table" or type(state.interface)~="string" or runtime[state.interface] then fail("orphan_runtime") end
  runtime[state.interface]=state
 end
 local rows={}
 c:foreach("network","interface",function(n)
  local section=n[".name"]
  local owned=type(n.rm_owner)=="string" and name(n.rm_owner) and
    (section:match("^wbnet_%x+$")~=nil or section:match("^wbr_%x+$")~=nil)
  if not owned and not section:match("^wwan%d*$") then return end
  if not name(section) then fail("section") end
  local reasons,seen={},{}
  local function block(reason) if not seen[reason] then reasons[#reasons+1]=reason;seen[reason]=true end end
  if n.rm_owner and c:get("wireless",n.rm_owner) then block("owner_exists") end
  if n.device or n.ifname or n.type or n.master then block("physical_binding") end
  if n.proto~="dhcp" and n.proto~="dhcpv6" then block("unsupported_protocol") end
  if n.rm_dhcp_backup then block("recovery_metadata") end
  local zones={}
  for _,config in ipairs({"wireless","network","firewall","dhcp"}) do
   c:foreach(config,nil,function(s)
    for key,value in pairs(s) do
     if key:sub(1,1)~="." and key~="rm_owner" and key~="key" and key~="ssid" and
        key~="hostname" and key~="description" and not (config=="network" and s[".name"]==section) then
      for _,word in ipairs(orphan_words(value)) do
       if word==section or word=="@"..section then
        if config=="firewall" and s[".type"]=="zone" and key=="network" then
         zones[#zones+1]=s.name or s[".name"]
        else block(config.."_reference") end
       end
      end
     end
    end
   end)
  end
  local state=runtime[section]
  if state then
   if state.up==true or state.pending==true or
      (state.l3_device~=nil and state.l3_device~="") or (state.device~=nil and state.device~="") then block("runtime_active")
   elseif state.up~=false or state.pending~=false then block("runtime_unknown") end
  end
  table.sort(zones);table.sort(reasons)
  rows[#rows+1]={section=section,owner=n.rm_owner or "",proto=n.proto or "",zones=zones,
                reasons=reasons,eligible=#reasons==0,source=owned and "owned" or "unknown"}
  if #rows>128 then fail("orphan_large") end
 end)
 table.sort(rows,function(a,b) return a.section<b.section end)
 if token~=orphan_token(c) then fail("changed") end
 return {token=token,rows=rows}
end
local function orphan_selection(review)
 if review.token~=req.review_token or type(req.selected)~="table" or #req.selected<1 or #req.selected>128 then fail("changed") end
 local allowed={};for _,row in ipairs(review.rows) do if row.eligible then allowed[row.section]=true end end
 local selected={}
 for _,section in ipairs(req.selected) do
  if not name(section) or not allowed[section] or selected[section] then fail("orphan_selection") end
  selected[section]=true
 end
 return selected
end
local function orphan_cleanup()
 local before=orphan_preview();local selected=orphan_selection(before)
 local c=clean()
 for section in pairs(selected) do remove("network",section) end
 c:foreach("firewall","zone",function(z)
  local values,changed={},false
  for _,network in ipairs(orphan_words(z.network)) do
   if selected[network] then changed=true else values[#values+1]=network end
  end
  if changed then
   if #values==0 then remove("firewall",z[".name"],"network") else set("firewall",z[".name"],{network=values}) end
  end
 end)
 -- Recheck live interface state immediately before the bounded commit.
 orphan_selection(orphan_preview())
 local result=save_session_commit(function()
  local check=clean()
  for section in pairs(selected) do if check:get("network",section) then fail("save_commit") end end
 end)
 result.removed=req.selected
 return result
end
'''
