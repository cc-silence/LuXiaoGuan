"""Reuse capture's router-side management path for pre-apply warnings."""
from dataclasses import dataclass
import re
from .management_link import read_management_link
from .signal_manager import SignalError


@dataclass(frozen=True)
class ManagementConnection:
    kind: str = "unknown"
    risk: str = "unknown"


def match_management_signal(request, link, runtime):
    if link.status == "wired":
        return ManagementConnection("wired", "independent")
    if link.status != "wireless" or not re.fullmatch(r"phy[0-9]+", link.phy):
        return ManagementConnection()
    if not isinstance(runtime, dict):
        return ManagementConnection("wifi")
    interfaces = runtime.get("interfaces")
    if interfaces == {}:
        interfaces = []
    if not isinstance(interfaces, list) or len(interfaces) > 512:
        return ManagementConnection("wifi")
    matches = [row for row in interfaces if isinstance(row, dict) and row.get("interface") == link.interface]
    if len(matches) == 1:
        row = matches[0]
        if not all(isinstance(row.get(key), str) and row[key] for key in ("section", "radio", "mode")):
            return ManagementConnection("wifi")
        target_phy = runtime.get("target_phy")
        known_phy = isinstance(target_phy, str) and bool(re.fullmatch(r"phy[0-9]+", target_phy))
        same_radio = request.radio == row["radio"]
        if same_radio and known_phy and target_phy != link.phy:
            return ManagementConnection("wifi")
        if request.section and request.section == row["section"] and request.mode == row["mode"] and same_radio:
            return ManagementConnection("wifi", "same_signal")
        if same_radio or target_phy == link.phy:
            return ManagementConnection("wifi", "same_radio")
        return ManagementConnection("wifi", "other_radio" if known_phy else "unknown")
    if not matches and runtime.get("target_phy") == link.phy:
        return ManagementConnection("wifi", "same_radio")
    return ManagementConnection("wifi")


def inspect_management_connection(manager, request, cancel):
    try:
        link = read_management_link(manager.runner, cancel)
        if link.status != "wireless":
            return match_management_signal(request, link, {})
        data = manager._call("management_path", cancel, timeout=8, radio=request.radio)
    except InterruptedError:
        raise
    except SignalError as exc:
        if exc.code == "cancelled":
            raise InterruptedError(str(exc)) from exc
        return ManagementConnection()
    except (RuntimeError, TimeoutError):
        return ManagementConnection()
    return match_management_signal(request, link, data)


def management_connection_notice(connection):
    if connection.risk == "same_signal":
        return "已确认：当前登录连接正经过您正在编辑的这个信号。应用后 Wi-Fi 和管理连接会暂时断开；请等待信号恢复，重新连接 Wi-Fi，再重新登录路由器。"
    if connection.risk == "same_radio":
        return "已确认：当前用于管理路由器的信号与本次修改共用一块无线网卡。应用可能使当前 Wi-Fi 和管理连接暂时断开；恢复后请重新连接并登录。"
    if connection.kind == "wired":
        return "路由器已核实：当前登录连接经过有线接口。无线设置仍可能影响该网卡上的其他信号或中继上网。"
    if connection.risk == "other_radio":
        return "路由器已核实：当前登录连接经过另一块无线网卡。应用设置仍可能短暂影响无线或网络服务；若连接中断，请在恢复后重新登录。"
    return "无法确认当前管理连接是否依赖本次修改的 Wi-Fi。应用可能使 Wi-Fi 和管理连接暂时断开，恢复后请重新连接并登录。"
