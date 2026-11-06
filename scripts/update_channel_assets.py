"""Public build-time update defaults, independent of user configuration."""
from modules.app_update_services.channel import GATEWAY
from modules.app_update_services.release_targets import manifest_endpoint, validate_platform


def default_update_config(platform):
    platform = validate_platform(platform)
    return dict(schema=1, enabled=True, manifest_url=GATEWAY + manifest_endpoint(platform),
                network_mode="auto", github_mirror=True)
