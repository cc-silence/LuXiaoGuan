"""根据完整安装计划和包元数据核算空间，不以压缩包大小代替安装占用。"""
import io
import math
import re
import tarfile
from .command_builder import quote_argument
from .signal_manager import SignalError

_NAME = r"[A-Za-z0-9][A-Za-z0-9+_.-]*"


def planned_packages(manager, plan):
    """只统计计划中的新增包；升级替换先由公共计划校验拒绝。"""
    if manager == "opkg":
        found = re.findall(r"(?m)^[ \t]*Installing (" + _NAME + r") \(([^)]+)\)", plan)
    else:
        found = re.findall(r"(?m)^\s*\(\s*\d+/\s*\d+\) Installing (" + _NAME + r") \(([^)]+)\)", plan)
    if len(found) > 64:
        raise SignalError("environment", "安装计划超过空间核算上限，未执行安装。")
    return dict(found)


def required_kb(sizes):
    """安装体积之外预留百分之十和每包 64KB，供配置文件及包数据库使用。"""
    values = tuple(sizes)
    if not values:
        return 0
    if any(type(v) is not int or v < 0 or v > 1024**3 for v in values):
        raise SignalError("environment", "安装体积元数据无效，未执行安装。")
    return math.ceil(sum(values) * 1.1 / 1024) + 64 * len(values)


def parse_opkg_sizes(text, planned):
    """读取包元数据的字节数；缺项或版本不匹配时停止，不猜大小。"""
    sizes = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        fields = dict(re.findall(r"(?m)^([A-Za-z-]+):[ \t]*(.*)$", block))
        name = fields.get("Package")
        if name in planned and fields.get("Version") == planned[name]:
            size = fields.get("Installed-Size", "")
            if size.isdigit():
                sizes[name] = max(sizes.get(name, 0), int(size))
    if set(sizes) != set(planned):
        raise SignalError("environment", "无法读取完整安装体积，未执行安装：" + "、".join(sorted(set(planned) - set(sizes))))
    return tuple(sizes.values())


def parse_apk_sizes(text, planned):
    """兼容原生 apk 的字节数和 KiB/MiB 显示，按计划中的准确版本匹配。"""
    sizes = {}
    pattern = r"(?m)^(\S+) installed size:\s*\n([0-9]+(?:\.[0-9]+)?)[ \t]*(B|KiB|MiB|GiB|kB|MB|GB)?[ \t]*$"
    identifiers = {name + "-" + version: name for name, version in planned.items()}
    units = {"": 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
             "kB": 1000, "MB": 1000**2, "GB": 1000**3}
    for identifier, number, unit in re.findall(pattern, text):
        if identifier in identifiers:
            sizes[identifiers[identifier]] = max(sizes.get(identifiers[identifier], 0), math.ceil(float(number) * units[unit]))
    if set(sizes) != set(planned):
        raise SignalError("environment", "无法读取完整 apk 安装体积，未执行安装：" + "、".join(sorted(set(planned) - set(sizes))))
    return tuple(sizes.values())


def check_plan_space(manager, package_manager, plan, cancel, *, repositories=""):
    """先按真实计划读取新增包体积，再按总量检查设备空间。"""
    names = planned_packages(package_manager.name, plan)
    if not names:
        return
    arguments = " ".join(quote_argument(name) for name in names)
    if package_manager.name == "opkg":
        command = "opkg info " + arguments
    else:
        command = "apk info --size " + (("--repositories-file " + quote_argument(repositories) + " ") if repositories else "") + arguments
    text = manager._execute_stage("dependency_install_sizes", command, 40, cancel, "核算安装占用")
    sizes = (parse_opkg_sizes if package_manager.name == "opkg" else parse_apk_sizes)(text, names)
    manager._preflight(required_kb(sizes), cancel)


def ipk_installed_size(path):
    """只读 IPK 控制信息；兼容 tar 和 ar 外壳，不解压到磁盘。"""
    def control_size(blob):
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
            for item in archive:
                if item.name.lstrip("./") == "control" and item.isfile() and item.size <= 512*1024:
                    text = archive.extractfile(item).read().decode("utf-8")
                    value = re.search(r"(?m)^Installed-Size:[ \t]*(\d+)[ \t]*$", text)
                    if value:
                        return int(value[1])
        raise ValueError("Missing Installed-Size")
    try:
        if path.stat().st_size > 64*1024*1024:
            raise ValueError("Package too large")
        with path.open("rb") as stream:
            magic = stream.read(8)
            if magic == b"!<arch>\n":
                for _ in range(64):
                    header = stream.read(60)
                    if not header:
                        break
                    if len(header) != 60 or header[58:] != b"`\n":
                        raise ValueError("Invalid ar header")
                    size = int(header[48:58])
                    if not 0 <= size <= 64*1024*1024:
                        raise ValueError("Invalid ar member")
                    name = header[:16].decode("ascii").strip().rstrip("/")
                    if name.startswith("control.tar"):
                        return control_size(stream.read(size))
                    stream.seek(size + size % 2, 1)
            else:
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:*") as archive:
                    for item in archive:
                        if item.name.lstrip("./").startswith("control.tar") and item.isfile() and item.size <= 2*1024*1024:
                            return control_size(archive.extractfile(item).read())
        raise ValueError("Missing control archive")
    except (ValueError, OSError, tarfile.TarError, UnicodeError) as exc:
        raise SignalError("environment", "无法核算安装包占用，未执行安装：" + path.name) from exc
