"""PC 端智能日志分析与抓包成果提取引擎。

负责解析从 /root/cap/ 拉回本地的会话日志（script_*.log）、工具明细日志（run_*.log）以及 .pcapng 成果：
1. 健康度研判：解析退出码（130 中断、131 内存守卫、132 空间守卫、0 正常等）；
2. 驱动与硬件诊断：识别 EINVAL 驱动错误、信道不支持、内核 BPF 规则状态等；
3. 成果统计：提取捕获的 AP（SSID、BSSID、信道）、PMKID 摘要、握手包 (EAPOL) 数量，汇总生成结构化报告。
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import List, Dict, Optional


@dataclass
class CapturedTargetSummary:
    """提取出的单个 AP 抓包摘要。"""

    bssid: str
    ssid: str = ""
    channel: str = ""
    has_pmkid: bool = False
    has_eapol: bool = False
    details: str = ""


@dataclass
class AutoCaptureDiagnosticReport:
    """自动抓包与离线拉取诊断完整报告。"""

    exit_code: Optional[int] = None
    exit_reason: str = "未知退出状态"
    health_level: str = "normal"  # normal | warning | danger | info
    health_title: str = "正常"
    firmware_info: str = ""
    hcx_version: str = ""
    dialect_mode: str = ""
    bpf_status: str = "未启用或无记录"
    hardware_notes: List[str] = field(default_factory=list)
    pmkid_count: int = 0
    eapol_count: int = 0
    targets: List[CapturedTargetSummary] = field(default_factory=list)
    captured_files: List[Dict[str, str]] = field(default_factory=list)
    raw_summary: str = ""


class AutoCaptureLogAnalyzer:
    """日志分析引擎，支持单个日志文本分析与离线目录批量分析。"""

    @classmethod
    def analyze_directory(cls, directory_path: str) -> AutoCaptureDiagnosticReport:
        """分析指定本地目录下的全部日志与 pcapng 文件。"""
        dir_p = Path(directory_path)
        if not dir_p.is_dir():
            return AutoCaptureDiagnosticReport(
                exit_reason="目录不存在",
                health_level="danger",
                health_title="目录错误",
            )

        # 收集 pcapng 成果
        captured_files = []
        for pcap_file in sorted(dir_p.glob("*.pcapng")):
            try:
                sz = pcap_file.stat().st_size
                sz_str = f"{sz / 1024:.1f} KB" if sz < 1024 * 1024 else f"{sz / (1024 * 1024):.2f} MB"
                captured_files.append({"name": pcap_file.name, "path": str(pcap_file), "size": sz_str, "bytes": sz})
            except Exception:
                pass

        # 找到最新的会话日志 script_*.log
        script_logs = sorted(dir_p.glob("script_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        session_text = ""
        if script_logs:
            try:
                session_text = script_logs[0].read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        # 收集所有 run_*.log 文本
        run_texts = []
        for rlog in sorted(dir_p.glob("run_*.log")):
            try:
                run_texts.append(rlog.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

        merged_text = session_text + "\n" + "\n".join(run_texts)
        report = cls.analyze_log_text(merged_text)
        report.captured_files = captured_files
        return report

    @classmethod
    def analyze_log_text(cls, log_text: str) -> AutoCaptureDiagnosticReport:
        """从合并或单份日志文本解析健康度、驱动信息与成果。"""
        report = AutoCaptureDiagnosticReport()
        if not log_text.strip():
            report.exit_reason = "暂无日志数据"
            report.health_title = "暂无日志"
            return report

        # 1. 固件与工具信息
        fw_match = re.search(r"固件:\s*([^\r\n]+)", log_text)
        if fw_match:
            report.firmware_info = fw_match.group(1).replace("DISTRIB_DESCRIPTION=", "").strip("'\" ")

        hcx_match = re.search(r"hcxdumptool:\s*([^\r\n]+)", log_text)
        if hcx_match:
            report.hcx_version = hcx_match.group(1).strip()

        dialect_match = re.search(r"hcxdumptool 方言:\s*([a-zA-Z0-9]+)", log_text)
        if dialect_match:
            report.dialect_mode = dialect_match.group(1).strip()

        # 2. BPF 状态
        if "init_bpf_filter" in log_text or "--bpf" in log_text or "--bpfc" in log_text:
            report.bpf_status = "内核白名单过滤已成功挂载 (仅放行信标与握手帧，零大流量磨损)"
        elif "降参" in log_text and "无 BPF" in log_text:
            report.bpf_status = "降参无 BPF 模式"

        # 3. 退出码与健康度判断
        code_match = re.search(r"退出码:\s*(\d+)", log_text)
        reason_match = re.search(r"退出原因:\s*([^,\r\n]+)", log_text)

        if code_match:
            report.exit_code = int(code_match.group(1))

        if reason_match:
            report.exit_reason = reason_match.group(1).strip()

        if report.exit_code == 132:
            report.health_level = "warning"
            report.health_title = "触发空间守卫 (SpaceGuard)"
            report.hardware_notes.append("路由器 Flash 擦除块剩余空间达到安全底线，脚本自动提前优雅退出并落盘，已保护 Flash 未被写满。")
        elif report.exit_code == 131:
            report.health_level = "warning"
            report.health_title = "触发内存守卫 (MemGuard)"
            report.hardware_notes.append("路由器可用内存低于阈值，脚本自动提前优雅退出并落盘，已防止系统发生 OOM 崩溃。")
        elif report.exit_code == 130:
            report.health_level = "normal"
            report.health_title = "已优雅停机 (拔线断电/人工中断)"
            if not report.exit_reason or report.exit_reason == "未知退出状态":
                report.exit_reason = "收到中断信号，正常收尾并同步落盘"
        elif report.exit_code == 0:
            report.health_level = "normal"
            report.health_title = "正常运行/正常完成"
            if not report.exit_reason or report.exit_reason == "未知退出状态":
                report.exit_reason = "任务正常完成"
        elif report.exit_code is not None and report.exit_code != 0:
            report.health_level = "danger"
            report.health_title = f"异常退出 (代码 {report.exit_code})"
        else:
            report.health_level = "info"
            report.health_title = "日志未记录明确退出码 (可能为断电拔线)"

        # 4. 驱动与硬件诊断
        if "EINVAL" in log_text:
            report.hardware_notes.append("检测到驱动返回 EINVAL (无效参数)，通常因为向单频网卡下发了跨频段信道，脚本已自动阶梯适配。")
        if "Device or resource busy" in log_text:
            report.hardware_notes.append("检测到网卡设备忙，可能存在未清理的旧监听口或正在运行的无线进程。")
        if "failed to set channel" in log_text.lower():
            report.hardware_notes.append("部分信道设置失败，可能该射频物理限制或 DFS 雷达避让。")

        # 5. 抓包成果提取 (PMKID 与 EAPOL 握手包)
        # 匹配格式示例：
        #   [CH 36b] AP: TP-LINK_5G_68B2 (aa:bb:cc:dd:ee:ff)
        #   [*] PMKID captured for AP: aa:bb:cc:dd:ee:ff
        #   [*] EAPOL M1+M2 captured for AP: aa:bb:cc:dd:ee:ff
        #   INTERCEPTED: PMKID / EAPOL
        pmkid_found = set()
        eapol_found = set()
        targets_map: Dict[str, CapturedTargetSummary] = {}

        # 扫描 AP 发现记录
        ap_patterns = [
            r"\[CH\s*([0-9a-zA-Z]+)\]\s+AP:\s*([^\s(]+)?\s*\(?([0-9a-fA-F:]{17})\)?",
            r"AP:\s*([0-9a-fA-F:]{17})\s*\(([^)]+)\)",
            r"AP:\s*([^\s]+)\s+([0-9a-fA-F:]{17})",
        ]
        for line in log_text.splitlines():
            for pat in ap_patterns:
                m = re.search(pat, line)
                if m:
                    g = m.groups()
                    if len(g) == 3:
                        ch, ssid, bssid = g[0], g[1] or "", g[2].upper()
                    elif len(g) == 2:
                        # 区分哪个是 bssid
                        if ":" in g[0]:
                            bssid, ssid, ch = g[0].upper(), g[1], ""
                        else:
                            ssid, bssid, ch = g[0], g[1].upper(), ""
                    else:
                        continue
                    if bssid not in targets_map:
                        targets_map[bssid] = CapturedTargetSummary(bssid=bssid, ssid=ssid, channel=ch)
                    elif not targets_map[bssid].ssid and ssid:
                        targets_map[bssid].ssid = ssid

            # 扫描 PMKID
            if "PMKID" in line:
                bssid_m = re.search(r"([0-9a-fA-F:]{17})", line)
                if bssid_m:
                    b_id = bssid_m.group(1).upper()
                    pmkid_found.add(b_id)
                    if b_id in targets_map:
                        targets_map[b_id].has_pmkid = True
                    else:
                        targets_map[b_id] = CapturedTargetSummary(bssid=b_id, has_pmkid=True)
                else:
                    pmkid_found.add(f"unknown_{len(pmkid_found)}")

            # 扫描 EAPOL 握手包
            if "EAPOL" in line or "handshake" in line.lower():
                bssid_m = re.search(r"([0-9a-fA-F:]{17})", line)
                if bssid_m:
                    b_id = bssid_m.group(1).upper()
                    eapol_found.add(b_id)
                    if b_id in targets_map:
                        targets_map[b_id].has_eapol = True
                    else:
                        targets_map[b_id] = CapturedTargetSummary(bssid=b_id, has_eapol=True)
                else:
                    eapol_found.add(f"unknown_{len(eapol_found)}")

        report.pmkid_count = len(pmkid_found)
        report.eapol_count = len(eapol_found)
        report.targets = list(targets_map.values())

        # 汇总文本
        summary_lines = [
            f"健康度: {report.health_title} ({report.exit_reason})",
            f"固件: {report.firmware_info or '未知'}",
            f"hcxdumptool: {report.hcx_version or '未知'} (方言: {report.dialect_mode or 'auto'})",
            f"BPF 过滤: {report.bpf_status}",
            f"捕获 PMKID: {report.pmkid_count} 个 | 捕获握手包 (EAPOL): {report.eapol_count} 个",
            f"识别 AP 热点: {len(report.targets)} 个",
        ]
        if report.hardware_notes:
            summary_lines.append("硬件与驱动说明:")
            for note in report.hardware_notes:
                summary_lines.append(f"  - {note}")
        report.raw_summary = "\n".join(summary_lines)
        return report
