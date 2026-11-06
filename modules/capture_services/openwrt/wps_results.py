"""Confirmed WPS credentials are written only to the result artifact."""
from pathlib import Path
from .result_converter import final_results_dir, safe_result_stem


def write_wps_result(output_dir, ssid, bssid, pin, psk):
    if not pin and not psk:
        return ""
    out_path = final_results_dir(Path(output_dir)) / f"{safe_result_stem(ssid, bssid)}-wps.txt"
    lines = [f"MAC地址: {bssid}", f"信号名称: {ssid}", f"PIN码: {pin or ''}"]
    if psk:
        lines.append(f"密码: {psk}")
    else:
        lines.append("提示：仅拿到 PIN，未取得 Wi-Fi 密码。")
    out_path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return str(out_path)
