"""Shared public update defaults and bounded optional configuration loading."""
import json
from pathlib import Path
from .channel import GATEWAY
from .release_targets import manifest_endpoint, validate_platform


def default_update_config(platform):
    platform = validate_platform(platform)
    return dict(schema=1, enabled=True, manifest_url=GATEWAY + manifest_endpoint(platform),
                network_mode="auto", github_mirror=True)


def load_update_config(path, platform):
    defaults = default_update_config(platform)
    path = Path(path)
    try:
        with path.open("rb") as source:
            raw = source.read(4097)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("Linked update configuration is unavailable") from None
        return defaults
    if len(raw) > 4096:
        raise ValueError("Update channel configuration is too large")
    config = json.loads(raw.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Invalid update channel configuration")
    if config.get("network_mode", "auto") not in {"auto", "proxy", "direct"}:
        raise ValueError("Invalid update network mode")
    return config
