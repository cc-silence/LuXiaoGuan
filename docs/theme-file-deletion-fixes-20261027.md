# Theme and file deletion fixes (2026-10-27)

## Root causes and behavior

Default theme labels and unavailable/empty theme files previously produced an empty main theme, while the window still loaded its dark chrome fragment. Navigation and scrollbars then fell through to native platform drawing. build_main_stylesheet now falls back to the complete built-in theme at the shared shell entry. A valid custom theme remains intact. Full built-in resources missing or empty cause an explicit error rather than a partly styled window. This repairs the reproduced path consistent with the reported sidebar and scrollbar screenshots; the affected Win11 binary/configuration has not been inspected.

Remote multi-selection already used a shared selection model and file_batch. The actual missing capability was recursive directory deletion: rmdir only accepts an empty directory. RouterManagement.mutate now delegates deletion to router_file_delete; single and multi-item remote deletion use the same cancellable background batch UI. Toolbar, context menu and Delete shortcut converge on this path.

Deletion scans the complete bounded tree before its first mutation, then unlinks files/links and removes emptied directories from the bottom up. It checks selected-target metadata, ancestor directory types, detected metadata changes, connection instance and host fingerprint, cancellation and a 120-second task deadline. SFTP calls retain a 10-second channel timeout. Per selected tree the inventory includes at most 10000 entries including its root, and depth is at most 64. Directory metadata is refreshed after this task removes a child. No shell command, rm option, radio/platform mapping or version-specific router command is introduced. Symbolic links are unlinked, never intentionally traversed; links in parent paths are rejected. SFTP has no portable inode-bound recursive transaction, so these checks cannot make deletion atomic against arbitrary concurrent remote filesystem changes. Permission failures, new children or detectable target changes stop deletion and may leave a partial result; already deleted data is not restored.

An interrupted/failed scan resets only the original cached SFTP session under the transaction lock, releasing outstanding server enumeration handles and preserving SSH login. Successful tasks reuse the existing session. Inventory memory is bounded and task-local; progress signals are throttled by elapsed time, with no refresh timer or permanent file cache.

The deletion icon now uses a trash-can SVG with the existing toolbar line weight, size and light/dark color handling. Tooltip text includes selection count, Ctrl/Shift/Delete usage and the difference between remote permanent deletion and local recycle-bin deletion. Confirmation explicitly includes all directory contents and irreversibility. Cancelled batches no longer finish the progress bar as a success. Remote partial deletion and local recoverable recycle-bin results have distinct messages. Both success and partial batch results refresh the current directory only while still on the original connection. Existing local single-item recycle behavior remains intact.

## Verification

- Main theme fallback: rendered navigation and scrollbars for default, missing and empty themes match a full built-in dark theme at 100/125/150/200 percent; valid custom theme preservation is checked.
- In-memory SFTP regression covers nested directories, files, links, empty directories, multiple selected directories, invalid/root/linked parent paths, metadata changes, new concurrent children, permission failure, cancellation before/during deletion, deadline limits, connection/fingerprint changes and SFTP cleanup. No real file is deleted by these tests.
- Actual file widgets cover Ctrl/Shift selection, shared icon/list selection, toolbar confirmation and cancellation, multiple delete jobs, one-item cancellable deletion, partial-result refresh, local recycle-bin wording and unchanged multiple-download execution.
- Related external drag/drop, main-window theme and chrome regressions were run with the application button-focus policy enabled: 41 tests passed before the final boundary additions. After final additions and the local/remote cancellation-message correction, all 26 targeted deletion and file-widget regressions passed. Across these runs, 44 distinct relevant tests passed (23 earlier deletion/file tests were rerun within the final 26).
- Actual dark/default and light file-page screenshots were reviewed at build/review/theme-file-delete/default-dark.png and light.png. They are offline fixtures, not a connected router. The release layout copies the images directory, so the new SVG is included by the established flow. No build/publication was requested or performed.

Windows checks use local Qt 6.3.0/offscreen rendering. Debian 12 desktop execution and the affected user's Win11 screen remain unverified. Desktop code and SFTP operations are shared across the two systems, with no new OS-specific import or API. Before release, check first launch without a theme toggle, both themes, scrollbars and Delete/Ctrl/Shift on the actual desktops. Router acceptance should use a disposable directory containing nested folders, ordinary files and a link to a separate retained sentinel; confirm the full tree is removed while the link target remains. Do not test against system/user working directories. No router was connected or modified in this task.

## Compatibility evidence

The change reuses standard SFTP lstat/READDIR/REMOVE/RMDIR operations already required by file management. Paramiko documents lstat as inspecting the link itself, and rmdir as empty-directory removal: https://docs.paramiko.org/en/stable/api/sftp.html . OpenWrt documents Dropbear plus openssh-sftp-server as the SFTP service: https://openwrt.org/docs/guide-user/services/nas/sftp.server . Service presence and permissions remain runtime requirements; no package is installed by this change.

Official package sources were checked on 2026-10-27. Each of these release branches defines/installs openssh-sftp-server; this confirms the service packaging path, not every device or exact firmware image:

- 19.07: https://raw.githubusercontent.com/openwrt/packages/openwrt-19.07/net/openssh/Makefile
- 21.02: https://raw.githubusercontent.com/openwrt/packages/openwrt-21.02/net/openssh/Makefile
- 22.03: https://raw.githubusercontent.com/openwrt/packages/openwrt-22.03/net/openssh/Makefile
- 23.05: https://raw.githubusercontent.com/openwrt/packages/openwrt-23.05/net/openssh/Makefile
- 24.10: https://raw.githubusercontent.com/openwrt/packages/openwrt-24.10/net/openssh/Makefile
- 25.12: https://raw.githubusercontent.com/openwrt/packages/openwrt-25.12/net/openssh/Makefile

The client protocol does not branch on CPU, target, wireless chipset, package manager, swconfig/DSA or firewall3/4. Thus mediatek/filogic, mediatek/mt7622, ramips/mt7621, ipq40xx/generic, ipq806x/generic, ipq807x/generic, qualcommax/ipq807x, ath79/generic, ath79/nand, bcm53xx/generic, bcm27xx/bcm2711, mvebu/cortexa9, mvebu/cortexa53, sunxi/cortexa53 and x86/64 use the same service path when available. Xiaomi AX3600, ASUS RT-ACRH17, Redmi AX6S and JCG Q30 Pro have no model-specific code introduced here and have not been tested live. JCG Q30 remains unverified and distinct from Q30 Pro. Package support inventory is unchanged; no firmware/target combination or device is newly asserted as verified.

Audit follow-up (2026-10-27): Batch audit additionally fixed connection changes during a shared file confirmation and double-leading-slash root aliases in the common remote path normalizer; real Paramiko protocol tests were added. See feedback-batch-audit-20261027.md.
