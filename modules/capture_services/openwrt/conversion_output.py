"""Bounded Hashcat output; byte identities are never decoded for serialization."""

def ssid_octets(value):
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        raise ValueError("SSID must be text or original bytes.")
    return value.encode("utf-8", errors="strict")


def collect_hash_lines(records, check, *, max_records=20000, max_bytes=8 * 1024 * 1024):
    seen = {}
    total = 0
    for line in records:
        check()
        fields = line.split("*")
        if fields[1] == "02" and not 99 <= len(bytes.fromhex(fields[7])) <= 256:
            continue
        if line in seen:
            continue
        total += len(line) + 1
        if len(seen) >= max_records or total > max_bytes:
            raise RuntimeError("Capture conversion output limit exceeded; original preserved.")
        seen[line] = None
    return list(seen)
