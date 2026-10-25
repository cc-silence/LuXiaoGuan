"""OpenWrt monitor 接口事务边界。"""

from dataclasses import dataclass, replace
from uuid import uuid4
from .monitor_ownership import preflight_command, create_command, owner_guard, remove_command

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.command_runner import require_command_completion
from modules.capture_services.openwrt.radio_policy import require_idle_phy
from modules.capture_services.openwrt.device_probe import (
    monitor_status_text,
    probe_monitor_capability,
    read_monitor_status,
)


@dataclass(frozen=True)
class RadioSessionRecord:
    """只记录本会话创建或修改的无线对象，用于精确恢复。"""

    session_id: str
    radio: str
    interface: str
    uci_section: str
    original_state_path: str = ""
    idle_radio_only: bool = False
    tool_managed: bool = False
    ownership_token: str = ""
    creation_pending: bool = False


class OpenWrtRadioSession:
    """负责创建和恢复本任务 monitor，不删除默认 AP 或其他 monitor。"""

    def __init__(self, command_runner):
        self.command_runner = command_runner
        self.record = None

    @property
    def active(self):
        """返回是否已记录本次 monitor 会话。"""

        return self.record is not None

    def clear_record(self):
        """确认远端恢复完成后清除内存记录，不执行命令。"""

        self.record = None

    def create(self, session_id, phy_name, band, cancel_event=None, idle_radio_only=False, *, tool_managed=False):
        """创建本任务专属 monitor 接口。

        参数：phy_name 是探测到的 phy；band 用于记录。返回 RadioSessionRecord。
        idle_radio_only 为真时创建前核实整块 phy 空闲，并把策略登记到恢复记录。
        副作用：使用 `iw` 添加临时接口并启用；不修改 UCI，不执行 wifi reload，不删
        除现有 AP/STA 或其他 monitor。本方法故意不执行 `iw set channel` 预切信道：
        部分驱动（含 mt76）会拒绝该操作（Resource busy，连同信道也拒绝），而
        airodump-ng（--channel）与 hcxdumptool 会自行调谐；调谐结果由各任务自行
        校验，失败时给出结构化错误。
        """

        if self.record is not None:
            raise RuntimeError("当前已有 OpenWrt monitor 会话。")
        safe_session = "".join(char for char in str(session_id) if char.isalnum())[:8]
        if not safe_session:
            raise ValueError("抓包会话 ID 不合法。")
        phy = str(phy_name or "").strip()
        if not phy.startswith("phy") or not phy[3:].isdigit():
            raise ValueError(f"无线 phy 名称不合法：{phy or '<空>'}")
        supported, cap_note = probe_monitor_capability(
            self.command_runner, phy, cancel_event
        )
        if supported is not True:
            raise RuntimeError(
                f"{phy} 不能用于抓包：{cap_note}"
                "请更换抓包频段，或先确认该频段 radio 的驱动能力。"
            )
        interface = f"wbmon{safe_session}"[:15]
        if idle_radio_only or tool_managed:
            require_idle_phy(self.command_runner, phy, cancel_event)
        interface_type = "managed" if tool_managed else "monitor"
        check = self.command_runner.run("check_monitor_name", preflight_command(interface),
                                        timeout=8, cancel_event=cancel_event)
        require_command_completion(check)
        if check.exit_code != 0 or check.stdout.strip() != "absent":
            raise RuntimeError("临时接口名称已占用或无法核实，未创建或删除接口。")
        self.record = RadioSessionRecord(str(session_id), phy, interface, "",
            idle_radio_only=idle_radio_only, tool_managed=tool_managed,
            ownership_token="routermanager:"+uuid4().hex, creation_pending=True)
        # Publish recovery ownership before any remote mutation. Unknown results keep it.
        add_result = self.command_runner.run("create_monitor", create_command(self.record, interface_type),
                                             timeout=15, cancel_event=cancel_event)
        require_command_completion(add_result)
        if add_result.exit_code != 0:
            if add_result.exit_code in (72,74):
                self.record = None  # Confirmed refusal before creating our interface.
            raise RuntimeError("创建临时接口或写入归属标记失败；结果未确认时已保留恢复记录。")
        self.record = replace(self.record, creation_pending=False)
        try:
            link_result = self.command_runner.run(
                "activate_monitor",
                owner_guard(self.record) + f"ip link set dev {quote_argument(interface)} up",
                timeout=10,
                cancel_event=cancel_event,
            )
            require_command_completion(link_result)
            if link_result.exit_code != 0:
                raise RuntimeError(
                    "启用 monitor 接口失败："
                    + ((link_result.stderr or link_result.stdout).strip() or str(link_result.exit_code))
                )
            # 建后验类型：命令都成功也可能是驱动建出了非 monitor 接口（如类型回退），
            # 此时不进入抓包，直接回滚并给出确切原因。
            if tool_managed:
                check = self.command_runner.run("verify_tool_managed_interface",
                    f"iw dev {quote_argument(interface)} info", timeout=10, cancel_event=cancel_event)
                require_command_completion(check)
                status = "ready" if check.exit_code == 0 and "type managed" in check.stdout else "invalid"
            else:
                status = read_monitor_status(self.command_runner, interface, cancel_event)
            if status != "ready":
                raise RuntimeError(
                    "新建 monitor 接口状态异常"
                    f"（{monitor_status_text(status)}），已回滚，请检查无线驱动。"
                )
            return self.record
        except Exception as primary_error:
            try:
                self.restore(cancel_event=None)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"{primary_error}；回滚临时无线接口又失败：{cleanup_error}。"
                    "已保留恢复记录，请保持设备在线后重试清理。"
                ) from primary_error
            raise

    def restore(self, cancel_event=None):
        """只删除本会话创建的 monitor 接口；确认删除后才清除恢复记录。

        删除失败且接口仍存在时保留 record，调用方可以重试或写入恢复日志。接口已经由
        其他清理路径删除时视为成功，不会误操作任何其他接口。
        """

        record = self.record
        if record is None:
            return True
        result = self.command_runner.run(
            "remove_monitor",
            remove_command(record) if record.ownership_token else f"iw dev {quote_argument(record.interface)} del",
            timeout=10,
            cancel_event=cancel_event,
        )
        require_command_completion(result)
        if result.exit_code != 0:
            if record.ownership_token:
                raise RuntimeError("临时接口的归属或创建结果未确认，已保留恢复记录；未删除其他接口。")
            # 接口已不存在也视为恢复完成。
            check = self.command_runner.run(
                "check_monitor_removed",
                f"ip link show dev {quote_argument(record.interface)} >/dev/null 2>&1",
                timeout=5,
                cancel_event=cancel_event,
            )
            require_command_completion(check)
            if check.exit_code != 1:
                raise RuntimeError(
                    "删除 monitor 接口失败："
                    + ((result.stderr or result.stdout).strip() or str(result.exit_code))
                )
        self.record = None
        return True

    def heal_link(self, cancel_event=None):
        """本会话 monitor 被关闭时重新启用，返回 (是否修好, 说明)。

        只处理“存在但被关闭”的情况：running 工具绑定的旧句柄不受影响，修好后可
        继续抓。其他状态（丢失、类型不对、读不出）一律不动并如实返回，由调用方
        决定是重试还是报错。只碰本次登记的接口。
        """

        record = self.record
        if record is not None and getattr(record, "tool_managed", False):
            return False, "\u5de5\u5177\u7ba1\u7406\u7684\u63a5\u53e3\u4e0d\u81ea\u52a8\u91cd\u5efa\uff0c\u8bf7\u91cd\u65b0\u542f\u52a8\u4efb\u52a1\u3002"
        if record is None:
            return False, "本次没有 monitor 会话，无可修复对象。"
        status = read_monitor_status(
            self.command_runner, record.interface, cancel_event
        )
        if status == "ready":
            return False, "monitor 接口正常，无需修复。"
        if status != "down":
            return False, f"monitor 接口{monitor_status_text(status)}，链路修复处理不了。"
        if record.idle_radio_only:
            require_idle_phy(self.command_runner, record.radio, cancel_event, record.interface)
        up_result = self.command_runner.run(
            "heal_monitor_link",
            (owner_guard(record) if record.ownership_token else "") + f"ip link set dev {quote_argument(record.interface)} up",
            timeout=10,
            cancel_event=cancel_event,
        )
        require_command_completion(up_result)
        if up_result.exit_code != 0:
            detail = (up_result.stderr or up_result.stdout).strip()
            return False, f"重新启用 monitor 接口失败：{detail or up_result.exit_code}"
        if (
            read_monitor_status(self.command_runner, record.interface, cancel_event)
            != "ready"
        ):
            return False, "monitor 接口已启用但类型仍异常，请检查无线驱动。"
        return True, "monitor 接口曾被关闭，已重新启用并继续抓包。"

    def recover(self, cancel_event=None):
        """丢失的本会话 monitor 重建一次，返回 (是否重建, 说明)。

        仅用于抓包工具启动前的窗口（此时尚无采集文件，重建无副作用）：接口丢失时
        按原名重建并启用、验类型；被关闭时走链路修复。单次尝试，不循环，避免掩盖
        真实故障。只碰本次登记的接口。
        """

        record = self.record
        if record is not None and getattr(record, "tool_managed", False):
            return False, "\u5de5\u5177\u7ba1\u7406\u7684\u63a5\u53e3\u4e0d\u81ea\u52a8\u91cd\u5efa\uff0c\u8bf7\u91cd\u65b0\u542f\u52a8\u4efb\u52a1\u3002"
        if record is None:
            return False, "本次没有 monitor 会话，无可恢复对象。"
        status = read_monitor_status(
            self.command_runner, record.interface, cancel_event
        )
        if status == "ready":
            return False, "monitor 接口正常，无需恢复。"
        if status == "down":
            return self.heal_link(cancel_event)
        if status != "missing":
            return False, f"monitor 接口{monitor_status_text(status)}，无法自动重建。"
        if record.idle_radio_only:
            require_idle_phy(self.command_runner, record.radio, cancel_event, record.interface)
        if record.ownership_token:
            self.record = replace(record, creation_pending=True)
        add_result = self.command_runner.run(
            "recover_monitor",
            create_command(record, "monitor") if record.ownership_token else
                f"iw phy {quote_argument(record.radio)} interface add {quote_argument(record.interface)} type monitor",
            timeout=15, cancel_event=cancel_event)
        require_command_completion(add_result)
        if add_result.exit_code != 0:
            return False, "重建临时接口失败，恢复记录已保留。"
        if record.ownership_token:
            self.record = replace(record, creation_pending=False)
        up_result = self.command_runner.run(
            "recover_monitor_up",
            (owner_guard(record) if record.ownership_token else "") + f"ip link set dev {quote_argument(record.interface)} up",
            timeout=10,
            cancel_event=cancel_event,
        )
        require_command_completion(up_result)
        if up_result.exit_code != 0:
            detail = (up_result.stderr or up_result.stdout).strip()
            return False, f"重建后启用 monitor 接口失败：{detail or up_result.exit_code}"
        if (
            read_monitor_status(self.command_runner, record.interface, cancel_event)
            != "ready"
        ):
            return False, "monitor 接口已重建但类型仍异常，请检查无线驱动。"
        return True, "monitor 接口在启动前丢失，已重建并继续任务。"
