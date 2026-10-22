"""接管专用配置事务；复用信号管理的锁、UCI 校验及设备内备份。"""


TAKEOVER_LUA = r'''
local function takeover_config()
    if type(request.token) ~= "string" or #request.token ~= 32 or
        not request.token:match("^%x+$") or not valid_name(request.radio) then fail("unsupported") end
    clean()
    local base = "/etc/winbox-wireless-backups"
    local dir = base .. "/capture-" .. request.token
    local current = all(cursor())
    if request.action == "takeover_prepare" then
        local radio = current[request.radio]
        if not radio or radio[".type"] ~= "wifi-device" or disabled(radio.disabled) ~= false then
            fail("unsupported")
        end
        local count = 0
        for name, s in pairs(current) do
            if s[".type"] == "wifi-iface" and s.device == request.radio then
                if s[".anonymous"] or not valid_name(name) or disabled(s.disabled) == nil or
                    (s.mode ~= "ap" and s.mode ~= "sta") then fail("unsupported") end
                if not disabled(s.disabled) then count = count + 1 end
            end
        end
        if count == 0 then fail("unsupported") end
        local revision = hash()
        if not shell("test ! -L /etc/config/wireless && test -f /etc/config/wireless && " ..
            "test ! -L " .. base .. " && umask 077 && mkdir -p " .. base .. " && chmod 700 " .. base ..
            " && mkdir " .. dir .. " && chmod 700 " .. dir .. " && cp /etc/config/wireless " ..
            dir .. "/wireless && chmod 600 " .. dir .. "/wireless && mkdir " .. dir .. "/delta && " ..
            "cmp -s /etc/config/wireless " .. dir .. "/wireless") then fail("backup") end
        clean()
        if hash() ~= revision then fail("changed") end
        local f = io.open(dir .. "/radio", "w")
        if not f then fail("backup") end
        local written = f:write(request.radio)
        local closed = f:close()
        if not written or not closed then fail("backup") end
        return {prepared=true, count=count}
    end
    if not shell("test ! -L " .. base .. " && test ! -L " .. dir .. " && test -d " .. dir ..
        " && test ! -L " .. dir .. "/wireless && test -f " .. dir .. "/wireless && test ! -L " ..
        dir .. "/radio && test ! -L " .. dir .. "/delta") then fail("backup") end
    local f = io.open(dir .. "/radio", "r")
    if not f then fail("backup") end
    local saved_radio = f:read("*a"); f:close()
    if saved_radio ~= request.radio then fail("changed") end
    local original_cursor = uci.cursor(dir, dir .. "/delta")
    if not original_cursor or not original_cursor:load("wireless") then fail("backup") end
    local original = all(original_cursor)
    local expected = all(original_cursor)
    local targets = {}
    for name, s in pairs(original) do
        if s[".type"] == "wifi-iface" and s.device == request.radio and disabled(s.disabled) == false then
            if s[".anonymous"] or not valid_name(name) then fail("unsupported") end
            targets[#targets+1] = name
            expected[name].disabled = "1"
        end
    end
    if #targets == 0 then fail("backup") end
    local restoring = request.action == "takeover_restore"
    if not restoring and request.action ~= "takeover_disable" then fail("unsupported") end
    if not same(current, original) and not same(current, expected) then fail("changed") end
    local desired = restoring and original or expected
    if not same(current, desired) then
        local revision = hash()
        local isolated = cursor(dir .. "/delta")
        if not same(all(isolated), current) then fail("changed") end
        for _, name in ipairs(targets) do
            local value = desired[name].disabled
            local ok
            if value == nil then ok = isolated:delete("wireless", name, "disabled")
            else ok = isolated:set("wireless", name, "disabled", value) end
            if not ok then fail("write") end
        end
        if not same(all(isolated), desired) then fail("changed") end
        clean()
        if hash() ~= revision then fail("changed") end
        if not isolated:commit("wireless") then fail("commit") end
        clean()
        if not same(all(cursor()), desired) then fail("unconfirmed") end
    end
    if not shell("/sbin/wifi reload " .. request.radio) then fail("unconfirmed") end
    return {committed=true}
end
'''
