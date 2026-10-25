"""Four application publication slots; Windows keeps its shipped channel keys."""
PLATFORMS = frozenset({"win-x64", "linux-x64"})
APPLICATION_CHANNELS = {
    "channel/stable.json": ("win-x64", "stable"),
    "channel/manifest.json": ("win-x64", "alpha"),
    "channel/linux-x64/stable.json": ("linux-x64", "stable"),
    "channel/linux-x64/alpha.json": ("linux-x64", "alpha"),
}


def validate_platform(value):
    if value not in PLATFORMS:
        raise ValueError("Unsupported release platform")
    return value


def release_channel(version, *, legacy=False):
    from .manifest import version_key
    stage = version_key(version)[1]
    if stage == 4:
        return "stable"
    if stage == 1 or legacy and stage < 4:
        return "alpha"
    raise ValueError("Only formal and alpha application releases may be published")


def channel_key(platform, channel):
    validate_platform(platform)
    for key, identity in APPLICATION_CHANNELS.items():
        if identity == (platform, channel):
            return key
    raise ValueError("Unsupported release channel")


def manifest_endpoint(platform):
    validate_platform(platform)
    return "/manifest" if platform == "win-x64" else "/manifest?platform=" + platform


def archive_key(platform, digest, name):
    import re
    validate_platform(platform)
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("Invalid archive fingerprint")
    if not re.fullmatch(r"(?:RouterManager|LuXiaoGuan)_v[0-9A-Za-z._-]+\.zip", name):
        raise ValueError("Invalid archive name")
    prefix = "files/" if platform == "win-x64" else "files/" + platform + "/"
    return prefix + digest + "/" + name
