"""Pure formatting for WPS timing; no clocks or UI-owned state."""
from math import isfinite


def candidate_timing_text(update):
    seconds = float(getattr(update, "candidate_elapsed", 0) or 0)
    seconds = max(0.0, seconds) if isfinite(seconds) else 0.0
    rate = getattr(update, "average_per_minute", None)
    if rate is not None:
        rate = float(rate)
    average = (f"{rate:.2f}\u4e2a/\u5206" if rate is not None and isfinite(rate) and rate >= 0
               else "\u7edf\u8ba1\u4e2d")
    return f"\u672c\u4e2a {seconds:.1f}\u79d2 \u00b7 \u5e73\u5747 {average}"


TIMING_NOTE = ("\u5e73\u5747\u901f\u5ea6\u6309\u672c\u76ee\u6807\u5de5\u5177\u62a5\u544a\u7684\u5019\u9009\u5207\u6362\u6570\u548c\u5df2\u8fc7\u65f6\u95f4\u7edf\u8ba1\uff0c"
               "\u5305\u542b\u7b49\u5f85\u548c\u91cd\u8bd5\uff0c\u4e0d\u662f\u6210\u529f\u7387\u3002"
               "\u8fde\u7eed 60 \u79d2\u65e0\u6709\u6548 WPS \u56de\u590d\u5219\u7ed3\u675f\u5f53\u524d\u76ee\u6807\uff0c\u7ee7\u7eed\u4e0b\u4e00\u4e2a\u3002")
