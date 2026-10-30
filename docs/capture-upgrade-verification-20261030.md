# Capture tool upgrade and cleanup verification (2026-10-30)

The user authorized real-device dependency installation and approved reuse of the existing progress dialog. No commits, publishing, firmware changes or wireless takeover were performed.

## Confirmed causes and changes

- The one-click upgrade used install_all(), whose missing-package collector intentionally excludes UPGRADABLE dependencies. The real device therefore returned "no installation needed" while hcxdumptool remained 6.2.4. Explicit hcx upgrades now have a separate service entry and verify READY after installation. Login and ordinary missing-dependency installation keep their existing scope.
- The dedicated primary installer previously rejected an unknown/unavailable SFTP flag before attempting transfer. It now uses the existing shared package uploader (SFTP or bounded command-channel chunks) with the same local/upload hash checks and shared-library verification.
- The auto-capture button had no progress subscription. A session-bound controller now displays the shared progress dialog, focuses that same dialog on repeated clicks, cancels only its own session, and shows a final failure. Page disposal disconnects subscriptions.
- Early capture failure still logged deletion of a monitor interface that had not been created. Error state also suppressed the successful-cleanup final log. Cleanup now skips uncreated resources, checks interface type and exact phy before removal, verifies absence, reports an explicit outcome even after capture failure, and preserves failed recovery records. A failed pipeline remains stopping until cleanup finishes; repeated start is blocked during stopping.
- The existing single-item dependency upgrade entry uses the same explicit upgrade service.

## Real-device evidence

The saved credentials were read only through the application's credential API, and the exact saved host fingerprint was required. Passwords and device addresses were not saved in this report.

Device: OpenWrt 23.05.4, mediatek/filogic, aarch64_cortex-a53, kernel 5.15.162.

- Before: hcxdumptool 6.2.4-1; PMKID state UPGRADABLE.
- After: hcxdumptool 6.3.4-r1; PMKID state READY.
- The existing dedicated primary installer used the 24.10 hcx-only package from tools/hc_offline_pack. Only hcxdumptool changed in the installed-package inventory; shared libraries were preserved. This verifies this device's installation and tool capability probe, not a general cross-release or radio-driver compatibility claim.
- Wireless configuration hash unchanged.
- Seven command-channel upload chunks completed and their resulting file hash was verified.
- The real service was then exercised with the production QThread completion path and an offscreen progress dialog: first click accepted and progress visible, second click reused the dialog, exactly one worker, task and progress references released after success, owner no longer busy.
- hcxmon1 was absent on the initial read-only check and was also confirmed absent using the new cleanup probe. No live monitor interface was created or deleted for this verification.
- 24 sequential short commands over a fresh verified connection succeeded.

Machine-readable records: build/hcx-upgrade-audit-20261030/live-result.json and live-service-result.json.

## Offline and visual validation

80 targeted tests passed, including upgrade feedback, upgrade verification, primary installer, auto capture, cleanup, dependency groups and login installation. Expected simulated-error tracebacks appear in test output. Syntax compilation and UTF-8 replacement-character checks passed for all changed Python files. Shared progress and failure dialogs were rendered and inspected in both themes with an explicitly loaded installed CJK font; no clipping was found.

The desktop automation helper could not start (Node kernel exit / sandbox ACL initialization failure), so the currently open desktop window was not clicked or forcibly terminated. Real-device service verification and offscreen Qt checks do not constitute an end-to-end desktop click test. The running old desktop process still needs a normal restart to load the source changes; the router package upgrade is already applied.

Debian 12 was not available and has not been validated. The new business logic has no platform-specific branches.

## Remaining uncertainty

The original application logs contain SSHException: Channel closed during exec-request confirmation, including a mkdir request before any monitor was created. That error was not reproduced on fresh verified connections and its originating shared-connection/server condition is not proven fixed. No blind command retry, timer delay, TLS bypass, automatic host-key acceptance or automatic re-login was introduced. Full live capture and wireless takeover/restore were not tested.

## Source review

Existing pinned OpenWrt/opkg source evidence was read under build/review/login-dependencies/official-source-review-20261029, including native argument handling for noaction, nodeps, force-reinstall and force-downgrade. The dedicated installer command policy was retained.

OpenWrt v23.05.4's exact iw Makefile was fetched and identifies iw 5.19. Its fixed v5.19 interface.c was read from the official kernel Git mirror, confirming type/wiphy info output and NL80211_CMD_DEL_INTERFACE for deletion. Source URLs, hashes and date are recorded in build/hcx-upgrade-audit-20261030/sources/source-index.json. These checks cover the changed command path on the current device; they are not an all-model or all-release live verification.
