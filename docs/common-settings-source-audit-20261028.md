# Common-settings source-semantic audit, 2026-10-28

This follow-up checks implementation semantics against official sources instead of repeating the previous whole-window review. No new confirmed defect was found; no execution code was changed in this round.

22 official source files were fetched and retained under build/review/common-settings-source-audit-20261028: pinned rpcd uci.c and uci.h for 19.07/21.02/22.03/23.05/24.10/25.12, LuCI ui.js for those six series, plus the old controller/admin/uci.lua and model/uci.lua paths for 19.07 and 21.02. All twelve pinned rpcd source/header hashes matched the existing version contracts. Exact URLs and hashes are in sources.json; summary.json records the results.

## Findings verified against source

- Native rpcd commit emits the config.change service event. Therefore a committed network update already requests the registered service update; adding another unconditional network reload after reconnect would risk a second disconnect. Current code intentionally avoids it.
- The rpcd apply path accepts a rollback boolean. Checked apply uses rollback=true with a 90-second timeout and requires verification/confirmation. Native timeout invokes rollback; confirm with no active apply returns NO_DATA, so the current code re-verifies desired saved/runtime state rather than equating NO_DATA with success.
- Forced LAN submission commits only the selected network package, verifies the saved values and requests unchecked application. NO_DATA after a completed commit can mean there are no remaining pending deltas; it does not by itself prove the commit failed. The saved-data check and reconnect/uncertain wording remain necessary. Lost write replies are not retried.
- LuCI's old implementation has a controller apply_unchecked route and a model apply(false) path. New UI source syntax differs, but a different text marker does not mean a feature is missing. The initial keyword-only search was inconclusive on some old files; manual inspection of the controller/model path resolved that, without changing compatibility scope.
- Native sysupgrade -b/-r are configuration backup/restore operations, separate from flashing a firmware image. Current restore handling retains frozen source bytes, archive/path/hash checks and explicit irreversible-phase handling. No firmware force-flash flag was added.

Primary sources include:
- https://raw.githubusercontent.com/openwrt/luci/openwrt-19.07/modules/luci-base/luasrc/model/uci.lua
- https://raw.githubusercontent.com/openwrt/luci/openwrt-19.07/modules/luci-base/luasrc/controller/admin/uci.lua
- https://raw.githubusercontent.com/openwrt/luci/openwrt-25.12/modules/luci-base/htdocs/luci-static/resources/ui.js
- https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/base-files/files/sbin/sysupgrade

The rpcd source used for per-version conclusions is pinned to each supported firmware contract, rather than inferred from current master. The full URL set is in the retained sources manifest.

## Verification and scope

136 targeted tests passed in 33.170 seconds. Coverage: forced apply consent and native Lua execution on both network layouts, checked apply/recovery, file backup cleanup, native shell maintenance contracts across six series, archive restore, firmware capability failures, reset result/recovery, actual restore result-to-UI routing and initial input validation. git diff --check passed. These are newly run tests; the prior 426-case full-window result is separate and was not added to this count.

This round adds no platform/CPU binary, radio/driver change, storage reset path or package-manager behavior. Platform/model coverage and official target references remain as documented in common-settings-full-audit-20261028.md, including AX3600, RT-ACRH17, AX6S and Q30 Pro; Q30 is not equated with Q30 Pro. Exact dependency coverage remains docs/offline-packages/support.json. No version/model Cartesian product is inferred and no new hardware acceptance is claimed.

Remaining acceptance: Win11 Microsoft IME on the real system, actual router LAN/force/restore/reset/relocation behavior and hardware-specific radio/storage behavior. The Windows Qt/PySide6 6.3.2 pin remains incompatible with Debian 12's default Python 3.11; separate Debian runtime adaptation/acceptance is still outstanding. No router/local adapter operation or package build/publication was performed.
