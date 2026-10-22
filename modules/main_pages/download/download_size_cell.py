"""下载页大小列：统一单位、多文件选择汇总和实际下载大小刷新。"""
from modules.version_services.download_size import declared_file_size

SIZE_COLUMN = 7


def format_file_size(size):
    """按 1024 进制显示 KB、MB、G，未知大小显示占位符。"""
    if type(size) is not int or size < 0:
        return '--'
    if size < 1024 ** 2:
        value, unit = size / 1024, 'KB'
    elif size < 1024 ** 3:
        value, unit = size / 1024 ** 2, 'MB'
    else:
        value, unit = size / 1024 ** 3, 'G'
    text = f'{value:.2f}'.rstrip('0').rstrip('.')
    if size > 0 and text == '0':
        text = '0.01'
    return f'{text} {unit}'


def refresh_download_size(page, row_data, result=None, task=None):
    """原位更新大小列，进度变化不重建单元格；不把部分已知大小当成总大小。"""
    table = getattr(page, 'table', None)
    if table is None or table.columnCount() <= SIZE_COLUMN:
        return
    if result is None:
        result = page._check_results.get(row_data.get('id', ''), {})
    downloads = result.get('downloads')
    multiple = isinstance(downloads, list) and len(downloads) > 1
    known = row_data.setdefault('_download_file_sizes', {})
    if task is not None:
        size = getattr(task, 'total_length', 0)
        if type(size) is int and size > 0:
            key = str(getattr(task, 'url', '') or getattr(task, 'filename', '')) if multiple else ''
            known[key] = size
    if isinstance(downloads, list) and downloads:
        indexes = row_data.get('_selected_multi_download_indexes') or range(len(downloads))
        items = [downloads[index] for index in sorted(set(indexes)) if type(index) is int and 0 <= index < len(downloads) and isinstance(downloads[index], dict)]
    else:
        items = [result]
    sizes, details = [], []
    for item in items:
        key = str(item.get('url') or item.get('download_url') or item.get('filename') or '') if multiple else ''
        size = known.get(key, declared_file_size(item))
        sizes.append(size)
        details.append(f"{item.get('filename') or item.get('name') or '文件'}：{format_file_size(size)}")
    total = sum(sizes) if sizes and all(size is not None for size in sizes) else None
    if total is None and multiple and not row_data.get('_selected_multi_download_indexes'):
        total = declared_file_size(result)
    text = format_file_size(total)
    tooltip = '\n'.join(details) if len(items) > 1 else ('文件大小未知，开始下载后会尝试更新。' if total is None else f'{total:,} 字节')
    row = page._row_for_data(row_data)
    if row is None:
        return
    item = table.item(row, SIZE_COLUMN)
    if item is None:
        page._set_row_text(row, SIZE_COLUMN, text, row_data, tooltip=tooltip)
    else:
        if item.text() != text:
            item.setText(text)
        if item.toolTip() != tooltip:
            item.setToolTip(tooltip)
