# Independent application naming audit - 2026-10-21

## Corrected user-visible leftovers

- New exported logs use LuXiaoGuan_<category>_<time>.zip through the shared exporter.
  This covers information panels and router terminal exports.
- Archive README and new error-log headers identify LuXiaoGuan. Default information
  panel category is application rather than the previous product name.
- Router login, SSH fingerprint, capture takeover, signal-management, dependencies,
  missing-tool and terminal messages use the current Chinese display name.
- Global settings include desktop shortcut creation. The unused inherited creator
  that would produce WinBox.lnk was removed; Explorer navigation helpers remain.
- The online-update title uses the display name and no longer claims the already
  deployed independent update service is awaiting configuration.
- New default capture/download folders use the current display name. Explicit old
  output folders remain unchanged and are not nested inside a new folder.
- Capture error logger and offline-feed request identification use the independent
  application naming.

## Intentionally retained compatibility identifiers

- RouterManager configuration, credentials, single-instance, firewall ownership,
  signing/product identity, data directories and Cloudflare deployment stay stable.
- Existing capture directories and user settings are not moved or rewritten.
- Router recovery/backup locations, remote process markers and watchdog strings
  retain their current protocol names, including /tmp/winbox-capture. Renaming only
  one side would risk losing cleanup and recovery of existing sessions.
- .winbox-update is an internal per-installation transaction/recovery directory,
  not a connection to the previous application's update service.
- Shared widget properties and MIME identifiers retain their protocol names.
- Descriptions explicitly referring to old WinBox HCCAPX formats remain historical
  compatibility documentation, not the current product name.
- Unused inherited software-management source is outside the active router UI;
  this audit does not delete it or change the original WinBox project.

## Delivery boundary

Changes are source-only. Existing installed/extracted executables remain their
current versions until a new package is explicitly requested. No package, router
operation, original WinBox modification or public release is performed here.
