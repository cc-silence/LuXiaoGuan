# 独立版公共组件

- `header_corners.round_table_headers(root)`：在页面构造或缩放后调用，为水平表头添加仅左上、右上的 5px 缩放圆角。按表头尺寸事件更新，不改列宽、排序、底角或配色，随父控件释放。

- `NavigationButton`：主导航和设置分类共用的按钮，只在绘制时去掉虚线焦点框；保留 Tab/空格键、悬停和选中状态。

- `tooltip_policy.install_tooltip_policy(app)`: shared hover text wrapping, measured with the current UI font and bounded to the screen; supports widgets and table items without retaining widget caches.
- `FramelessDialog`: title dragging uses Qt system movement where available, preserving OS snapping; manual movement is only a platform fallback. Enter activates the focused push button, including secondary and cancel actions; other single-line fields fall back to the enabled `dialogAcceptButton`. Multiline editors preserve their own Enter handling.
- `modules.main_pages.info_panel.InfoPanel`: shared log font, bounded log storage and `fontDeltaChanged` / `set_font_delta` for synchronized zoom across router pages.

- `settings_widgets.ContentSizedStackedWidget`: shared current-page stack for inline wireless settings. Size hints, minimum size and height-for-width all come from the current page, so hidden long forms cannot add blank space. Pages retain their original size policies.


- `InfoPanelDock(content, panel, orientation, parent, sizes)`: shared draggable information dock. Hides the panel completely, retains its share of available space, and moves the reverse-arrow control outside the hidden panel. Reopening restores the same panel and log history; no timer or duplicated log cache.

- `ConfirmDialog.secondary_selected`: true only after explicitly clicking the secondary action. Title-bar X and Escape keep it false, allowing dialogs with two continuation choices to distinguish cancellation.

- `config_fields.ConfigCheckBox`: native checkbox for configuration switches. `configure(value, preserve=True)` shows known 0/1 values; an unspecified value is indeterminate with an inherit label. `pending_value()` omits untouched values; user clicks choose definite on/off states. Uses the shared checkbox theme.
- `config_fields.ConfigValueList`: reusable add/remove text rows for DNS and MAC filter lists. `set_values()` / `values()` preserve order; at most 512 rows are retained. Removed rows are deleted through Qt ownership; no background work or timers. Optional `set_item_validator(callback)` reuses the shared `inputError` styling on individual rows; the callback returns an error message or an empty string. `validate(required=...)` checks nonempty rows (and an empty required list), while `validationChanged` reports the first error or clears it after edits/removals. Lists without a validator keep their existing behavior.

- `FramelessDialog(window_controls=True)`: editing dialogs share `DialogWindowControls` for minimize, maximize/restore and title-bar double click. Controls preserve input and task ownership; resize grips hide while maximized. Use `window_controls=False` for confirmation, message, progress or a main window that already owns its chrome. Styles live in the existing light/dark settings partials.

Modal editors minimize the visible owner group to retain modal ownership; modeless editors minimize independently.

- `card_icon.CardIcon`: fixed-size, scale-aware colored card illustration; paints through QIcon without per-frame pixmap caches.

- icon_button.create_themed_icon_button accepts optional light_color and dark_color semantic colors; shared refresh retains them. Existing callers retain default behavior.



- `TaskProgressDialog`: shared task dialog with `RoundedProgressBar`, theme, scale and resource cleanup. Connect `cancelRequested`, send real stage/byte progress and call `finish` on every outcome. Completion stops busy animation; failure keeps the last known progress and late updates are ignored. Router backup/policy tasks use a thin `RouterTaskProgressDialog` adapter; avoid separate progress styles and fake timer percentages. The router adapter can show a worker-supplied monotonic phase deadline and no-power-off notice. Its clock only updates text; completion stops it immediately, and reaching zero never implies success.

- `TableFilterBar(parent)`: always-visible single-line search and filtered/total count. Emits `changed(str)`; the table owns filtering. Put it in the footer beside status text and call `refresh_scale()` after appearance changes. No overlay, position persistence, mouse dragging or timer.

- `InfoPanel.append_info(text, "diagnostic")`: retain bounded detailed evidence in `export_entries()` without rendering it, including after theme/privacy redraws. Visible messages continue to use info/success/warning/error.


The OpenWrt RouterTaskProgressDialog adapter can optionally display a bounded, read-only LAN guidance panel using the shared settingsMessageText theme and CommonComboBox. Its networkRenewRequested signal carries only the selected adapter identity; the widget performs no OS networking operations. ConfigurationProgressController routes the request to the task-bound recovery service and owns/disconnects explicit Qt connection handles. The panel supports light/dark themes and UI scaling; static or unsupported interfaces show guidance without a recovery action.

- `button_focus_style.install_button_focus_style(app)`: install once before widget creation; shares the existing WidgetFilterStyle application proxy and wraps the current platform style to hide native focus rectangles on push/tool buttons, preserving Tab/Space, input focus and existing theme drawing.

- `NavigationButton.stack_icon_text`: defaults to `True` for main navigation. `SettingsNavPanel` sets it to `False` to use the native icon-left, vertically centered label layout and existing theme alignment.

- `ModelessMessageGroup`: shared themed notifications using `show()` and `Qt.NonModal`. Windows close independently and delete on close; at most 8 windows remain open, with subsequent messages grouped into the last window (latest 64). Optional `max_windows=1, incremental=True` appends new messages to the same unacknowledged window without rebuilding its text; `max_messages` bounds the buffer. Password-found notifications use this mode and keep summaries separate. Callers retain durable result files; closing clears displayed text and in-memory message buffers. No nested event loop, acknowledgment queue, worker-thread widget creation or timers.

- `TableFilterPanel`: table-owned, fixed-position collapsible search and filter controls. It emits state changes without changing row selection, uses viewport resize events without timers, and uses the packet capture light/dark theme rules. The softly colored opaque toggle uses compact upright vertical text at the viewport bottom-right with a 2px logical inset; the filter panel expands to its left.

- Page text buttons may set `compactAction=true` inside the capture theme to use a compact toolbar height while retaining their normal/danger colors.

- `FloatingFeedback.show_feedback(..., anchor=QPoint(...))`: optional position in parent coordinates for feedback beside a completed click; clamps the animation to the parent. Omit it to keep the existing bottom-right placement.
- `SpacedItemDelegate(..., show_focus_rect=False, minimum_height=False)`: suppress cell focus rectangles while keeping the original size hint; the default minimum row height remains enabled for other callers.


- `ElidingCheckBox`: native checkbox drawing and interaction; long labels elide only when width is constrained, preserving adjacent action buttons. `text()` retains the complete value and callers keep their normal detailed tooltip. Size hints follow actual style and font metrics; no timers or text mutation.

### PrivacyLogView

Read-only streaming log view with reversible display privacy. Use `insert_raw_text()` to append and `setPlainText()` to replace a frame; use `raw_text()` only for internal deduplication. Copy and selection always use displayed text. Raw storage has a 200,000 UTF-16-unit bound and trims complete rows. `setMaximumBlockCount()` limits visible rows; `context_blocks` reserves a small additional raw prefix for fields spanning multiple rows (34 for capture output, 1 by default). Appends reproject only that bounded suffix; identity changes rebuild the bounded display. An optional projector contributes sensitive spans to the common `Masker.text(..., extra_spans=...)` operation, so overlapping and multiline identities are replaced once against original text.

InfoPanel display text is projected through `modules.info_message_text.user_info_text`; raw entries and persistent export history remain unchanged. Mark internal checks as `diagnostic` at their source. Keep actionable failures visible; do not filter arbitrary messages by technical keywords.
