"""匹配随包静态工具，只覆盖经过构建核验的设备 profile。"""
import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from modules.config_manager import BASE_DIR

# 构建完成后固定摘要；不信任用户可编辑清单提供的摘要。
BINARY_SHA256 = "6a49782c3cf4a122d3efc03bcdadbb70a2dc698d47b0c92b7f28b750f96969e7"
DIRECTORY = BASE_DIR / "bin" / "openwrt_compatible" / "hcxdumptool-6.3.4-aarch64-static"


def validate_static_elf(binary):
    """有界核对 AArch64 ELF，拒绝动态加载器及外部运行库依赖。"""
    if len(binary)<64 or len(binary)>8*1024*1024 or binary[:6]!=b"\x7fELF\x02\x01":
        raise ValueError("新版兼容工具不是有效的 64 位 ELF。")
    kind,machine=struct.unpack_from("<HH",binary,16)
    if kind not in (2,3) or machine!=183:
        raise ValueError("新版兼容工具的处理器架构不匹配。")
    offset=struct.unpack_from("<Q",binary,32)[0]
    size,count=struct.unpack_from("<HH",binary,54)
    if size!=56 or not 0<count<=128 or offset+size*count>len(binary):
        raise ValueError("新版兼容工具的 ELF 结构无效。")
    for index in range(count):
        values=struct.unpack_from("<IIQQQQQQ",binary,offset+index*size)
        flag,_,start,_,_,length,_,_=values
        if start+length>len(binary):
            raise ValueError("新版兼容工具的 ELF 分段已截断。")
        if flag==3:
            raise ValueError("新版兼容工具依赖外部加载器，已拒绝安装。")
        if flag==2:
            if length%16:
                raise ValueError("新版兼容工具的动态段无效。")
            for position in range(start,start+length,16):
                tag,_=struct.unpack_from("<QQ",binary,position)
                if tag==1:
                    raise ValueError("新版兼容工具依赖外部运行库，已拒绝安装。")


@dataclass(frozen=True)
class BundledTool:
    """固定来源的静态工具，与官方 IPK 候选沿用相同安装事务。"""
    path: Path
    version: str = "6.3.4-winbox-static1"
    size: int = 0
    sha256: str = BINARY_SHA256
    url: str = "winbox-bundled:hcxdumptool-6.3.4-aarch64-static"

    def read(self):
        """使用前再次核对普通文件、字节预算和固定摘要。"""
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("\u8def\u5c0f\u7ba1 随包新版工具缺失或为链接，请恢复软件文件后重试。")
        with self.path.open("rb") as stream:
            binary=stream.read(8*1024*1024+1)
        if len(binary)!=self.size or hashlib.sha256(binary).hexdigest()!=self.sha256:
            raise ValueError("\u8def\u5c0f\u7ba1 随包新版工具校验失败，旧工具未更改。")
        validate_static_elf(binary)
        return binary


def match_bundled_tool(device):
    """只匹配当前已核验的 23.05.5 Filogic profile，不猜其他架构。"""
    if (str(device.version)!="23.05.5" or getattr(device,"target","")!="mediatek/filogic"
            or device.package_architecture!="aarch64_cortex-a53"
            or device.package_manager!="opkg"):
        return None
    kernel=re.match(r"^(\d+)\.(\d+)",str(getattr(device,"kernel_version","")))
    if kernel is None or tuple(map(int,kernel.groups()))<(5,15):
        raise ValueError("当前内核版本未确认或低于新版工具要求，未执行安装。")
    path=DIRECTORY/"hcxdumptool"
    if not path.is_file() or path.is_symlink():
        raise ValueError("\u8def\u5c0f\u7ba1 缺少当前系统需要的新版兼容工具，未执行旧版本重复安装。")
    tool=BundledTool(path,size=path.stat().st_size,sha256=BINARY_SHA256)
    tool.read()
    return tool


def verify_modern_interface_api(runner, executable, cancel):
    """只读枚举接口；失败时不接管无线、不启用候选工具。"""
    from .command_builder import quote_argument
    from .command_runner import require_command_completion
    result=None
    try:
        result=runner.run('compatible_interface_api',quote_argument(executable)+' -L',
            timeout=10,cancel_event=cancel,output_limit=128*1024)
        require_command_completion(result)
        if result.exit_code:
            raise ValueError('新版工具的无线接口检查失败，未启用；原工具已保留。')
        if not re.search(r"(?m)^\s*\d+\s+\d+\s+[0-9a-fA-F]{12}\s+[0-9a-fA-F]{12}\s+.*\(NETLINK\)\s*$",result.stdout or ''):
            raise ValueError('新版工具尚未识别到可用无线接口，未启用；请确认无线设备已启用，再重新检查。')
    except InterruptedError:
        raise
    except Exception as primary:
        # Keep the command result outside ordinary messages; existing session export redacts it.
        from uuid import uuid4
        from .workspace import local_session_root
        try:
            directory=local_session_root()/uuid4().hex
            directory.mkdir()
            detail="stage: compatible_interface_api\noption: -L\nerror_type: "+type(primary).__name__+"\n"
            if result is not None:
                detail+="exit_code: "+str(result.exit_code)+"\n===== stdout =====\n"+(result.stdout or "")[:128*1024]
                detail+="\n===== stderr =====\n"+(result.stderr or "")[:128*1024]
            (directory/'compatible-wireless-diag.txt').write_text(detail,encoding='utf-8')
        except OSError as diagnostic_error:
            from modules.capture_services.capture_logging import LOGGER
            LOGGER.warning('Compatible diagnostic save failed type=%s',type(diagnostic_error).__name__)
            primary.args=(str(primary)+'诊断文件保存失败。',)
        else:
            primary.args=(str(primary)+'诊断已保存，可导出日志。诊断目录：'+str(directory),)
        raise


def require_preferred_tool(device, version):
    """已有匹配新版时阻止旧版反复接管；安装必须由用户确认。"""
    if version!="6.2.4":
        return
    if match_bundled_tool(device) is not None:
        from .hcx_capabilities import HcxCompatibilityError
        raise HcxCompatibilityError("当前仍在使用旧版 PMKID 工具，\u8def\u5c0f\u7ba1 已提供适用于本系统的新版。"
            "请到“路由器登录 → PMKID 工具”点击“检查并安装兼容工具”，安装后重新启动。"
            "本次尚未接管无线或开始采集。")
