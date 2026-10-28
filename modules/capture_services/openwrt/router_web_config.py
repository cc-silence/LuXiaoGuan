"""Select only the unique active standard LuCI document root."""
from .uci_booleans import shell_bool


def management_server(snapshot):
    servers=[]
    for name,row in snapshot['configs'].get('uhttpd',{}).items():
        if row.get('.type')!='uhttpd' or not shell_bool(row.get('enabled'),default=True):continue
        if str(row.get('home','')).rstrip('/')!='/www':continue
        if not row.get('listen_http') and not row.get('listen_https'):continue
        servers.append((name,row))
    if len(servers)==1:return servers[0]
    if not servers:raise ValueError('\u672a\u627e\u5230\u53ef\u786e\u8ba4\u7684 /www \u7ba1\u7406\u7f51\u9875\u670d\u52a1\uff0c\u672a\u4fee\u6539\u7f51\u9875\u914d\u7f6e\u3002')
    raise ValueError('\u591a\u4e2a\u542f\u7528\u7684\u7f51\u9875\u670d\u52a1\u4f7f\u7528 /www\uff0c\u65e0\u6cd5\u786e\u5b9a\u76ee\u6807\uff1a'+', '.join(n for n,_ in servers))


def homepage_server(snapshot):
    """Only replace the file the standard server actually uses as its first index."""
    name,server=management_server(snapshot)
    pages=server.get('index_page',[])
    if isinstance(pages,str):pages=pages.split()
    if pages and pages[0]!='index.html':
        raise ValueError("\u7ba1\u7406\u7f51\u9875\u4f7f\u7528\u81ea\u5b9a\u4e49\u9996\u9875\u987a\u5e8f\uff0c\u672a\u4fee\u6539\u3002\u8bf7\u5148\u5728\u8def\u7531\u5668\u7f51\u9875\u670d\u52a1\u8bbe\u7f6e\u4e2d\u786e\u8ba4 index.html \u4e3a\u9996\u9009\u9996\u9875\u6587\u4ef6\u3002")
    return name,server
