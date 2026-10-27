# Policy recovery-copy lifecycle (2026-10-27)

## Change

Successful policy file writes previously retained every /root/luxiaoguan-policy.* directory, even after the entire operation was verified. The 20-directory guard eventually blocked another file-changing policy operation. Recovery copies now belong to a single task and are cleaned only after its verified terminal state.

- Full application success: files, configuration and requested service/boot status finish verification before cleanup runs.
- Failed application: restore the task files, require an explicit restored response, then re-read the configuration hashes against the task baseline. Cleanup is allowed only when that check and the remote original-file hash checks pass.
- Failure inside the Lua file writer: initialize a complete journal before mutation, restore any written files, then clean only if original-file hashes are verified. Metadata/backup-copy failures are handled without silently leaving a normal-success directory.
- Commit/application uncertainty, disconnection, unverified recovery or concurrent configuration changes: retain recovery data and report its directory. The caller does not retry configuration application to resolve a cleanup problem.
- Cleanup failure/lost response: show that cleanup could not be confirmed and give the exact task directory. A verified settings operation stays successful; its note records the cleanup warning. The user is not told that a directory definitely exists if the cleanup reply was lost.

The normal-success notes no longer advertise a recovery location that has been removed. A user-selected whole-router backup export is separate and remains untouched.

## Cleanup boundary and resource use

New task directories contain an owner marker with the same random 16-hex-digit token already used for that task's temporary writes. The cleanup action requires the exact directory, token and terminal state. It rejects paths outside the fixed directory shape, directory/root/metadata/copy symlinks, foreign tokens, unowned legacy directories, malformed journals, duplicate or unknown target paths, changed live files and inconsistent applied-state backup hashes. It checks for unexpected immediate children before unlinking anything. Journal parsing remains bounded at 32768 bytes with at most three fixed file records; only the small marker, journal and known numbered copies can be removed. Finally Lua os.remove removes the empty directory. Unknown children introduced after preflight still prevent directory removal and are never recursively deleted.

No recursive rm, deletion glob, broad old-directory cleanup, timer, periodic scan or permanent in-memory directory cache is introduced. The pre-existing 20-directory guard remains a safety limit for exceptional/unresolved and legacy copies; successful new operations do not fill it. Existing router directories are not inspected or removed automatically. Power loss before a terminal state can still leave a recovery copy intentionally. Cleanup is bounded by a 10-second command timeout, and the existing background management queue, verified device/channel ownership and stdin transport for long Lua requests are retained.

## Verification

71 distinct related tests passed: 70 tests covering the new lifecycle tests, original policy tests, apply modes, policy audit, official source contracts and initial-settings feedback; one additional test exercises the cleanup lifecycle over all six official release fixtures. The final added test uses both legacy and current network layout fixtures. These are real Lua 5.1 scripts on an in-memory router, not live OpenWrt acceptance.

The lifecycle cases include 25 successive successful file operations with no accumulating task directories; old-directory and whole-router export preservation; all-known success/restore paths; internal copy/write failures; corrupt copies; wrong owner/path/journal; symlinks and extra children; uncertain transactions and boot status; external changes; disconnected/unreadable recovery verification; failed cleanup and a lost cleanup response. Faults never execute host deletion commands: Lua file operations are bridged into an in-memory filesystem. Python compilation, Lua 5.1 compilation, UTF-8/encoding checks and git diff --check passed.

No real router was connected or modified. No build or publication was performed. Windows local/offline tests passed; Debian 12 desktop execution and live router acceptance remain pending. There is no UI layout/style change.

## Compatibility evidence and acceptance boundary

Lua 5.1 documents os.remove as deleting a named file or an empty directory, with failure returned explicitly: https://www.lua.org/manual/5.1/manual.html#pdf-os.remove . Its reference implementation calls the C remove function: https://www.lua.org/source/5.1/loslib.c.html . Cleanup executes on the Linux router, not the Windows desktop. find -maxdepth, wc, shell test, sha256sum and the existing Lua/json/uci/ubus execution path were already required by this policy implementation; this change installs no package and adds no platform-specific desktop import.

Official OpenWrt source records and the existing policy contract fixtures cover 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12; see tests/fixtures/official_policy_contracts.json and docs/theme-file-deletion-fixes-20261027.md for the release-source links checked during this task sequence. No version-number, package-manager or hardware-specific cleanup branch is introduced. The cleanup path is independent of CPU, radio driver, swconfig/DSA and firewall3/4. Supported mediatek/filogic, mediatek/mt7622, ramips/mt7621, ipq40xx/generic, ipq806x/generic, ipq807x/generic, qualcommax/ipq807x, ath79/generic, ath79/nand, bcm53xx/generic, bcm27xx/bcm2711, mvebu/cortexa9, mvebu/cortexa53, sunxi/cortexa53 and x86/64 share this path when the existing management capabilities are present. Xiaomi AX3600, ASUS RT-ACRH17, Redmi AX6S and JCG Q30 Pro were not tested live; JCG Q30 remains separately unverified. Neither package inventory nor verified-model status changed.

Live acceptance should inventory existing policy directories first, apply one authorized reversible file-changing setting, verify the entire operation, and check that only its new task directory disappears while old directories and any exported whole-router backup remain. Perform fault/rollback tests only in a dedicated test environment; production recovery copies with uncertain status must be retained. Repeat the client flow from Windows and Debian 12; offline source/fixture coverage is not a claim of live compatibility.

Audit follow-up (2026-10-27): The subsequent full feedback audit rechecked this cleanup lifecycle with all current UI policies enabled. See feedback-batch-audit-20261027.md for final verification and the separate Debian branch integration limit.
