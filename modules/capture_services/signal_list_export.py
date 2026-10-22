"""扫描信号列表的本机导出，不读取路由器或修改扫描状态。"""

import csv
import math
import os
import re
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

HEADERS = ("序号", "Wi-Fi 名称", "路由器 MAC", "信号强度 (dBm)",
           "客户端数", "WPS", "信道", "加密", "频段")
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _power(point):
    value = point.power
    return value if isinstance(value, (int, float)) and math.isfinite(value) and -127 <= value < -1 else None


def signal_rows(points):
    """从冻结的全部信号生成排序行，未知强度放最后；不修改原数据。"""
    ordered = sorted(points, key=lambda p: (_power(p) is None, -(_power(p) or 0)))
    for number, point in enumerate(ordered, 1):
        wps = "不支持" if not point.wps_supported else ("已锁定" if point.wps_locked else "支持")
        yield (number, point.ssid or "<隐藏网络>", point.bssid,
               _power(point), len(point.clients), wps,
               point.channel if point.channel > 0 else None, point.encryption, point.band)


def _cell(value, column, row, style):
    reference = f"{chr(65 + column)}{row}"
    if value is None:
        return f'<c r="{reference}" s="{style}"/>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    text = escape(_BAD_XML.sub(" ", str(value)))
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _write_xlsx(path, points, check):
    """使用标准 Excel 文件结构逐行写入，不引入额外大型依赖。"""
    with ZipFile(path, "w", ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        book.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml", f'<workbook xmlns="{_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="信号列表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        book.writestr("xl/styles.xml", f'<styleSheet xmlns="{_NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF356A9A"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF0F5FA"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        with book.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(f'<worksheet xmlns="{_NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'.encode())
            for i, width in enumerate((8, 34, 23, 22, 14, 14, 10, 28, 12), 1):
                sheet.write(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'.encode())
            sheet.write(b'</cols><sheetData>')
            for row, values in enumerate(_with_header(points), 1):
                check()
                style = 1 if row == 1 else (2 if row % 2 == 0 else 0)
                xml = f'<row r="{row}" ht="22" customHeight="1">' + ''.join(_cell(v, c, row, style) for c, v in enumerate(values)) + '</row>'
                sheet.write(xml.encode('utf-8'))
            sheet.write(f'</sheetData><autoFilter ref="A1:I{len(points)+1}"/></worksheet>'.encode())


def _with_header(points):
    yield HEADERS
    yield from signal_rows(points)


def export_signal_list(points, destination, cancel_event=None, timeout=30):
    """导出不可变信号快照到 xlsx/txt，成功才原子替换目标，失败清理自有暂存。"""
    points = tuple(points)
    path = Path(destination)
    if path.suffix.lower() not in {".xlsx", ".txt"}:
        raise ValueError("请选择 .xlsx 或 .txt 文件。")
    if not points or len(points) > 100000:
        raise ValueError("信号列表为空或超过 100000 条，无法导出。")
    deadline = time.monotonic() + timeout
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("已取消导出。")
        if time.monotonic() >= deadline:
            raise TimeoutError("导出超时，请选择本机可写目录后重试。")
    check()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.winbox-signals-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
        if path.suffix.lower() == '.xlsx':
            _write_xlsx(temporary, points, check)
        else:
            with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter='\t')
                for values in _with_header(points):
                    check()
                    writer.writerow(_BAD_XML.sub(" ", v) if isinstance(v, str) else v for v in values)
        check()
        os.replace(temporary, path)
        return len(points)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
