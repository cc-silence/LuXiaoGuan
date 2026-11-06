"""在线验证的状态播报；只消费既有事件，不参与验证策略。"""



class PasswordVerificationReporter:
    """将目标生命周期和候选计数转为现有消息回调，不接收密码。"""

    def __init__(self, targets, settings, message_callback, progress_callback):
        self.targets = targets
        self.settings = settings
        self.message_callback = message_callback
        self.progress_callback = progress_callback
        self._attempt_update = None
        self._speed_text = "\u901f\u5ea6\uff1a\u7edf\u8ba1\u4e2d"
        self.active = None
        self.started = set()
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
            self._attempt_update = None
            self._speed_text = "\u901f\u5ea6\uff1a\u7edf\u8ba1\u4e2d"
        if self.progress_callback is not None:
            from .password_verification import PasswordVerifyUpdate
            target = self.targets[index]
            from .password_events import target_update_for
            self.progress_callback(target_update_for(target, entry))
            self.progress_callback(PasswordVerifyUpdate(
                tried=0 if entry is None else entry.tried,
                total=0 if entry is None else entry.total,
                target_label=str(target.ssid or target.bssid),
                target_index=index, target_total=len(self.targets),
                target_bssid=str(target.bssid) if entry is None else "",
                speed_text=self._speed_text,
            ))
        if entry is None:
            self.active = index
            self.started.add(index)
            self.submitted = self.last_reported = 0
            self.emit(f"{self.prefix(index)}，开始处理。")
            return
        if not getattr(entry, "final", True):
            pending = getattr(entry, "unconfirmed", 0)
            extra = f"其中 {pending} 个连接结果未确认，不判定为密码错误。" if pending else ""
            self.emit(f"{self.prefix(index)}：本轮已完成，后续字典继续尝试；累计已试 {entry.tried} 个，历史跳过 {entry.skipped} 个。{extra}")
            self.active = None
            return
        if getattr(entry, "error", False):
            status, level = "error", "warning"
            detail = entry.note
        elif entry.found:
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

    def dictionary_round(self, number, total, name):
        self.emit(f"字典第 {number}/{total} 轮：{name}，本轮先尝试所有尚未成功的选中信号。")

    def progress(self, update):
        """沿用逐候选事件；每目标最多约十二条计数，不把提交当成完成。"""
        from dataclasses import replace
        from .password_speed import PasswordAttemptElapsed
        if isinstance(update, PasswordAttemptElapsed):
            if self._attempt_update is not None:
                self._speed_text = f"\u901f\u5ea6\uff1a{update.seconds:.1f} \u79d2/\u4e2a"
                if self.progress_callback is not None:
                    self.progress_callback(replace(self._attempt_update, speed_only=True,
                                                   speed_text=self._speed_text,
                                                   attempt_started=0.0, attempt_elapsed=update.seconds))
            self._attempt_update = None
            return
        from .password_verification import PasswordVerifyUpdate
        if isinstance(update, PasswordVerifyUpdate):
            update = replace(update, speed_text=self._speed_text)
            # Do not retain the transient candidate secret in statistics.
            self._attempt_update = replace(update, candidate="") if update.attempt_started > 0 else None
        if self.progress_callback is not None:
            self.progress_callback(update)
        from .password_events import PasswordFoundNotice
        if isinstance(update, PasswordFoundNotice):
            return
        if getattr(update, "phase", ""):
            self.emit(f"{self.prefix(update.target_index)}\uff1a{update.phase}")
            return
        if update.tried == 1 and self.last_reported == 0:
            skipped = getattr(update, 'history_skipped', 0)
            if skipped:
                self.emit(f"{self.prefix(update.target_index)}\uff1a\u5df2\u8df3\u8fc7 {skipped} \u4e2a\u5386\u53f2\u5df2\u786e\u8ba4\u5019\u9009\uff0c\u672c\u8f6e\u4ecd\u9700\u9a8c\u8bc1 {update.total} \u4e2a\u3002")
            engine = getattr(update, "engine", "")
            if engine in {"native", "compatibility"}:
                text = "快速验证" if engine == "native" else "兼容验证（逐次重载临时无线配置）"
                self.emit(f"{self.prefix(update.target_index)}：本目标使用{text}。")
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
            if self.progress_callback is not None:
                from .password_events import PasswordTargetUpdate
                target = self.targets[self.active]
                self.progress_callback(PasswordTargetUpdate(target.ssid,target.bssid,
                    'cancelled' if cancelled else 'failed'))
            self.counts[status] += 1
            detail = "已取消" if cancelled else "出错，验证未完成"
            self.emit(
                f"{self.prefix(self.active)}：{detail}；已提交 {self.submitted} 个候选，"
                "未完成部分不能判定为未找到。", level,
            )
            self.active = None
        remaining = len(self.targets) - len(self.started)
        self.emit(
            f"在线密码验证中断汇总：找到 {self.counts['found']} 个，"
            f"未找到 {self.counts['not_found']} 个，跳过 {self.counts['skipped']} 个，"
            f"取消 {self.counts['cancelled']} 个，出错 {self.counts['error']} 个，"
            f"尚未开始 {remaining} 个。", level,
        )
