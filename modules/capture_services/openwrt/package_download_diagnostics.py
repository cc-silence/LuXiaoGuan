"""Bounded metadata for dependency downloads; never log raw URLs or exceptions."""
import os
import time
import urllib.parse


def endpoint(url):
    try:
        parsed=urllib.parse.urlsplit(url)
        host=parsed.hostname or "unknown"
        # A hostname cannot include control characters or arbitrary log text.
        if any(not (c.isalnum() or c in ".-:[]_") for c in host):host="invalid-host"
        port=parsed.port or (443 if parsed.scheme=="https" else 80)
        if ":" in host:host="["+host+"]"
        return f"{parsed.scheme or 'unknown'}://{host[:253]}:{port}"
    except (TypeError,ValueError):
        return "invalid-endpoint"


def configuration_lines(plans):
    """Read configuration only, without probing endpoints or exposing credentials."""
    lines=[]
    try:
        from modules import network_policy as policy
    except Exception as exc:
        lines.append("代理配置读取失败："+type(exc).__name__)
    else:
        for title,name in (("HTTP","winbox_http_url"),("SOCKS","winbox_socks_url"),("系统","system_proxy_url")):
            try:
                value=getattr(policy,name)()
                lines.append(title+"代理："+(endpoint(value) if value else "未配置或未检测到，跳过"))
            except Exception as exc:
                lines.append(title+"代理读取失败："+type(exc).__name__)
    lines.append("绕过代理环境规则（NO_PROXY）："+("已设置" if os.environ.get("no_proxy") or os.environ.get("NO_PROXY") else "未设置"))
    routes=["直连" if label=="直连" else endpoint(label) for label,_ in plans]
    lines.append("本次候选线路："+" -> ".join(routes))
    if not any(label!="直连" for label,_ in plans):
        lines.append("本次没有显式代理入口；代理软件的全局接管模式无法由此判断。")
    return lines


class AttemptDiagnostics:
    def __init__(self,url,route,timeout,round_number,bypass=None):
        self.target=endpoint(url);self.route=route;self.timeout=timeout
        self.round=round_number+1;self.started=time.monotonic()
        self.stage="建立连接/等待响应头"
        self.received=0;self.expected=None;self.status=None;self.bypass=bypass

    @property
    def channel(self):
        return "\u76f4\u8fde" if self.bypass else self.route.split("\uff08",1)[0]

    def short_start(self):
        return "\u6b63\u5728\u901a\u8fc7 "+self.channel+" \u4e0b\u8f7d\u2026"

    def short_result(self,failed=False):
        return (self.channel+" \u4e0b\u8f7d\u5931\u8d25\u3002" if failed else
                "\u4e0b\u8f7d\u5b8c\u6210\uff08"+self.channel+"\uff09\u3002")

    def start(self):
        bypass="；目标命中绕过代理规则" if self.bypass else ""
        return f"下载尝试 {self.round}/2：{self.target}；当前下载通道 {self.route}；单次等待上限 {self.timeout:.1f} 秒{bypass}"

    def result(self,exc=None):
        elapsed=max(0,time.monotonic()-self.started)
        state="成功" if exc is None else "失败"
        length=str(self.expected) if self.expected is not None else "未提供"
        result=f"下载{state}：{self.target}；候选入口 {self.route}；阶段 {self.stage}；耗时 {elapsed:.2f} 秒；已收 {self.received} 字节；响应长度 {length}"
        if self.status is not None:result+=f"；HTTP {self.status}"
        if exc is not None:
            reason=getattr(exc,"reason",exc)
            result+="；异常 "+type(exc).__name__+"/"+type(reason).__name__
            for key in ("errno","winerror","verify_code"):
                value=getattr(reason,key,None)
                if type(value) is int:result+=f"；{key}={value}"
            if isinstance(reason,TimeoutError) and self.stage!="读取响应内容":
                result+="；当前接口无法进一步区分连接、代理隧道和等待响应头超时"
        return result
