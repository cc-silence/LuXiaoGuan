"""Chinese progress and bounded, shareable diagnostic summaries."""
from datetime import datetime
import io
from contextlib import redirect_stdout
import zipfile

from .runner import Runner

TARGETS = {
    "app_manifest": "检查软件更新", "app_catalog": "读取更新文件目录",
    "app_catalog_standalone": "单独测试更新文件目录",
    "app_incremental_blob": "下载增量文件", "app_full_package": "下载软件完整包",
    "common_manifest": "检查常用依赖包", "common_archive": "下载常用依赖包",
    "capture_manifest": "检查抓包依赖包", "capture_archive": "下载抓包依赖包",
    "app_downloads": "软件更新下载",
}
REASONS = {
    "ok": "成功", "connection_timeout": "建立连接超时",
    "response_timeout": "等待服务器数据超时", "transfer_interrupted": "传输中断",
    "tls_failed": "加密连接失败", "dns_failed": "域名解析失败",
    "connection_denied": "系统拒绝建立连接（可能受防火墙或安全软件限制）",
    "proxy_failed": "代理连接失败", "http_status": "服务器返回错误状态",
    "total_deadline": "达到本次请求总时限", "cancelled": "已取消",
    "connection_closed_before_response": "收到响应前连接被关闭",
    "connection_or_response_failed": "连接或响应失败，现有证据无法进一步区分",
    "signed_metadata_invalid": "清单签名或目录校验失败",
    "hash_or_size_mismatch": "下载文件完整性校验失败",
    "signed_size_mismatch": "服务器声明的文件大小与签名不符",
    "truncated_sample": "文件首段不完整", "unexpected_encoding": "服务器返回了非预期的压缩编码",
    "invalid_content_range": "服务器返回的文件范围不正确", "size_limit": "内容超过允许大小",
    "worker_failed": "诊断子进程未正常完成",
    "no_verified_manifest": "未取得有效清单，跳过下载",
    "no_verified_catalog": "未取得有效更新目录，跳过增量文件",
    "run_download_budget": "超过本轮完整下载额度，跳过",
    "signed_snapshot_verified": "随包清单验签通过，仅用于继续测试下载",
    "snapshot_invalid": "随包清单不可用",
}
PROFILES = {"current": "原有等待", "long_wait": "延长等待", "long_wait_full": "完整下载校验"}


def describe(record):
    target = TARGETS.get(record.get("target"), record.get("target", "未知项目"))
    reason = REASONS.get(record.get("reason"), "其他错误，详见诊断记录")
    if record.get("source") == "bundled_signed_snapshot":
        return f"{target}：{reason}（不计作在线检查成功）"
    profile = PROFILES.get(record.get("profile"), "")
    seconds = record.get("elapsed_seconds", 0)
    size = record.get("received_bytes", 0)
    verified = "；文件指纹校验通过" if record.get("verified") else ""
    status = f"；服务器状态 {record['status']}" if record.get("status") else ""
    return f"{target} / {profile}：{reason}；耗时 {seconds:.1f} 秒；收到 {size:,} 字节{verified}{status}"


class ChineseRunner(Runner):
    def request(self, label, url, route, profile, **kwargs):
        if not self.cancel.is_set():
            print(f"正在{TARGETS.get(label, label)}（{PROFILES.get(profile['name'], profile['name'])}），请稍候……",
                  flush=True)
        with redirect_stdout(io.StringIO()):
            record, body = super().request(label, url, route, profile, **kwargs)
        if record:
            print(describe(record), flush=True)
        return record, body

    def seed(self, name, verifier):
        result = super().seed(name, verifier)
        if result is not None:
            print("在线清单未取得，改用随包已验签地址继续测下载；在线检查仍记为失败。", flush=True)
        return result

    def save(self):
        super().save()
        lines = [
            "路小管：直连更新及依赖下载测试报告",
            "生成时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "测试线路：真正直连（不使用系统代理或软件代理）",
            "延长等待：连接最多 45 秒；连续无数据最多 60 秒；单次检查最多 180 秒。",
            "完整文件最多 15 分钟；每轮先比较首段，再视连通情况下载完整文件并校验。",
            "随包清单只提供测试地址，不代表在线检查成功或当前最新版本。",
            "软件更新和依赖均未安装；正式软件参数没有修改。",
            "",
        ]
        lines.extend(describe(record) for record in self.report["records"])
        if self.report.get("cancelled"):
            lines.append("\n测试已取消，以上为已完成项目。")
        if self.report.get("fatal_error_type"):
            lines.append("\n诊断未完整完成，内部错误类型：" + self.report["fatal_error_type"])
        self.output.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8-sig")

    def export(self):
        self.save()
        archive = self.output.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            for path in (self.output, self.output.with_suffix(".txt")):
                target.write(path, path.name)
        return archive
