"""Plan removal of old wireless networks as one relocation transaction."""
NETWORK_CLEANUP = r'''
local function move_cleanup_networks(c,owners,wireless_networks,preserved_networks)
 local protected={loopback=true,lan=true,wan=true,wan6=true}
 for section in pairs(preserved_networks) do protected[section]=true end
 local removed={}
 c:foreach("network","interface",function(n)
  local section=n[".name"]
  if not protected[section] and (owners[n.rm_owner] or wireless_networks[section] or
      section:match("^wwan%d*$") or n.proto=="relay") then removed[section]=true end
 end)
 local function references(n)
  for _,key in ipairs({"network","ifname","device","interface","in","out"}) do
   for _,ref in ipairs(move_words(n[key])) do
    if removed[ref] or removed[ref:gsub("^@","")] then return true end
   end
  end
  return false
 end
 -- Old relays can share uplinks or depend on one another; remove their closure.
 local changed=true
 while changed do
  changed=false
  c:foreach("network","interface",function(n)
   local section=n[".name"]
   if not protected[section] and not removed[section] and references(n) then
    removed[section]=true;changed=true
   end
  end)
 end
 c:foreach("network","interface",function(n)
  if protected[n[".name"]] and references(n) then fail("move_management") end
 end)
 local related={}
 c:foreach("network",nil,function(n)
  if n[".type"]~="interface" and n[".type"]~="device" and references(n) then
   related[#related+1]={config="network",section=n[".name"]}
  end
 end)
 c:foreach("dhcp","dhcp",function(d)
  for _,ref in ipairs(move_words(d.interface)) do
   if removed[ref] then related[#related+1]={config="dhcp",section=d[".name"]};break end
  end
 end)
 table.sort(related,function(a,b) return a.config.."."..a.section<b.config.."."..b.section end)
 return removed,related
end
'''
