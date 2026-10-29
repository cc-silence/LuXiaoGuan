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


def format_mac(raw: str) -> str:
    """归一化 MAC 地址为标准大写冒号格式 (如 AA:BB:CC:DD:EE:FF)。"""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(cleaned) == 12:
        return ":".join(cleaned[i : i + 2].upper() for i in range(0, 12, 2))
    return raw.upper()


@dataclass
class CapturedTargetSummary:
    """提取出的单个 AP 抓包摘要。"""

    bssid: str
    ssid: str = ""
    channel: str = ""
    has_pmkid: bool = False
    has_eapol: bool = False
    handshake_frames: str = ""
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

        # 深度分析本地抓包成果文件 (.pcapng / .cap)，与日志解析结果相互补全与校验
        try:
            from modules.capture_services.openwrt.result_converter import (
                analyze_handshake_cap,
                extract_beacon_ssids,
            )

            pmkid_set = {t.bssid for t in report.targets if t.has_pmkid}
            eapol_set = {t.bssid for t in report.targets if t.has_eapol}
            targets_dict = {t.bssid: t for t in report.targets}

            for pcap_info in captured_files:
                pcap_path = Path(pcap_info["path"])
                try:
                    # 1. 提取信标中的 SSID
                    ssids = extract_beacon_ssids(pcap_path, max_bytes=8 * 1024 * 1024)
                    for bssid_raw, ssid_name in ssids.items():
                        b_norm = format_mac(bssid_raw)
                        if b_norm in targets_dict:
                            if not targets_dict[b_norm].ssid and ssid_name:
                                targets_dict[b_norm].ssid = ssid_name
                        else:
                            targets_dict[b_norm] = CapturedTargetSummary(bssid=b_norm, ssid=ssid_name)

                    # 2. 深度分析四次握手与 PMKID 结构
                    _analysis, pairs = analyze_handshake_cap(pcap_path, max_bytes=16 * 1024 * 1024)
                    if pairs:
                        for (ap_bssid, _sta_bssid), pair_info in pairs.items():
                            ap_norm = format_mac(ap_bssid)
                            if ap_norm not in targets_dict:
                                targets_dict[ap_norm] = CapturedTargetSummary(bssid=ap_norm)
                            t_entry = targets_dict[ap_norm]

                            if pair_info.pmkid:
                                pmkid_set.add(ap_norm)
                                t_entry.has_pmkid = True
                                t_entry.details = "已捕获有效 PMKID"

                            has_usable_eapol = bool(pair_info.m1 or pair_info.m2 or pair_info.m3 or pair_info.m4)
                            if has_usable_eapol:
                                eapol_set.add(ap_norm)
                                t_entry.has_eapol = True

                            frames_found = []
                            if pair_info.m1:
                                frames_found.append("M1")
                            if pair_info.m2:
                                frames_found.append("M2")
                            if pair_info.m3:
                                frames_found.append("M3")
                            if pair_info.m4:
                                frames_found.append("M4")

                            if frames_found:
                                existing_frames = set(re.findall(r"M[1-4]", t_entry.handshake_frames))
                                existing_frames.update(frames_found)
                                sorted_f = [f for f in ["M1", "M2", "M3", "M4"] if f in existing_frames]
                                t_entry.handshake_frames = "+".join(sorted_f)

                            if len(pair_info.m1) > 0 and len(pair_info.m2) > 0:
                                t_entry.details = "已捕获可跑包四次握手 (M1+M2)"
                except Exception:
                    pass

            report.targets = list(targets_dict.values())
            report.pmkid_count = max(report.pmkid_count, len(pmkid_set))
            report.eapol_count = max(report.eapol_count, len(eapol_set))

            # 重新生成汇总文本
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
        except Exception:
            pass

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
        pmkid_found = set()
        eapol_found = set()
        targets_map: Dict[str, CapturedTargetSummary] = {}

        # 扫描 AP 发现记录 (兼容传统日志行格式)
        ap_patterns = [
            r"\[CH\s*([0-9a-zA-Z]+)\]\s+AP:\s*([^\s(]+)?\s*\(?([0-9a-fA-F:]{17})\)?",
            r"AP:\s*([0-9a-fA-F:]{17})\s*\(([^)]+)\)",
            r"AP:\s*([^\s]+)\s+([0-9a-fA-F:]{17})",
        ]

        # 正则编译：hcxdumptool 6.3.x 终端字符表格行
        # AP 行：'   1 08:52:54 + +     + 54a7035d5758 TP-LINK_abcd'
        hcx_ap_tbl_pat = re.compile(
            r"^\s*(\d+[a-zA-Z]?)\s+(\d{2}:\d{2}:\d{2})\s+([+ ]{9})\s+([0-9a-fA-F]{12})\s*(.*)$"
        )
        # 客户端行：' 08:52:54 + + 54a7035d5758 3ac62034d21b TP-LINK_abcd'
        hcx_sta_tbl_pat = re.compile(
            r"^\s*(\d{2}:\d{2}:\d{2})\s+([+ ]{3,5})\s+([0-9a-fA-F]{12})\s+([0-9a-fA-F]{12})\s*(.*)$"
        )

        for raw_line in log_text.splitlines():
            # 剥离 ANSI 彩色与光标控制码
            line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw_line).strip("\r")

            # 1. 尝试匹配 hcxdumptool 6.3.x AP 表格行
            m_ap = hcx_ap_tbl_pat.match(line)
            if m_ap:
                ch, _time, flags, hex_mac, essid = m_ap.groups()
                b_norm = format_mac(hex_mac)
                s_name = essid.strip()
                if b_norm not in targets_map:
                    targets_map[b_norm] = CapturedTargetSummary(bssid=b_norm, ssid=s_name, channel=ch)
                elif not targets_map[b_norm].ssid and s_name:
                    targets_map[b_norm].ssid = s_name

                # flags 9字符: R(0), ' '(1), 1(2)=M1, ' '(3), 3(4)=M3, ' '(5), P(6)=PMKID, ' '(7), S(8)=WPS
                has_m1 = len(flags) > 2 and flags[2] == "+"
                has_m3 = len(flags) > 4 and flags[4] == "+"
                has_p = len(flags) > 6 and flags[6] == "+"
                if has_p:
                    pmkid_found.add(b_norm)
                    targets_map[b_norm].has_pmkid = True
                    targets_map[b_norm].details = "已捕获有效 PMKID"
                if has_m1 or has_m3:
                    eapol_found.add(b_norm)
                    targets_map[b_norm].has_eapol = True
                    cur_frames = set(re.findall(r"M[1-4]", targets_map[b_norm].handshake_frames))
                    if has_m1:
                        cur_frames.add("M1")
                    if has_m3:
                        cur_frames.add("M3")
                    targets_map[b_norm].handshake_frames = "+".join([f for f in ["M1", "M2", "M3", "M4"] if f in cur_frames])
                continue

            # 2. 尝试匹配 hcxdumptool 6.3.x 客户端握手行
            m_sta = hcx_sta_tbl_pat.match(line)
            if m_sta:
                _time, flags, ap_hex, _sta_hex, essid = m_sta.groups()
                ap_norm = format_mac(ap_hex)
                s_name = essid.strip()
                if ap_norm not in targets_map:
                    targets_map[ap_norm] = CapturedTargetSummary(bssid=ap_norm, ssid=s_name)
                elif not targets_map[ap_norm].ssid and s_name:
                    targets_map[ap_norm].ssid = s_name

                # E 或 2 列有 '+' 代表收到客户端 EAPOL-Key M2 帧
                if "+" in flags:
                    eapol_found.add(ap_norm)
                    targets_map[ap_norm].has_eapol = True
                    targets_map[ap_norm].details = "已捕获客户端握手响应 (M2)"
                    cur_frames = set(re.findall(r"M[1-4]", targets_map[ap_norm].handshake_frames))
                    cur_frames.add("M2")
                    targets_map[ap_norm].handshake_frames = "+".join([f for f in ["M1", "M2", "M3", "M4"] if f in cur_frames])
                continue

            # 3. 传统 AP 匹配
            for pat in ap_patterns:
                m = re.search(pat, line)
                if m:
                    g = m.groups()
                    if len(g) == 3:
                        ch, ssid, bssid = g[0], g[1] or "", format_mac(g[2])
                    elif len(g) == 2:
                        if ":" in g[0] or len(g[0]) == 12:
                            bssid, ssid, ch = format_mac(g[0]), g[1], ""
                        else:
                            ssid, bssid, ch = g[0], format_mac(g[1]), ""
                    else:
                        continue
                    if bssid not in targets_map:
                        targets_map[bssid] = CapturedTargetSummary(bssid=bssid, ssid=ssid, channel=ch)
                    elif not targets_map[bssid].ssid and ssid:
                        targets_map[bssid].ssid = ssid

            # 4. 传统 PMKID 行扫描 (支持 17 位或 12 位 MAC)
            if "PMKID" in line:
                bssid_m = re.search(r"([0-9a-fA-F:]{17}|[0-9a-fA-F]{12})", line)
                if bssid_m:
                    b_id = format_mac(bssid_m.group(1))
                    pmkid_found.add(b_id)
                    if b_id in targets_map:
                        targets_map[b_id].has_pmkid = True
                    else:
                        targets_map[b_id] = CapturedTargetSummary(bssid=b_id, has_pmkid=True)
                else:
                    pmkid_found.add(f"unknown_{len(pmkid_found)}")

            # 5. 传统 EAPOL / handshake 行扫描
            if "EAPOL" in line or "handshake" in line.lower():
                bssid_m = re.search(r"([0-9a-fA-F:]{17}|[0-9a-fA-F]{12})", line)
                if bssid_m:
                    b_id = format_mac(bssid_m.group(1))
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
