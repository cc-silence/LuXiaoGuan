"""hcxdumptool 的只读能力检测和有上游证据的参数策略。"""

import re
from dataclasses import dataclass

from modules.capture_services.openwrt.command_builder import quote_argument


SUPPORTED_HCX_VERSIONS = frozenset({"6.2.4", "6.3.4", "6.3.5"})


SUPPORT_NOTE = (
    "支持 6.2.4、6.3.4、6.3.5 全局或目标被动采集；"
    "6.3.4 支持手动过滤中勾选目标的主动 PMKID 请求，不发送断连或全局信标。"
    "各版均必须使用接收白名单，不按相似版本猜测能力。"
    "请求不保证目标返回 PMKID，驱动注入能力仍需真机验证。"
)


class HcxCompatibilityError(RuntimeError):
    """工具 API 或所选组合无法满足任务承诺，禁止启动或尝试其他参数。"""


def validate_pmkid_mode(mode, active_beacon):
    """共用静态模式检查，不读取设备或修改设置。"""
    if active_beacon and mode != "filter":
        raise HcxCompatibilityError(
            "主动请求 PMKID 只对勾选目标生效，本次尚未开始采集。"
            "请选择“手动过滤”并勾选已授权目标，或关闭主动请求使用被动监听。"
        )


@dataclass(frozen=True)
class HcxCapabilities:
    """单次只读检测所得能力；仅沿当前任务调用栈传递，不跨设备缓存。"""

    version: str
    options: frozenset

    def capture_options(self, band, active_beacon=False, filtered=False, bpf_path=None):
        """返回已核实参数；需要 BPF 的组合没有文件路径时禁止构造命令。"""

        if self.version == "6.0.1":
            raise HcxCompatibilityError(
                "hcxdumptool 6.0.1 缺少当前流程必需的接收过滤能力，未启动 PMKID 采集。"
                "请使用常规握手抓包。")
        if self.version not in SUPPORTED_HCX_VERSIONS:
            raise HcxCompatibilityError(
                f"hcxdumptool {self.version or '版本未知'} 未经 API 核对，已拒绝启动。"
                + SUPPORT_NOTE + "请核对官方包的版本与帮助输出，不要反复试参数。"
            )
        validate_pmkid_mode("filter" if filtered else "global", active_beacon)
        if active_beacon and self.version != "6.3.4":
            raise HcxCompatibilityError(
                f"当前路由器工具版本（hcxdumptool {self.version}）尚未适配目标 PMKID 主动请求，本次尚未开始采集。"
                "请到“抓包设置 → PMKID”关闭“主动请求 PMKID”，再点击开始。"
            )
        if band not in {"2.4G", "5G"}:
            raise HcxCompatibilityError("PMKID 频段无效，请重新选择 2.4G 或 5G。")
        # 依据官方标签 hcxdumptool.c 的 usage/getopt 和发送路径，不按版本区间推断。
        required = {"-i", "-o", "-s", "--silent", "--enable_status"}
        arguments = ("-s", "2" if band == "2.4G" else "3", "--silent", "--enable_status=15")
        output = "-o"
        if self.version in {"6.3.4", "6.3.5"}:
            channels = range(1, 14) if band == "2.4G" else (
                36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116,
                120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165,
            )
            suffix = "a" if band == "2.4G" else "b"
            output = "-w"
            arguments = ("-c", ",".join(f"{c}{suffix}" for c in channels),
                         "--attemptapmax=0", "--attemptclientmax=0", "--disable_beacon")
            required = {"-i", "-w", "-c", "--attemptapmax", "--attemptclientmax", "--disable_beacon"}
            if active_beacon:
                # Three AP request cycles; no client interaction, broadcast probing or disconnect.
                arguments = ("-c", arguments[1], "--attemptapmax=3", "--attemptclientmax=0",
                             "--disable_deauthentication", "--disable_proberequest",
                             "--disable_reassociation", "--disable_beacon")
                required |= {"--disable_deauthentication", "--disable_proberequest", "--disable_reassociation"}

        if not bpf_path:
            raise HcxCompatibilityError("此组合必须使用已生成的接收 BPF；禁止无过滤启动。")
        option = "--bpfc" if self.version == "6.2.4" else "--bpf"
        required.add(option)
        arguments += (f"{option}={bpf_path}",)
        missing = required - self.options
        if missing:
            raise HcxCompatibilityError(
                f"hcxdumptool {self.version} 帮助输出缺少所需能力：{', '.join(sorted(missing))}。"
                "已拒绝启动；请核对官方包是否有裁剪或不同 API，不会尝试其他参数。"
            )
        return (output, *arguments)


def read_hcx_capabilities(command_runner, cancel_event=None, *, executable="hcxdumptool"):
    """后台执行一次帮助查询，严格处理取消/失败；不初始化无线接口、不缓存结果。"""

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("hcxdumptool 能力检测已取消。")
    result = command_runner.run(
        "hcxdumptool_capabilities", ("hcxdumptool -h" if executable == "hcxdumptool" else quote_argument(executable)+" -h"), timeout=10, cancel_event=cancel_event,
    )
    if result.cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise RuntimeError("hcxdumptool 能力检测已取消。")
    if result.timed_out:
        raise RuntimeError("hcxdumptool 能力检测超时，已停止；请检查连接和设备负载。")
    if result.exit_code != 0:
        raise RuntimeError(
            f"hcxdumptool 帮助查询失败（退出码 {result.exit_code}）；"
            "请检查路由器上的官方包是否可执行及其运行库，未启动无线任务。"
        )
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    versions = set(re.findall(r"(?m)^hcxdumptool[ \t]+(\d+\.\d+\.\d+)[ \t]+\(C\)", text))
    if len(versions) != 1:
        raise HcxCompatibilityError(
            "hcxdumptool 版本未知或身份输出冲突，已拒绝启动；请核对官方包的帮助输出。"
        )
    options = frozenset(re.findall(r"(?m)^[ \t]*(--[a-z_]+|-[A-Za-z])(?=[ \t=<:]|$)", text))
    return HcxCapabilities(versions.pop(), options)


def hcx_capture_command(interface, output, options, executable="hcxdumptool"):
    """自检和正式采集共享构造器，仅输出位置不同；动态路径按 POSIX 引用。"""

    return " ".join(quote_argument(value) for value in (
        executable, "-i", interface, options[0], str(output), *options[1:],
    ))
