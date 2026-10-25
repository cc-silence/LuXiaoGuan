"""单套代理设置：HTTP 和 SOCKS 参数编辑。"""

from __future__ import annotations

import copy

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit

from .settings_widgets import (
    create_hint_label,
    create_settings_scroll_page,
    create_settings_section_title,
    setup_proxy_input,
)


# 每个代理区域的表单字段：(label_text, placeholder, tooltip)
_PROXY_FIELDS = [
    ("HTTP 地址", "例如 127.0.0.1", "HTTP 代理服务器地址，一般是 127.0.0.1。"),
    ("HTTP 端口", "例如 1081", "HTTP 代理端口，例如 v2rayN 常见为 1081。端口填错会导致代理下载失败。"),
    ("SOCKS 地址", "例如 127.0.0.1", "SOCKS 代理服务器地址，一般是 127.0.0.1。"),
    ("SOCKS 端口", "例如 1080", "SOCKS5 代理端口，常见为 1080；检测和下载都可使用。端口填错会导致代理连接失败。"),
]


def _build_proxy_section(parent, title, scale_manager):
    """构建一个代理区域的表单。

    参数：parent 是父控件；title 是区域标题；scale_manager 用于间距缩放。
    返回值：dict，包含 group_label 和四个 QLineEdit 控件的引用。
    副作用：只创建控件，不读写配置。
    """
    section_label = create_settings_section_title(title, parent)

    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(scale_manager.px(10))

    inputs = {}
    for field_name, placeholder, tooltip in _PROXY_FIELDS:
        line_edit = QLineEdit(parent)
        setup_proxy_input(line_edit)
        line_edit.setPlaceholderText(placeholder)
        line_edit.setToolTip(tooltip)
        # 用 field_name 作为 key，如 "http_host"、"http_port"、"socks_host"、"socks_port"
        key = field_name.split()[0].lower() + "_" + field_name.split()[-1].lower()
        # 映射中文字段名到英文 key
        if "HTTP" in field_name and "地址" in field_name:
            key = "http_host"
        elif "HTTP" in field_name and "端口" in field_name:
            key = "http_port"
        elif "SOCKS" in field_name and "地址" in field_name:
            key = "socks_host"
        elif "SOCKS" in field_name and "端口" in field_name:
            key = "socks_port"
        inputs[key] = line_edit
        form.addRow(field_name, line_edit)

    return {"section_label": section_label, "form": form, "inputs": inputs}


def _collect_section_data(inputs):
    """从输入框收集数据为 dict。"""
    return {
        "http_host": inputs["http_host"].text().strip(),
        "http_port": inputs["http_port"].text().strip(),
        "socks_host": inputs["socks_host"].text().strip(),
        "socks_port": inputs["socks_port"].text().strip(),
    }


def _fill_section_data(inputs, data):
    """把数据填入输入框。"""
    inputs["http_host"].setText(str(data.get("http_host", "")))
    inputs["http_port"].setText(str(data.get("http_port", "")))
    inputs["socks_host"].setText(str(data.get("socks_host", "")))
    inputs["socks_port"].setText(str(data.get("socks_port", "")))


def build_proxy_page(window, *, immediate=False):
    """构建代理设置页面。

    参数：window 是 SettingsWindow 实例。
    返回值：BounceScrollArea 实例，包含单套 HTTP/SOCKS 编辑界面。
    副作用：读取 proxy_config.json；设置窗口"确定"时写入磁盘。
    """
    # 代理页必须复用 SettingsWindow 的同一管理器。
    # 导入分享包会替换该实例的内存数据；若这里另建实例，表单会继续显示旧配置，
    # 点击"确定"还会把旧数据写回磁盘并覆盖导入内容。
    mgr = window.proxy_config_manager
    mgr._ensure_loaded()
    initial_data = copy.deepcopy(mgr.to_dict())
    dirty = False

    page, container, layout = create_settings_scroll_page(window.pages, spacing=8)

    section = _build_proxy_section(container, "代理设置", window.scale_manager)
    layout.addWidget(section["section_label"])
    enabled_check = QCheckBox("开启代理", container)
    enabled_check.setObjectName("settingsProxyEnabled")
    enabled_check.setToolTip("关闭后只走直连，不使用已填写的代理或 Windows 系统代理。")
    window.scale_manager.bind_metrics(enabled_check, min_height=28)
    layout.addWidget(enabled_check)
    layout.addLayout(section["form"])

    def _fill_all(data):
        blocker = QSignalBlocker(enabled_check)
        enabled_check.setChecked(data.get("enabled", True))
        _fill_section_data(section["inputs"], data)
        for line_edit in section["inputs"].values():
            line_edit.setEnabled(enabled_check.isChecked())
        del blocker

    # ── 说明文字 ──────────────────────────────────────────
    hint = create_hint_label(
        '参数输入完成后自动保存。'
        '关闭代理后直接联网，已填写的内容会保留。开启后依次尝试 HTTP、SOCKS、系统代理，均失败后尝试直连。地址或端口留空的代理会跳过。',
        container,
    )
    layout.addWidget(hint)
    save_error = create_hint_label("", container)
    save_error.hide()
    layout.addWidget(save_error)

    layout.addStretch()

    # ── 数据回调：设置窗口"确定"/"取消"/"导入"时由 settings_window 调用 ──

    def _save_all():
        """把当前输入框内容写入管理器并写盘。

        返回值：True 表示数据有变化且已写盘，False 表示无变化。
        副作用：写盘，更新 initial_data 快照。
        """
        nonlocal dirty, initial_data
        data = {"enabled": enabled_check.isChecked(), **_collect_section_data(section["inputs"])}
        if data != mgr.to_dict():
            dirty = True
        if not dirty and not mgr.needs_migration:
            return False
        previous = mgr.to_dict()
        try:
            mgr.set_proxy(data)
            mgr.save()
        except (OSError, ValueError):
            mgr.from_dict(previous)
            raise
        initial_data = copy.deepcopy(mgr.to_dict())
        dirty = False
        return True

    def _flush():
        """Save completed edits and report invalid input without losing the edit."""
        try:
            _save_all()
        except (OSError, ValueError) as exc:
            save_error.setText("\u672a\u4fdd\u5b58\uff1a" + str(exc))
            save_error.show()
            return False
        save_error.clear()
        save_error.hide()
        return True

    def _discard_all():
        """恢复打开设置前的配置，并刷新输入框。"""
        nonlocal dirty
        mgr.from_dict(initial_data)
        dirty = False
        _fill_all(mgr.to_dict())

    def _replace_imported_data(data):
        """将导入的代理配置写入当前编辑会话并立即刷新输入框。

        参数：data 是分享包合并后的完整代理配置字典。
        副作用：替换共享管理器的内存数据、标记为待"确定"保存；
        不会直接写 proxy_config.json，因此点击"取消"仍可恢复打开设置前的内容。
        """
        nonlocal dirty
        mgr.from_dict(data)
        dirty = True
        _fill_all(mgr.to_dict())
        if immediate:
            _flush()

    # 注册回调到 window，供 settings_window 在确定/取消/导入时调用
    window.proxy_inputs = section["inputs"]
    window.proxy_enabled_check = enabled_check
    window._proxy_flush_fn = _flush
    window._proxy_save_fn = _save_all
    window._proxy_discard_fn = _discard_all
    window._proxy_replace_imported_data = _replace_imported_data

    # ── 初始加载 ────────────────────────────────────────
    _fill_all(mgr.to_dict())

    def _enabled_changed(enabled):
        for line_edit in section["inputs"].values():
            line_edit.setEnabled(enabled)
        if immediate:
            _flush()

    enabled_check.toggled.connect(_enabled_changed)
    if immediate:
        for line_edit in section["inputs"].values():
            line_edit.editingFinished.connect(_flush)

    return page
