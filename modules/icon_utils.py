# 图标颜色反转工具
# 亮色主题下将白色图标转为深灰色，暗色主题保持原样。
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QPointF, QByteArray, QRectF, QFile, QIODevice
from PySide6.QtGui import QImage, QIcon, QIconEngine, QPixmap, QPainter, QColor, QBrush, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

# 缓存：{"light": {resource_path: QIcon}, "dark": {resource_path: QIcon}}
_icon_cache: dict[str, dict[str, QIcon]] = {}

# 只替换内置的小位图；大 PNG、软件 Logo、用户选择的图片均保持原资源。
_SMALL_ICON_PATHS = {
    "icon_close.png": '<path d="M5 5l14 14M19 5L5 19"/>',
    "icon_minimize.png": '<path d="M5 12h14"/>',
    "icon_maximize.png": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
    "icon_restore.png": '<path d="M9 8V5h10v10h-3"/><rect x="5" y="9" width="10" height="10" rx="1"/>',
    "icon_menu.png": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "cil-reload.png": '<path d="M19 7a8 8 0 1 0 1 9M19 3v5h-5"/>',
    "cil-cloud-download.png": '<path d="M7 17H6a4 4 0 0 1-1-8 7 7 0 0 1 14-1 4.5 4.5 0 0 1-1 9h-1M12 11v10m-3-3 3 3 3-3"/>',
    "cil-wifi-signal-4.png": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M6 10a10 10 0 0 1 12 0M8.5 13a6 6 0 0 1 7 0M11 16a2 2 0 0 1 2 0"/>',
    "cil-link-alt.png": '<path d="m10 14 4-4M9 15l-1 1a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4 0M15 9l1-1a3 3 0 0 1 4 4l-4 4a3 3 0 0 1-4 0"/>',
    "cil-media-play.png": '<path d="m7 4 13 8-13 8Z"/>',
    "cil-media-pause.png": '<rect x="5" y="4" width="4" height="16" rx="1"/><rect x="15" y="4" width="4" height="16" rx="1"/>',
    "cil-x.png": '<path d="m5 5 14 14M19 5 5 19"/>',
    "cil-zoom-out.png": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6M7 10h6"/>',
    "cil-zoom-in.png": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6M7 10h6M10 7v6"/>',
    "cil-description.png": '<path d="M5 3h10l4 4v14H5ZM14 3v5h5M8 12h8M8 16h8"/>',
    "cil-star.png": '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
    "cil-terminal.png": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="m6 8 4 4-4 4M13 16h4"/>',
    "cil-folder.png": '<path d="M3 6h7l2 3h9v11H3Z"/>',
    "cil-folder-open.png": '<path d="M3 19V5h7l2 3h8v3M3 19l3-8h16l-3 8Z"/>',
    "cil-low-vision.png": '<path d="M3 12s3-6 9-6 9 6 9 6-3 6-9 6-9-6-9-6ZM3 21 21 3"/><circle cx="12" cy="12" r="3"/>',
    "cil-lock-locked.png": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
    "cil-file.png": '<path d="M5 3h9l5 5v13H5ZM14 3v6h5"/>',
    "cil-data-transfer-down.png": '<path d="M12 3v14m-5-5 5 5 5-5M4 21h16"/>',
    "cil-x-circle.png": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>',
    "cil-settings.png": '<path d="m10 3 4 0 .6 2.4 2 .9 2.3-.7 2 3.5-1.7 1.7v2.4l1.7 1.7-2 3.5-2.3-.7-2 .9L14 21h-4l-.6-2.4-2-.9-2.3.7-2-3.5L4.8 13v-2.2L3.1 9.1l2-3.5 2.3.7 2-.9Z"/><circle cx="12" cy="12" r="3"/>',
}
_SMALL_ICON_PATHS["cil-chevron-top.png"] = '<path d="m5 15 7-7 7 7"/>'
_SMALL_ICON_PATHS["cil-chevron-bottom.png"] = '<path d="m5 9 7 7 7-7"/>'
_SMALL_ICON_PATHS["icon_settings.png"] = _SMALL_ICON_PATHS["cil-settings.png"]


class _VectorIconEngine(QIconEngine):
    """按 Qt 请求的实际像素绘制小功能图标；不缓存每一帧或放大旧位图。"""

    def __init__(self, svg, color=None):
        super().__init__()
        self._svg = svg
        self._color = color
        self._renderer = QSvgRenderer(QByteArray(svg))

    def clone(self):
        return _VectorIconEngine(self._svg, self._color)

    def paint(self, painter, rect, mode, state):
        dpr = painter.device().devicePixelRatioF()
        pixmap = self.pixmap(QSize(round(rect.width() * dpr), round(rect.height() * dpr)), mode, state)
        pixmap.setDevicePixelRatio(dpr)
        painter.drawPixmap(rect, pixmap)

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self._renderer.render(painter, QRectF(pixmap.rect()))
        if self._color is not None:
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), self._color)
        painter.end()
        return pixmap


def _small_vector_icon(resource_path, color):
    """仅为确认过的小尺寸内置资源返回矢量图，足够大的 PNG 不作替换。"""
    name = Path(resource_path).name
    paths = _SMALL_ICON_PATHS.get(name)
    builtin_file = Path(__file__).resolve().parent.parent / "images" / "icons" / name
    if paths is None or not (resource_path.startswith(":/icons/images/icons/") or Path(resource_path) == builtin_file):
        return None
    image = QImage(resource_path)
    if image.isNull() or max(image.width(), image.height()) > 32:
        return None
    stroke = (color or QColor("#ffffff")).name()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{stroke}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
           f'{paths}</svg>').encode("utf-8")
    return QIcon(_VectorIconEngine(svg))


def _create_color_icon(resource_path: str, color: QColor) -> QIcon:
    """将白色图标的所有非透明像素替换为目标颜色，返回 QIcon。"""
    if resource_path.lower().endswith(".svg"):
        source = QFile(resource_path)
        if source.open(QIODevice.ReadOnly):
            svg = bytes(source.readAll())
            source.close()
            return QIcon(_VectorIconEngine(svg, color))
    img = QImage(resource_path)
    if img.isNull():
        return QIcon(resource_path)

    result = QImage(img.size(), QImage.Format_ARGB32)
    result.fill(Qt.transparent)

    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    painter.drawImage(0, 0, img)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QBrush(color))
    painter.end()

    return QIcon(QPixmap.fromImage(result))


def get_theme_icon(
    resource_path: str,
    is_light: bool,
    light_color: QColor | None = None,
    dark_color: QColor | None = None,
) -> QIcon:
    """根据主题返回对应颜色的图标（带内存缓存）。

    - 暗色主题默认直接返回资源图标；传入 dark_color 时统一着色为高对比颜色。
    - 亮色主题返回深灰色版本（#555555），也可通过 light_color 指定颜色。
    - dark_color 适合资源本身是深色图标、放在暗色按钮上不可见的场景。
    """
    color = (light_color or QColor(85, 85, 85)) if is_light else dark_color
    key = f"color:{color.name()}" if color is not None else "raw"
    if key not in _icon_cache:
        _icon_cache[key] = {}

    cached = _icon_cache[key].get(resource_path)
    if cached is not None:
        return cached

    vector = _small_vector_icon(resource_path, color)
    if vector is not None:
        icon = vector
    elif color is not None:
        icon = _create_color_icon(resource_path, color)
    else:
        icon = QIcon(resource_path)

    _icon_cache[key][resource_path] = icon
    return icon


def generate_dark_icon_file(resource_path: str, output_dir: str | Path) -> str:
    """从 Qt 资源路径加载白色图标，生成深灰色 (#555555) PNG 并保存。

    如果文件已存在则跳过生成，直接返回路径。

    - resource_path: Qt 资源路径，如 ":/icons/images/icons/cil-cloud-download.png"
    - output_dir:   输出目录绝对路径

    返回: 生成的文件绝对路径（字符串）
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(resource_path).name
    output_path = output_dir / filename
    if output_path.exists():
        return str(output_path)

    img = QImage(resource_path)
    if img.isNull():
        return resource_path

    result = QImage(img.size(), QImage.Format_ARGB32)
    result.fill(Qt.transparent)

    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    painter.drawImage(0, 0, img)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QBrush(QColor(85, 85, 85)))
    painter.end()

    result.save(str(output_path), "PNG")
    return str(output_path)


def invert_icon_file(file_path: str) -> QIcon:
    """加载图片文件，反转所有像素颜色，返回 QIcon。

    用于暗色主题下用户自定义图标颜色过深时，反转后提高可见性。
    内部使用 QImage.invertPixels() 做逐像素反转（RGBA 各通道 255 - 原值），保留 alpha 透明度。
    """
    img = QImage(file_path)
    if img.isNull():
        return QIcon(file_path)
    img.invertPixels()
    return QIcon(QPixmap.fromImage(img))


def get_visibility_icon(hidden: bool, color: QColor) -> QIcon:
    """返回共用屏幕/斜线屏幕图标；多尺寸缓存适配表头与高 DPI 按钮。"""
    key = f"visibility:{color.name(QColor.HexArgb)}"
    icons = _icon_cache.setdefault(key, {})
    state = str(bool(hidden))
    if state not in icons:
        icon = QIcon()
        for size in (16, 18, 24, 32, 48, 64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(size / 24, size / 24)
            painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            outline = QPainterPath()
            outline.addRoundedRect(2, 4, 20, 13, 1, 1)
            painter.drawPath(outline)
            painter.drawLine(QPointF(12, 17), QPointF(12, 21))
            painter.drawLine(QPointF(8, 21), QPointF(16, 21))
            if hidden:
                painter.drawLine(QPointF(3, 3), QPointF(21, 21))
            painter.end()
            icon.addPixmap(pixmap)
        icons[state] = icon
    return icons[state]


def clear_icon_cache():
    """释放主题图标缓存。

    调用方可以在主题切换或主窗口待机释放后调用。已分配给控件的 QIcon 不会受影响，
    只清除本模块为后续复用保留的 Python 引用。
    """
    _icon_cache.clear()
