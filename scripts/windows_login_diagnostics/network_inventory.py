"""Bounded IPv4 inventory using read-only IP Helper APIs; no PowerShell startup."""
import ctypes
import ipaddress
import struct
from ctypes import wintypes as w

MAX_TABLE = 1024 * 1024


def _address(value):
    return str(ipaddress.IPv4Address(struct.pack('<I', value)))


DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16


class AddressRow(ctypes.Structure):
    _fields_ = [(name, DWORD) for name in ('address','index','mask','broadcast','reassembly')]
    _fields_ += [('unused', WORD), ('kind', WORD)]


class RouteRow(ctypes.Structure):
    _fields_ = [(name, DWORD) for name in ('destination','mask','policy','gateway','index',
        'kind','protocol','age','next_hop_as','metric1','metric2','metric3','metric4','metric5')]


def _table(name, row_type):
    class Table(ctypes.Structure):
        _fields_ = [('count', DWORD), ('rows', row_type * 1)]
    api = ctypes.WinDLL('iphlpapi', use_last_error=True)
    function = getattr(api, name)
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.ULONG), w.BOOL]
    function.restype = w.ULONG
    size = w.ULONG(0)
    code = function(None, ctypes.byref(size), False)
    if code != 122 or not Table.rows.offset <= size.value <= MAX_TABLE:
        raise OSError(code, 'Network table size query failed')
    for attempt in range(2):
        capacity = size.value
        buffer = ctypes.create_string_buffer(capacity)
        code = function(buffer, ctypes.byref(size), False)
        # A network interface may appear between the size query and the read.
        if code == 122 and attempt == 0 and capacity < size.value <= MAX_TABLE:
            continue
        if code:
            raise OSError(code, 'Network table read failed')
        if not Table.rows.offset <= size.value <= capacity:
            raise ValueError('Invalid network table returned size')
        count = DWORD.from_buffer_copy(buffer).value
        offset, stride = Table.rows.offset, ctypes.sizeof(row_type)
        if count > 4096 or offset + count * stride > size.value:
            raise ValueError('Network table bounds exceeded')
        result = []
        for index in range(count):
            row = row_type.from_buffer_copy(buffer, offset + index * stride)
            result.append(tuple(getattr(row, field) for field, _ in row_type._fields_))
        return result
    raise OSError('Network table could not be read')


def _route(row):
    destination, mask, _, gateway, index, kind, protocol, age, _, metric, *_ = row
    return dict(InterfaceIndex=index, DestinationPrefix=str(ipaddress.IPv4Network(
        _address(destination) + '/' + _address(mask), strict=False)), NextHop=_address(gateway),
        RouteMetric=metric, RouteType=kind, Protocol=protocol, Age=age)


def inventory(address):
    address = str(ipaddress.IPv4Address(address))
    ipv4, routes = [], []
    for local, index, mask, _, _, _, kind in _table('GetIpAddrTable', AddressRow):
        ipv4.append(dict(InterfaceIndex=index, IPAddress=_address(local),
            PrefixLength=ipaddress.IPv4Network('0.0.0.0/' + _address(mask)).prefixlen, Type=kind))
    routes = [_route(row) for row in _table('GetIpForwardTable', RouteRow)]
    api = ctypes.WinDLL('iphlpapi', use_last_error=True)
    api.GetBestRoute.argtypes = [w.DWORD, w.DWORD, ctypes.c_void_p]
    api.GetBestRoute.restype = w.DWORD
    row = (w.DWORD * 14)()
    destination = struct.unpack('<I', ipaddress.IPv4Address(address).packed)[0]
    code = api.GetBestRoute(destination, 0, row)
    if code:
        selected = dict(status='failed', native_error=int(code))
    else:
        selected = dict(status='ok', data=_route(row))
        selected['candidate_local_addresses'] = [item['IPAddress'] for item in ipv4
                                                if item['InterfaceIndex'] == row[4]]
    return dict(ipv4=dict(status='ok', data=ipv4), routes=dict(status='ok', data=routes),
                selected_route=selected)


def system_inventory():
    import winreg
    path = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    fields = ('ProductName', 'DisplayVersion', 'CurrentBuildNumber', 'UBR', 'EditionID', 'InstallationType')
    result = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        for name in fields:
            try:result[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:pass
    return dict(status='ok', data=result)
