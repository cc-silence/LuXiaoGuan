"""解析 OpenWrt 官方 Packages 索引。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageRecord:
    """单个 OpenWrt 软件包索引记录。"""

    name: str
    version: str
    architecture: str
    filename: str
    sha256: str
    size: int = 0
    installed_size: int = 0
    depends: tuple[str, ...] = field(default_factory=tuple)
    provides: tuple[str, ...] = field(default_factory=tuple)
    feed: str = ""
    base_url: str = ""

    @property
    def url(self):
        """返回软件包官方下载 URL。"""

        return self.base_url.rstrip("/") + "/" + self.filename


def parse_packages_index(text, feed, base_url):
    """解析 Debian control 格式 Packages 文本并返回 PackageRecord 元组。

    参数：text 是已解压索引；feed 和 base_url 标记来源。副作用：无。
    """

    records = []
    for block in str(text or "").replace("\r\n", "\n").split("\n\n"):
        fields = {}
        current_key = ""
        for raw_line in block.splitlines():
            if raw_line[:1].isspace() and current_key:
                fields[current_key] += " " + raw_line.strip()
                continue
            if ":" not in raw_line:
                continue
            current_key, value = raw_line.split(":", 1)
            current_key = current_key.strip()
            fields[current_key] = value.strip()
        if not fields.get("Package") or not fields.get("Filename"):
            continue
        records.append(
            PackageRecord(
                name=fields["Package"],
                version=fields.get("Version", ""),
                architecture=fields.get("Architecture", ""),
                filename=fields["Filename"],
                sha256=fields.get("SHA256sum", "").lower(),
                size=_integer(fields.get("Size")),
                installed_size=_integer(fields.get("Installed-Size")),
                depends=_split_field(fields.get("Depends")),
                provides=_split_field(fields.get("Provides")),
                feed=str(feed),
                base_url=str(base_url),
            )
        )
    return tuple(records)


def _split_field(value):
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
