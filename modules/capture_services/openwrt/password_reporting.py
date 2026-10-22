"""在线验证的状态播报；只消费既有事件，不参与验证策略。"""



class PasswordVerificationReporter:
    """将目标生命周期和候选计数转为现有消息回调，不接收密码。"""

    def __init__(self, targets, settings, message_callback, progress_callback):
        self.targets = targets
        self.settings = settings
        self.message_callback = message_callback
        self.progress_callback = progress_callback
        self.active = None
        self.submitted = 0
        self.last_reported = 0
        self.counts = dict(found=0, not_found=0, skipped=0, cancelled=0, error=0)

    def emit(self, text, level="info"):
        if self.message_callback is not None:
            self.message_callback(text, level)

    def prefix(self, index):
        target = self.targets[index]
        label = str(target.ssid or target.bssid or "")
        return f"在线验证第 {index + 1}/{len(self.targets)} 个：{label}"

    def target_event(self, index, entry=None):
        """entry 为空表示实际开始，否则按原返回结果播报一次结局。"""
        if entry is None:
            self.active = index
            self.submitted = self.last_reported = 0
            self.emit(f"{self.prefix(index)}，开始处理。")
            return
        if entry.found:
            status, level = "found", "success"
            detail = f"已找到密码，结果已写入文件；已试 {entry.tried}/{entry.total} 个候选。"
        elif entry.tried == 0:
            status, level = "skipped", "warning"
            detail = f"已跳过；{entry.note or '未执行候选验证。'}"
        else:
            status, level = "not_found", "warning"
            detail = f"未找到可用密码；已试 {entry.tried}/{entry.total} 个候选，未连上。"
        self.counts[status] += 1
        self.active = None
        self.emit(f"{self.prefix(index)}：{detail}", level)

    def progress(self, update):
        """沿用逐候选事件；每目标最多约十二条计数，不把提交当成完成。"""
        if self.progress_callback is not None:
            self.progress_callback(update)
        self.submitted = update.tried
        step = max(10, (update.total + 9) // 10)
        if update.tried == 1 or update.tried == update.total or update.tried - self.last_reported >= step:
            if update.tried <= self.last_reported:
                return
            self.last_reported = update.tried
            self.emit(
                f"{self.prefix(update.target_index)}：已提交 {update.tried}/{update.total} 个候选，"
                "当前候选的连接结果仍在核实。"
            )

    def interrupted(self, cancelled):
        """异常仍由原调用链抛出；这里只报告已知结局和未开始数量。"""
        status = "cancelled" if cancelled else "error"
        level = "info" if cancelled else "error"
        if self.active is not None:
            self.counts[status] += 1
            detail = "已取消" if cancelled else "出错，验证未完成"
            self.emit(
                f"{self.prefix(self.active)}：{detail}；已提交 {self.submitted} 个候选，"
                "未完成部分不能判定为未找到。", level,
            )
            self.active = None
        remaining = len(self.targets) - sum(self.counts.values())
        self.emit(
            f"在线密码验证中断汇总：找到 {self.counts['found']} 个，"
            f"未找到 {self.counts['not_found']} 个，跳过 {self.counts['skipped']} 个，"
            f"取消 {self.counts['cancelled']} 个，出错 {self.counts['error']} 个，"
            f"尚未开始 {remaining} 个。", level,
        )
