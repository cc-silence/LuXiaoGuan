"""OpenWrt 无线接管事务：快照、临时关闭、恢复校验。

只在目标频段没有空闲 radio、且用户已在主线程确认的前提下使用。实现原则：
新接管临时禁用原本启用的信号，结束后恢复 disabled 原值；旧记录仍按运行态恢复。
破坏性步骤之前保存设备备份与本地恢复记录；恢复失败保留凭据，不声称已经恢复。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Tuple
from uuid import uuid4

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.device_probe import read_iwdev_complete, probe_monitor_capability
from modules.capture_services.openwrt.radio_identity import status_radio_ifnames, uci_radio_bands, snapshot_radio_mapping


@dataclass(frozen=True)
class RadioTakeoverRecord:
    """一次无线接管的恢复凭据，只描述本任务动过的 radio。"""

    session_id: str
    radio: str
    phy: str
    interfaces: Tuple[str, ...] = field(default_factory=tuple)
    config_token: str = ""


class OpenWrtRadioTakeover:
    """负责被占用 radio 的安全接管与恢复，不负责页面弹窗。

    参数：command_runner 是已连接的 OpenWrtCommandRunner。所有远端操作都有超时和
    取消；任何歧义（phy 与 radio 无法唯一对应、快照为空）都直接拒绝，不猜测。
    """

    def __init__(self, command_runner):
        self.command_runner = command_runner
        self.pending_record = None
        self._config_lua = None

    def _config_command(self, action, record, cancel_event):
        """执行设备内配置事务；输出仅含白名单状态，不传回配置或凭据。"""
        from .signal_commands import signal_command, signal_probe_command, _LUA_BINS
        if self._config_lua is None:
            probe = self.command_runner.run("takeover_config_probe", signal_probe_command(), 20, cancel_event)
            if probe.exit_code != 0 or probe.cancelled or probe.timed_out:
                raise RuntimeError("信号接管依赖检查失败，未继续修改配置。")
            data = json.loads(probe.stdout).get("result", {})
            if not all(data.get(key) == 1 for key in ("lua", "uci", "ubus", "jsonc", "sha256sum")) or data.get("lua_bin") not in _LUA_BINS:
                raise RuntimeError("信号接管缺少依赖，请在信号管理页检查安装后重试。")
            self._config_lua = data["lua_bin"]
        result = self.command_runner.run(action, signal_command(
            action, self._config_lua, token=record.config_token, radio=record.radio), 60, cancel_event)
        if result.exit_code != 0 or result.cancelled or result.timed_out:
            raise RuntimeError("信号配置事务未完成，已保留恢复记录，请重新连接后核实。")
        data = json.loads(result.stdout)
        if data.get("ok") is not True:
            from .signal_manager import _ERRORS
            raise RuntimeError(_ERRORS.get(data.get("code"), "信号配置事务响应异常，恢复记录已保留。"))
        expected = "prepared" if action == "takeover_prepare" else "committed"
        if data.get("result", {}).get(expected) is not True:
            raise RuntimeError("信号配置事务未返回完成凭据，已停止并保留现场。")

    def prepare_config_takeover(self, phy, radio, session_id, cancel_event=None):
        """先备份原始配置与运行接口；此步骤不禁用信号。"""
        if self.pending_record is not None:
            raise RuntimeError("上次信号接管尚未恢复，不能开始新的接管。")
        supported, reason = probe_monitor_capability(self.command_runner, phy, cancel_event)
        if supported is not True:
            raise RuntimeError("当前无线驱动的监听能力未通过检查，未接管无线：" + reason)
        record = RadioTakeoverRecord(str(session_id), radio, phy,
            self._phy_interfaces(phy, "read_takeover_before", cancel_event), uuid4().hex)
        from .management_link import require_management_link
        require_management_link(self.command_runner, record.phy, cancel_event)
        self._config_command("takeover_prepare", record, cancel_event)
        return record

    def takeover_prepared(self, record, cancel_event=None):
        """调用方先持久化 journal，再禁用原本启用的信号并核实接口消失。"""
        self.pending_record = record
        self._config_command("takeover_disable", record, cancel_event)
        after = self._wait_phy_interfaces(record.phy, (), 15, "verify_takeover_down", cancel_event)
        if after:
            raise RuntimeError("信号禁用后仍有接口运行，已拒绝开始抓包并保留恢复记录。")
        return record

    def snapshot_wireless(self, cancel_event=None):
        """只读抓取无线配置与接口快照，返回原始文本。

        副作用：执行 `uci show wireless`、`iw dev`、`wifi status`；失败或快照为空时
        抛出 RuntimeError，调用方不得继续破坏性步骤。
        """

        result = self.command_runner.run(
            "snapshot_wireless",
            "uci show wireless 2>/dev/null; "
            "printf '\\n__WINBOX_SNAP_IWDEV__\\n'; iw dev 2>/dev/null; "
            "printf '\\n__WINBOX_SNAP_WIFISTATUS__\\n'; wifi status 2>/dev/null",
            timeout=20,
            cancel_event=cancel_event,
        )
        if result.exit_code != 0 or result.cancelled or result.timed_out:
            raise RuntimeError("无线快照抓取失败，拒绝接管无线网卡。")
        text = result.stdout or ""
        head = text.split("__WINBOX_SNAP_IWDEV__")[0]
        if not uci_radio_bands(head):
            raise RuntimeError("无线快照中没有无线配置，拒绝接管无线网卡。")
        from .radio_identity_probe import enrich_radio_identity
        return enrich_radio_identity(self.command_runner, text, cancel_event)

    def resolve_radio(self, phy, snapshot_text, cancel_event=None, expected_band=""):
        """把 phy（如 phy0）唯一映射到 UCI radio（如 radio0）。

        参数：phy 是 `iw dev` 中的编号；snapshot_text 是 snapshot_wireless 的结果。
        返回值：radio 名。以同一快照中 `iw dev` 接口与 `wifi status` 的归属交集映射，
        再与 UCI 频段交叉核对；无法唯一对应时拒绝，不能用设备路径猜测。
        """

        phy_name = str(phy or "").strip()
        if not phy_name.startswith("phy") or not phy_name[3:].isdigit():
            raise ValueError(f"无线 phy 名称不合法：{phy_name or '<空>'}")
        interfaces = {p: tuple(name for name, _ in rows)
                      for p, rows in _parse_iwdev_interfaces(snapshot_text).items()}
        mapping = snapshot_radio_mapping(snapshot_text, interfaces)
        unique = [radio for radio, owner in mapping.items() if owner == phy_name]
        if len(unique) != 1:
            raise RuntimeError(
                f"{phy_name} 无法按硬件身份和运行接口唯一对应到无线 radio，不能安全接管无线网卡。"
            )
        radio = unique[0]
        wanted = str(expected_band or "").strip().lower()
        wanted = {"2.4g": "2g", "5g": "5g"}.get(wanted, wanted)
        actual = _parse_uci_radio_bands(snapshot_text).get(radio, "")
        if wanted and actual != wanted:
            raise RuntimeError(
                f"{phy_name} 对应 {radio}，但配置频段为 {actual or '未知'}，"
                f"与任务频段 {wanted} 不一致，已拒绝接管。"
            )
        return radio

    def resolve_phy(self, radio, snapshot_text):
        """从接管前快照反查 radio 的 phy 与原运行接口；无法唯一对应时拒绝。"""
        radio_name = str(radio or "").strip()
        iwdev = _parse_iwdev_interfaces(snapshot_text)
        interfaces = {p: tuple(name for name, _ in rows) for p, rows in iwdev.items()}
        phy = snapshot_radio_mapping(snapshot_text, interfaces).get(radio_name)
        candidates = [(phy, tuple(name for name, _ in iwdev.get(phy, ())))] if phy else []
        if len(candidates) != 1:
            raise RuntimeError("无法唯一确认无线硬件归属，恢复记录保持原样，请刷新无线状态。")
        return candidates[0]

    def takeover_phy(self, phy, radio, session_id, cancel_event=None):
        """临时关闭指定 radio 的全部无线接口，返回恢复凭据。

        参数：phy/radio 已由 resolve_radio 唯一确定；session_id 是本次任务 ID。
        返回值：RadioTakeoverRecord。副作用：执行 `wifi down <radio>`；关闭命令返回
        不代表接口瞬间消失，因此轮询约 15 秒确认该 phy 上确无残留业务接口，超时仍
        有残留会先尝试 `wifi up` 回滚再报错，不把半关状态留给用户。
        """

        before = self._phy_interfaces(phy, "read_takeover_before", cancel_event)
        result = self.command_runner.run(
            "wifi_down_radio",
            f"wifi down {quote_argument(radio)}",
            timeout=60,
            cancel_event=cancel_event,
        )
        if result.exit_code != 0 or result.cancelled or result.timed_out:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"临时关闭 {radio} 失败：{detail or result.exit_code}。"
                "未改动任何无线配置，可直接重试或更换目标。"
            )
        after = self._wait_phy_interfaces(phy, (), 15, "verify_takeover_down", cancel_event)
        if after:
            rollback_ok = False
            try:
                self.command_runner.run(
                    "wifi_up_rollback",
                    f"wifi up {quote_argument(radio)}",
                    timeout=60,
                    cancel_event=None,
                )
                restored = self._wait_phy_interfaces(
                    phy, tuple(before), 15, "verify_takeover_rollback", None
                )
                rollback_ok = set(restored) == set(before)
            except Exception:
                pass
            if rollback_ok:
                hint = "回滚后原接口已恢复，可直接重试或更换目标。"
            else:
                hint = (
                    f"回滚后原接口未完全恢复，请在路由器上手动执行 `wifi up {radio}` "
                    "并确认客户端重新上线后再试。"
                )
            raise RuntimeError(
                f"{radio} 关闭后仍有接口在线（{', '.join(after)}），已尝试恢复原状。"
                f"{hint}"
            )
        return RadioTakeoverRecord(str(session_id), radio, phy, tuple(before))

    def restore(self, record, cancel_event=None):
        """重新启用接管过的 radio 并校验业务接口回来。

        参数：record 是 takeover_phy 返回的凭据。副作用：先按原路径恢复（配置
        事务或 `wifi up <radio>`）；无线接口重建需要数秒，轮询约 30 秒核对之前
        记下的接口名是否重现。部分设备上 reload 只改配置不重建接口（hostapd
        收到新配置却不 Add interface），此时完整 down/up 一轮走另一条 netifd
        路径再核一次；仍缺才抛出带恢复指引的异常，调用方必须把原文展示给用户，
        不得吞掉。
        """

        if getattr(record, "config_token", ""):
            self._config_command("takeover_restore", record, cancel_event)
            missing, recycled = self._verify_restored_interfaces(record, cancel_event)
            if not missing:
                if self.pending_record == record:
                    self.pending_record = None
                return True
            raise RuntimeError(
                "信号启用配置已恢复，但运行接口未完全回来"
                + ("；期间又试了一次关闭再启用仍没回来" if recycled else "")
                + "；恢复记录已保留，请到信号管理页点“重新启用”，"
                  "仍不行请重启路由器后再刷新核对。")
        result = self.command_runner.run(
            "wifi_up_radio",
            f"wifi up {quote_argument(record.radio)}",
            timeout=60,
            cancel_event=cancel_event,
        )
        if result.exit_code != 0 or result.cancelled or result.timed_out:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"恢复 {record.radio} 失败：{detail or result.exit_code}。"
                f"请保持设备在线并在路由器上手动执行 `wifi up {record.radio}`，"
                "恢复后检查无线客户端是否重新上线。"
            )
        missing, recycled = self._verify_restored_interfaces(record, cancel_event)
        if not missing:
            return True
        raise RuntimeError(
            f"{record.radio} 已重新启用，但以下接口未回来：{', '.join(missing)}"
            + ("；期间又试了一次关闭再启用仍没回来" if recycled else "")
            + "；请到信号管理页点“重新启用”，仍不行请重启路由器；"
              "确认客户端重新上线后再继续抓包。"
        )

    def _verify_restored_interfaces(self, record, cancel_event):
        """核对业务接口回来；缺席则完整 down/up 一轮再核一次。

        返回 (缺失接口名列表, 是否走过 down/up)。down/up 命令本身失败不抛，
        只影响返回标记，最终缺失结论不变；取消直接抛异常。
        """

        current = self._wait_phy_interfaces(
            record.phy, record.interfaces, 30, "verify_takeover_restored", cancel_event
        )
        missing = [name for name in record.interfaces if name not in current]
        if not missing:
            return [], False
        recycled = self._recycle_radio(record, cancel_event)
        current = self._wait_phy_interfaces(
            record.phy, record.interfaces, 30, "verify_takeover_recycled", cancel_event
        )
        return [name for name in record.interfaces if name not in current], recycled

    def _recycle_radio(self, record, cancel_event):
        """完整关闭再启用 radio，走与 reload 不同的 netifd 路径；返回是否都成功。"""

        ok = True
        for action in ("down", "up"):
            try:
                result = self.command_runner.run(
                    f"wifi_{action}_retry",
                    f"wifi {action} {quote_argument(record.radio)}",
                    timeout=60,
                    cancel_event=cancel_event,
                )
            except Exception:
                return False
            if result.cancelled:
                raise RuntimeError("无线接管等待已取消。")
            ok = ok and not result.timed_out and result.exit_code == 0
        return ok

    def audit_restore(self, record, cancel_event=None):
        """恢复完成后只读自检：把该 radio 的 UCI 状态和当前运行接口对账。

        只抓取 `uci show wireless`、`iw dev`、`wifi status` 三份只读输出做交叉核对，
        不执行任何 up/down/reload，不改任何状态。核对两类差异：
        1. 该 radio 下 `disabled='1'` 的段，其接口仍出现在运行接口（本应禁用的信号
           被拉起，即用户报告的“误启用”）；
        2. 该 radio 下 UCI 已启用、但接管前不在 record.interfaces（接管前没在运行）
           的段，其接口现在出现在运行接口（配置启用但之前没跑的信号被打开）。
        返回中文说明，无差异返回空字符串。自检失败（只读命令异常等）不抛异常，不
        掩盖恢复结果。
        """

        unknown = "无线恢复后的硬件归属或接口状态未能核实，请刷新无线状态后检查。"
        try:
            text = self.snapshot_wireless(cancel_event)
        except Exception:
            return unknown
        radio_sections = {
            name: attrs
            for name, attrs in _parse_uci_wireless_sections(text).items()
            if attrs["device"] == record.radio
        }
        if not radio_sections:
            return ""
        status_ifnames = _parse_status_ifnames(text)
        phy_interfaces = _parse_iwdev_interfaces(text)
        interfaces = {p: tuple(name for name, _ in rows) for p, rows in phy_interfaces.items()}
        mapped = snapshot_radio_mapping(text, interfaces).get(record.radio)
        if not mapped or (record.phy and mapped != record.phy):
            return unknown
        phys = [mapped]
        run_names = set()
        run_macs = set()
        for phy in phys:
            for name, mac in phy_interfaces.get(phy, ()):
                run_names.add(name)
                if mac:
                    run_macs.add(mac)
        before = set(record.interfaces or ())
        notes = []
        for name, attrs in sorted(radio_sections.items()):
            if attrs["disabled"]:
                iface = status_ifnames.get(name, "")
                alive = bool(iface and iface in run_names) or bool(
                    attrs["macaddr"] and attrs["macaddr"] in run_macs
                )
                if alive:
                    notes.append(
                        f"{name}({attrs['mode']})配置为禁用但其接口"
                        f"{iface or '未知名'}仍在运行"
                    )
            else:
                iface = status_ifnames.get(name, "")
                if (
                    iface
                    and iface in run_names
                    and iface not in before
                    and not iface.startswith("wbmon")
                ):
                    notes.append(
                        f"{name}({attrs['mode']})配置启用且接管前未在运行，"
                        f"本次恢复后被拉起为接口 {iface}"
                    )
        if not notes:
            return ""
        return "无线接管恢复后只读自检发现： " + "；".join(notes)

    def _wait_phy_interfaces(self, phy, want, timeout_seconds, command_name, cancel_event):
        """轮询 `iw dev` 直到 phy 接口集合符合预期或超时。

        参数：want 是期望的接口名元组（关闭时传空元组，恢复时传接管前记录）；
        timeout_seconds 是总等待秒数。返回值：最后一轮读到的接口元组。关闭验证期望
        为空，dump 多次确认缺失也视为已清空；恢复等待时 phy 短暂缺席只视为“还没
        回来”（恢复命令刚下发、接口重建中，`iw dev` 本来就不列出零接口 phy），
        按空集合继续等到超时，只有超时仍缺席才由调用方按“未完全回来”报错。
        取消与读取失败（非缺席）仍直接抛出，不返回半截结论。
        """

        deadline = time.monotonic() + max(1, int(timeout_seconds))
        last = ()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("无线接管等待已取消。")
            try:
                last = self._phy_interfaces(
                    phy, command_name, cancel_event, missing_as_empty=not want
                )
            except RuntimeError as exc:
                if f"在路由器上找不到 {phy}" not in str(exc):
                    raise
                # 到此的只有恢复/回滚等待分支（关闭等待缺席已视为清空不抛）：
                # phy 缺席只是接口还没重建好，按空集合继续等，超时由调用方定论。
                last = ()
            # 按集合比较：接口出现顺序不影响结论，避免顺序抖动导致等满超时。
            if set(last) == set(want):
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(1)

    def _phy_interfaces(self, phy, command_name, cancel_event, missing_as_empty=False):
        """读取指定 phy 上当前的接口名元组。

        参数 missing_as_empty 为真且完整读取多次确认缺失时，返回空元组（关闭后
        验证期望为空，phy 从 dump 消失同样视为已清空）；否则缺失抛异常并附输出
        摘要。读取自带 sysfs 完整性校验，缺 phy 会自动重读。
        """

        phy_interfaces, _channels, diag = read_iwdev_complete(
            self.command_runner, command_name, cancel_event
        )
        if phy in phy_interfaces:
            return tuple(phy_interfaces[phy])
        if missing_as_empty:
            return ()
        raise RuntimeError(
            f"在路由器上找不到 {phy}，不能安全接管无线网卡。"
            f"本次 `iw dev` 输出摘要：{diag['excerpt'] or '<空>'}。{diag['note']}"
        )


def _parse_uci_wireless_sections(text):
    """解析快照中的无线 UCI 段，返回 {段名: 属性字典}。

    属性含 device/mode/disabled/macaddr（小写）。只识别 `wifi-iface` 段；`wifi-device`
    段与 globals 不参与。用于恢复后只读自检的配置基准。
    """

    sections = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("wireless.") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        rest = key[len("wireless."):]
        if "." not in rest:
            if value.strip() == "wifi-iface":
                sections.setdefault(
                    rest,
                    {"device": "", "mode": "", "disabled": False, "macaddr": ""},
                )
            continue
        section, prop = rest.split(".", 1)
        if section not in sections:
            continue
        val = value.strip().strip("'\"").strip()
        if prop == "device":
            sections[section]["device"] = val
        elif prop == "mode":
            sections[section]["mode"] = val
        elif prop == "disabled":
            sections[section]["disabled"] = val not in ("", "0", "false")
        elif prop == "macaddr":
            sections[section]["macaddr"] = val.lower()
    return sections


def _parse_iwdev_interfaces(text):
    """解析快照中的 `iw dev` 段，返回 {phy: [(接口名, MAC小写)]}。"""

    iwdev = text.split("__WINBOX_SNAP_IWDEV__")[-1].split(
        "__WINBOX_SNAP_WIFISTATUS__"
    )[0]
    result = {}
    phy = None
    for raw in iwdev.splitlines():
        line = raw.strip()
        lower = line.lower()
        if line.startswith("phy#"):
            phy = "phy" + line[len("phy#"):].strip()
            result.setdefault(phy, [])
            continue
        if phy is None:
            continue
        if lower.startswith("interface "):
            name = line.split(None, 1)[1].strip()
            result.setdefault(phy, []).append((name, ""))
        elif lower.startswith("addr "):
            mac = line.split(None, 1)[1].strip().lower()
            if result.setdefault(phy, []):
                prev_name, _prev_mac = result[phy][-1]
                result[phy][-1] = (prev_name, mac)
    return result


def _parse_status_ifnames(text):
    """解析快照中的 `wifi status` JSON 段，返回 {section: ifname}。"""

    payload = text.split("__WINBOX_SNAP_WIFISTATUS__")[-1]
    mapping = {}
    try:
        data = json.loads(payload)
    except (ValueError, AttributeError):
        return mapping
    radios = data.values() if isinstance(data, dict) else ()
    for radio in radios:
        if not isinstance(radio, dict):
            continue
        for iface in radio.get("interfaces") or ():
            if not isinstance(iface, dict):
                continue
            section = iface.get("section")
            ifname = iface.get("ifname")
            if section and ifname:
                mapping[section] = ifname
    return mapping


def _parse_status_radio_ifnames(text):
    """解析 `wifi status`，返回 {radio: {ifname}}；无完整归属时返回空集合。"""
    return status_radio_ifnames(text)


def _parse_uci_radio_bands(text):
    """解析 wifi-device 的 band/hwmode，返回 {radio: 2g/5g/6g/空}。"""
    return uci_radio_bands((text or "").split("__WINBOX_SNAP_IWDEV__", 1)[0])
