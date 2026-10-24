"""Read APK v3 indexes using upstream apk-tools adb.h and apk_adb.h.

Shared bounded metadata reader. Package files require SHA256 and native signature checks.
"""
import struct
import zlib

MAX_INDEX_SIZE = 32 * 1024 * 1024


class AdbReader:
    """\u6709\u754c\u8bfb\u53d6\u5b98\u65b9 ADB \u5bf9\u8c61\uff0c\u683c\u5f0f\u9519\u8bef\u76f4\u63a5\u62d2\u7edd\u3002"""
    def __init__(self, raw):
        if raw[:4] in (b"ADBd", b"ADBc"):
            offset = 4 if raw[:4] == b"ADBd" else 6
            if offset == 6 and raw[4] != 1:
                raise ValueError("Unsupported APK index compression")
            decoder = zlib.decompressobj(-15)
            raw = decoder.decompress(raw[offset:], MAX_INDEX_SIZE + 1)
            if not decoder.eof or len(raw) > MAX_INDEX_SIZE:
                raise ValueError("APK index exceeds decompression limit")
        if raw[:8] != b"ADB.indx":
            raise ValueError("Expected APK v3 repository index")
        header = struct.unpack_from("<I", raw, 8)[0]
        size = header & 0x3fffffff
        if header >> 30 or not 12 <= size <= len(raw) - 8:
            raise ValueError("Invalid APK metadata block")
        self.data = raw[12:8 + size]
        if self.data[0] != 0:
            raise ValueError("Unsupported ADB compatibility version")

    def take(self, pos, size):
        if pos < 0 or size < 0 or pos + size > len(self.data):
            raise ValueError("ADB offset outside metadata block")
        return self.data[pos:pos + size]

    def integer(self, value):
        kind, pos = value >> 28, value & 0xfffffff
        if kind == 1:
            return pos
        if kind in (2, 3):
            return int.from_bytes(self.take(pos, 4 if kind == 2 else 8), "little")
        if value == 0:
            return 0
        raise ValueError("Expected ADB integer")

    def object(self, value):
        if value == 0:
            return [0]
        if value >> 28 not in (13, 14):
            raise ValueError("Expected ADB object")
        pos = value & 0xfffffff
        count = int.from_bytes(self.take(pos, 4), "little")
        if not 1 <= count <= 100000:
            raise ValueError("Invalid ADB object count")
        return list(struct.unpack("<" + "I" * count, self.take(pos, 4 * count)))

    def text(self, value):
        if value == 0:
            return ""
        kind, pos = value >> 28, value & 0xfffffff
        width = {8: 1, 9: 2, 10: 4}.get(kind)
        if width is None:
            raise ValueError("Expected ADB string")
        size = int.from_bytes(self.take(pos, width), "little")
        return self.take(pos + width, size).decode("utf-8")

    def dependencies(self, value):
        result = []
        for item in self.object(value)[1:]:
            fields = self.object(item)
            result.append(dict(name=self.text(fields[1]),
                               version=self.text(fields[2]) if len(fields) > 2 else "",
                               match=self.integer(fields[3]) if len(fields) > 3 else 0))
        return result


def read_apk_index(raw):
    """\u8bfb\u53d6\u5305\u7248\u672c\u3001\u67b6\u6784\u3001\u5927\u5c0f\u548c\u4f9d\u8d56\uff0c\u4e0d\u64cd\u4f5c\u8def\u7531\u5668\u3002"""
    reader = AdbReader(raw)
    root = reader.object(int.from_bytes(reader.take(4, 4), "little"))
    rows = []
    for item in reader.object(root[2])[1:]:
        fields = reader.object(item)
        def field(index):
            return fields[index] if index < len(fields) else 0
        name, version = reader.text(field(1)), reader.text(field(2))
        rows.append(dict(name=name, version=version,
                         architecture=reader.text(field(5)),
                         size=reader.integer(field(13)),
                         filename=name + "-" + version + ".apk",
                         depends=reader.dependencies(field(15)),
                         provides=reader.dependencies(field(16)),
                         install_if=reader.dependencies(field(18))))
    if not rows:
        raise ValueError("Empty APK index")
    return rows
