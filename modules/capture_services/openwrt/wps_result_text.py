"""Concise user results; protocol evidence remains in diagnostics and native result notes."""


def result_message(entry):
    note = str(entry.note or "")
    if entry.psk_ok:
        message, level = "\u5df2\u627e\u5230 Wi-Fi \u5bc6\u7801\uff0c\u7ed3\u679c\u5df2\u4fdd\u5b58\u3002", "success"
    elif entry.pin_ok:
        message, level = "\u5df2\u627e\u5230 PIN\uff0c\u6682\u672a\u53d6\u5f97 Wi-Fi \u5bc6\u7801\u3002", "success"
    else:
        level = "warning"
        if "\u4e1a\u52a1\u63a5\u53e3" in note and "\u672c\u6b21\u5c1a\u672a\u6388\u6743" in note:
            message = "\u672a\u542f\u52a8\uff1a\u6240\u9009\u65e0\u7ebf\u7f51\u5361\u6b63\u5728\u4f7f\u7528\uff0c\u672c\u6b21\u672a\u786e\u8ba4\u4e34\u65f6\u63a5\u7ba1\u3002"
        elif "\u5df2\u9501\u5b9a" in note and not entry.tried_seconds:
            message = "\u8def\u7531\u5668 WPS \u5df2\u9501\u5b9a\uff0c\u5df2\u8df3\u8fc7\u3002"
        elif "\u9650\u6d41" in note or "WPS \u9501\u5b9a" in note:
            message = "\u672a\u62ff\u5230 PIN\uff0c\u8def\u7531\u5668\u6682\u65f6\u9650\u5236\u4e86\u5c1d\u8bd5\u3002"
        elif "60 \u79d2\u65e0\u6709\u6548 WPS \u56de\u590d" in note:
            message = "\u672a\u62ff\u5230 PIN\uff0c\u8def\u7531\u5668\u957f\u65f6\u95f4\u6ca1\u6709\u56de\u5e94\u3002"
        elif "\u65e0\u6cd5\u5173\u8054" in note or "\u5173\u8054\u672a\u5b8c\u6210" in note:
            message = "\u672a\u62ff\u5230 PIN\uff0c\u672a\u80fd\u8fde\u63a5\u5230\u8def\u7531\u5668\u3002"
        elif "\u672a\u5339\u914d" in note:
            message = "\u672a\u5339\u914d\u5230\u53ef\u7528\u7684\u9ed8\u8ba4 PIN\uff0c\u5df2\u8df3\u8fc7\u3002"
        elif "\u672a\u68c0\u6d4b\u5230" in note and "WPS" in note:
            message = "\u672a\u68c0\u6d4b\u5230 WPS\uff0c\u5df2\u8df3\u8fc7\u3002"
        elif any(word in note for word in ("\u8fd0\u884c\u65f6\u9650", "\u8fd0\u884c\u65f6\u95f4", "\u9a8c\u8bc1\u4e0a\u9650")):
            message = "\u5df2\u5230\u8bbe\u5b9a\u65f6\u95f4\uff0c\u672a\u62ff\u5230 PIN\u3002"
        elif "\u672a\u786e\u8ba4\u5b8c\u6210\u5355\u4e2a\u5019\u9009" in note or "\u672a\u786e\u8ba4\u539f\u751f\u8fdb\u5ea6" in note:
            message = "\u672c\u4fe1\u53f7\u6d4b\u8bd5\u672a\u5b8c\u6210\uff0c\u672a\u62ff\u5230 PIN\u3002"
        elif entry.tried_seconds:
            message = "\u672c\u6b21\u672a\u62ff\u5230 PIN\u3002"
        else:
            message = "\u672c\u4fe1\u53f7\u6d4b\u8bd5\u672a\u5b8c\u6210\uff0c\u8bf7\u67e5\u770b\u8bca\u65ad\u8bb0\u5f55\u3002"
    if entry.wpc_file:
        message += "\u8fdb\u5ea6\u5df2\u4fdd\u5b58\u3002"
    cleanup = (("\u6e05\u7406" in note and any(word in note for word in ("\u5931\u8d25", "\u5f02\u5e38", "\u672a\u786e\u8ba4")))
               or "\u6062\u590d\u5931\u8d25" in note or "\u672a\u786e\u8ba4\u6062\u590d" in note
               or ("\u505c\u6b62" in note and "\u8fdb\u7a0b\u5931\u8d25" in note))
    if cleanup:
        message += "\u4e34\u65f6\u8d44\u6e90\u672a\u6e05\u7406\u5b8c\u6210\uff0c\u8bf7\u4fdd\u6301\u8def\u7531\u5668\u5728\u7ebf\u3002"
        level = "warning"
    return message, level


def record_detail(text):
    """Use the existing bounded, redacted history; no extra file cache."""
    from modules.log_history import current_history
    history = current_history()
    if history is not None and text:
        try:
            history.append("\u6293\u5305", str(text), "diagnostic", channel="wps-result-details")
        except (OSError, RuntimeError):
            from modules.capture_services.capture_logging import LOGGER
            LOGGER.warning("WPS result detail could not be saved")


def failure_message(summary):
    summary = str(summary)
    if "\u5df2\u53d6\u6d88" in summary:
        text = "PIN \u6d4b\u8bd5\u5df2\u505c\u6b62\u3002"
    elif "SSH \u8fde\u63a5\u5df2\u65ad\u5f00" in summary:
        text = "\u8fde\u63a5\u5df2\u65ad\u5f00\uff0cPIN \u6d4b\u8bd5\u5df2\u505c\u6b62\u3002"
    else:
        text = "PIN \u6d4b\u8bd5\u672a\u5b8c\u6210\uff0c\u8bf7\u67e5\u770b\u8bca\u65ad\u8bb0\u5f55\u3002"
    if "\u8fdb\u5ea6\u5df2\u81ea\u52a8\u4fdd\u5b58" in summary:
        text += "\u8fdb\u5ea6\u5df2\u4fdd\u5b58\u3002"
    if ("\u6e05\u7406" in summary and any(word in summary for word in ("\u5931\u8d25", "\u5f02\u5e38", "\u672a\u786e\u8ba4"))) or "\u6062\u590d\u5931\u8d25" in summary or ("\u505c\u6b62" in summary and "\u8fdb\u7a0b\u5931\u8d25" in summary):
        text += "\u4e34\u65f6\u8d44\u6e90\u672a\u6e05\u7406\u5b8c\u6210\uff0c\u8bf7\u4fdd\u6301\u8def\u7531\u5668\u5728\u7ebf\u3002"
    return text
