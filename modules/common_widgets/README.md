# 独立版公共组件

- `header_corners.round_table_headers(root)`：在页面构造或缩放后调用，为水平表头添加仅左上、右上的 5px 缩放圆角。按表头尺寸事件更新，不改列宽、排序、底角或配色，随父控件释放。

- `NavigationButton`：主导航和设置分类共用的按钮，只在绘制时去掉虚线焦点框；保留 Tab/空格键、悬停和选中状态。

- `tooltip_policy.install_tooltip_policy(app)`: shared hover text wrapping, measured with the current UI font and bounded to the screen; supports widgets and table items without retaining widget caches.
- `FramelessDialog`: title dragging uses Qt system movement where available, preserving OS snapping; manual movement is only a platform fallback.
- `modules.main_pages.info_panel.InfoPanel`: shared log font, bounded log storage and `fontDeltaChanged` / `set_font_delta` for synchronized zoom across router pages.

- `settings_widgets.ContentSizedStackedWidget`: shared current-page stack for inline wireless settings. Size hints, minimum size and height-for-width all come from the current page, so hidden long forms cannot add blank space. Pages retain their original size policies.

- `TableSearchOverlay(table)`: WiKey-style bottom-right arrow with a single-line, clearable search field and visible/total count. Emits `changed(str)`; the host owns filtering. Call `refresh_scale()` after theme/scale changes. Uses viewport resize events, never a timer; closing the panel preserves the current filter.

- `InfoPanelDock(content, panel, orientation, parent, sizes)`: shared draggable information dock. Hides the panel completely, retains its share of available space, and moves the reverse-arrow control outside the hidden panel. Reopening restores the same panel and log history; no timer or duplicated log cache.

- `ConfirmDialog.secondary_selected`: true only after explicitly clicking the secondary action. Title-bar X and Escape keep it false, allowing dialogs with two continuation choices to distinguish cancellation.
