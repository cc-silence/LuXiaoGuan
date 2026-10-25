"""UCI switches follow their consumers, not one guessed universal truth table.

See openwrt/uci blob.c and OpenWrt lib/functions.sh get_bool.
"""


NETIFD_BOOL_LUA = r'''
local function netifd_bool(value,default)
 if value==nil then return default end
 if value=="1" or value=="true" then return true end
 if value=="0" or value=="false" then return false end
 return nil
end
'''


def _parse(value,default,positive,negative):
    if value is None:return default
    if isinstance(value,str):
        if value in positive:return True
        if value in negative:return False
    raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u5f00\u5173\u914d\u7f6e\u7684\u542b\u4e49\uff0c\u8bf7\u5728\u8def\u7531\u5668\u8bbe\u7f6e\u4e2d\u6838\u5bf9\u3002")


def netifd_bool(value,*,default):
    return _parse(value,default,("1","true"),("0","false"))


def shell_bool(value,*,default):
    return _parse(value,default,("1","on","true","yes","enabled"),("0","off","false","no","disabled"))
