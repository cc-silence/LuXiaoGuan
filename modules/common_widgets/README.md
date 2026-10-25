# 独立版公共组件

- `header_corners.round_table_headers(root)`：在页面构造或缩放后调用，为水平表头添加仅左上、右上的 5px 缩放圆角。按表头尺寸事件更新，不改列宽、排序、底角或配色，随父控件释放。

- `NavigationButton`：主导航和设置分类共用的按钮，只在绘制时去掉虚线焦点框；保留 Tab/空格键、悬停和选中状态。

- `tooltip_policy.install_tooltip_policy(app)`: shared hover text wrapping, measured with the current UI font and bounded to the screen; supports widgets and table items without retaining widget caches.
- `FramelessDialog`: title dragging uses Qt system movement where available, preserving OS snapping; manual movement is only a platform fallback.
- `modules.main_pages.info_panel.InfoPanel`: shared log font, bounded log storage and `fontDeltaChanged` / `set_font_delta` for synchronized zoom across router pages.

- `settings_widgets.ContentSizedStackedWidget`: shared current-page stack for inline wireless settings. Size hints, minimum size and height-for-width all come from the current page, so hidden long forms cannot add blank space. Pages retain their original size policies.


- `InfoPanelDock(content, panel, orientation, parent, sizes)`: shared draggable information dock. Hides the panel completely, retains its share of available space, and moves the reverse-arrow control outside the hidden panel. Reopening restores the same panel and log history; no timer or duplicated log cache.

- `ConfirmDialog.secondary_selected`: true only after explicitly clicking the secondary action. Title-bar X and Escape keep it false, allowing dialogs with two continuation choices to distinguish cancellation.

- `config_fields.ConfigCheckBox`: native checkbox for configuration switches. `configure(value, preserve=True)` shows known 0/1 values; an unspecified value is indeterminate with an inherit label. `pending_value()` omits untouched values; user clicks choose definite on/off states. Uses the shared checkbox theme.
- `config_fields.ConfigValueList`: reusable add/remove text rows for DNS and MAC filter lists. `set_values()` / `values()` preserve order; at most 512 rows are retained. Removed rows are deleted through Qt ownership; no background work or timers.

- `FramelessDialog(window_controls=True)`: editing dialogs share `DialogWindowControls` for minimize, maximize/restore and title-bar double click. Controls preserve input and task ownership; resize grips hide while maximized. Use `window_controls=False` for confirmation, message, progress or a main window that already owns its chrome. Styles live in the existing light/dark settings partials.

Modal editors minimize the visible owner group to retain modal ownership; modeless editors minimize independently.

- `card_icon.CardIcon`: fixed-size, scale-aware colored card illustration; paints through QIcon without per-frame pixmap caches.

- icon_button.create_themed_icon_button accepts optional light_color and dark_color semantic colors; shared refresh retains them. Existing callers retain default behavior.



- `TaskProgressDialog`: shared task dialog with `RoundedProgressBar`, theme, scale and resource cleanup. Connect `cancelRequested`, send real stage/byte progress and call `finish` on every outcome. Completion stops busy animation; failure keeps the last known progress and late updates are ignored. Router backup/policy tasks use a thin `RouterTaskProgressDialog` adapter; avoid separate progress styles and fake timer percentages.

- `TableFilterBar(parent)`: always-visible single-line search and filtered/total count. Emits `changed(str)`; the table owns filtering. Put it in the footer beside status text and call `refresh_scale()` after appearance changes. No overlay, position persistence, mouse dragging or timer.
