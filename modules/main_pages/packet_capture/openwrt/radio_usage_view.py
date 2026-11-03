"""One existing badge style for all automatic-capture admission displays."""
from modules.capture_services.openwrt.radio_usage_state import radio_state

_LABELS = {
    "idle": "\u3010\u7a7a\u95f2\u3011(\u5b89\u5168\u65e0\u611f)",
    "business": "\u3010\u6b63\u5728\u4f7f\u7528\u3011(\u65e0\u7ebf\u4e1a\u52a1\u5df2\u542f\u7528)",
    "blocked": "\u3010\u76d1\u542c\u63a5\u53e3\u51b2\u7a81\u3011(\u5f52\u5c5e\u5f85\u6838\u5b9e)",
    "unknown": "\u3010\u72b6\u6001\u5f85\u786e\u8ba4\u3011(\u8bf7\u5237\u65b0\u65e0\u7ebf\u72b6\u6001)",
}


def apply_usage_status(card):
    state = radio_state(card.radio_info)
    card.status_badge.setText(_LABELS[state])
    color = "#10b981" if state == "idle" else "#f59e0b"
    card.status_badge.setStyleSheet("color: " + color + "; font-weight: bold;")
    card.takeover_check.setVisible(state == "business")
