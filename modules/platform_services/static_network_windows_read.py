"""Fast read-only IPv4 snapshots through Qt, registry and Windows IP Helper."""
import ctypes as c
import ipaddress
import socket
from .lan_network_windows import inspect
from .static_network import StaticMigrationError


class Sock4(c.Structure):
    _fields_=[('family',c.c_uint16),('port',c.c_uint16),('address',c.c_ubyte*4),('padding',c.c_ubyte*8)]
class Sock6(c.Structure):
    _fields_=[('family',c.c_uint16),('port',c.c_uint16),('flow',c.c_uint32),('address',c.c_ubyte*16),('scope',c.c_uint32)]
class Sock(c.Union):
    _fields_=[('v4',Sock4),('v6',Sock6)]
class Prefix(c.Structure):
    _fields_=[('address',Sock),('length',c.c_ubyte)]
class Route(c.Structure):
    _fields_=[('luid',c.c_uint64),('index',c.c_uint32),('destination',Prefix),('next_hop',Sock),
              ('site_prefix',c.c_ubyte),('valid_lifetime',c.c_uint32),('preferred_lifetime',c.c_uint32),
              ('metric',c.c_uint32),('protocol',c.c_uint32),('loopback',c.c_ubyte),('autoconfigure',c.c_ubyte),
              ('publish',c.c_ubyte),('immortal',c.c_ubyte),('age',c.c_uint32),('origin',c.c_uint32)]
class Table(c.Structure):
    _fields_=[('count',c.c_uint32),('rows',Route*1)]


def default_routes(index):
    api=c.WinDLL('iphlpapi',use_last_error=True)
    api.GetIpForwardTable2.argtypes=[c.c_uint16,c.POINTER(c.c_void_p)]
    api.GetIpForwardTable2.restype=c.c_uint32
    api.FreeMibTable.argtypes=[c.c_void_p];api.FreeMibTable.restype=None
    pointer=c.c_void_p()
    code=api.GetIpForwardTable2(socket.AF_INET,c.byref(pointer))
    if code:raise OSError(code,'IPv4 route inspection failed')
    try:
        table=c.cast(pointer,c.POINTER(Table)).contents
        if table.count>65536:raise StaticMigrationError('Invalid native route table size')
        rows=(Route*table.count).from_address(pointer.value+Table.rows.offset)
        return [(socket.inet_ntop(socket.AF_INET,bytes(row.next_hop.v4.address)),int(row.metric))
                for row in rows if row.index==index and row.destination.length==0
                and bytes(row.destination.address.v4.address)==b'\0'*4]
    finally:api.FreeMibTable(pointer)


def read(adapter):
    rows=inspect(adapter_id=adapter['id'])
    row=next((r for r in rows if r['id']==adapter['id']),None)
    if not row or row['index']!=adapter['index'] or row['mac'].replace(':','').replace('-','').upper()!=adapter['mac'].replace(':','').replace('-','').upper() or row.get('dhcp') is not False or not row.get('connected'):
        raise StaticMigrationError('Static adapter identity or mode changed')
    routes=default_routes(row['index'])
    if len(routes)>2 or len({metric for _,metric in routes})>1:
        raise StaticMigrationError('Ambiguous default routes')
    dns=[]
    for value in row['dns']:
        try:dns.append(str(ipaddress.IPv4Address(value)))
        except ValueError:continue
    return dict(addresses=sorted(row['addresses']),gateways=sorted(gateway for gateway,_ in routes),
                dns=dns,metric=routes[0][1] if routes else 0)
