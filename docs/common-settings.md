# Common router settings

The Common Settings tab follows Router Login. It is created on demand; opening it does not query the router. Cards open separate editors for login password, LAN configuration, and hostname/timezone. Relocation and orphan-interface review retain their existing preview/confirmation workflows.

## Device behavior

- Password: administrator access and BusyBox passwd are required. Two password lines travel through encrypted SSH stdin, never command arguments or application logs. A separate password-only login checks the pinned host fingerprint. The existing session stays connected. Confirmed changes update the current in-memory password and the user's existing Windows credential preference. An unconfirmed result is shown explicitly.
- LAN: reads and writes only the existing network.lan section. Saves without a network reload. A later network/router restart is required. The confirmation explains management-address changes and that DHCP address-pool settings are not edited. Existing legacy type/ifname bindings are preserved; changing that legacy binding requires the router web interface.
- Hostname/timezone: requires one unambiguous system section. Saves only that configuration, reloads the system service, and checks the runtime hostname and timezone file/link. This does not rename SSIDs.
- Timezone picker preserves the current rule and offers Beijing, UTC, and whole-hour fixed offsets. Fixed offsets do not implement daylight-saving transitions.
- Both configuration editors use an isolated rpcd UCI session, file-hash conflict detection, unique backups, readback verification, and conditional recovery. Pending edits in the target configuration block saving.
- Required configuration capabilities: administrator access, Lua, uci, ubus/rpcd, luci.jsonc and sha256sum. No automatic package installation occurs.

## Compatibility evidence

Official 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 branch sources were inspected for the common static-address options and system reload contract. The selected path depends on available capabilities and configuration shape, not a guessed version or interface number.

Primary source paths (replace BRANCH with openwrt-19.07, openwrt-21.02, openwrt-22.03, openwrt-23.05, openwrt-24.10 or openwrt-25.12):

- https://github.com/openwrt/openwrt/blob/BRANCH/package/base-files/files/etc/init.d/system
- https://github.com/openwrt/luci/blob/BRANCH/modules/luci-base/htdocs/luci-static/resources/protocol/static.js
- https://github.com/mirror/busybox/blob/master/loginutils/passwd.c
- https://github.com/mirror/busybox/blob/master/libbb/bb_askpass.c

Offline tests execute the real Lua transaction with simulated UCI/rpcd/filesystem data, including legacy ifname/type bindings and modern device bindings. Coverage includes preserving unrelated settings, stale reviews, backup/commit failures, missing wireless configuration, runtime reload failure, password transport and credential handoff, cancel, and connection switches. UI checks render light/dark themes at 100 and 150 percent.

These are source and offline checks, not live-router certification. Live password and configuration changes still require device verification.

## Backup, restore and factory reset

Two-column cards use matching color icons from the user's reference collection, already shipped under images/user_icons/icons_quick_settings. Wrapping and short buttons keep two columns usable in narrow windows. The common action helper clears focus before disabling a button, preventing focus from walking down through sibling cards and scrolling the page.

Export uses native sysupgrade -b, downloads to an owned local temporary directory, verifies SHA-256 and gzip/tar integrity, then publishes the local file. A damaged download never replaces an existing backup.

Restore previews up to 80 file names. The user confirms the backup belongs to this router and firmware and chooses a separate local safety-backup path. A verified current configuration backup must be saved there before sysupgrade -r starts. The inspected source is frozen before upload, and its hash and the device/configuration token are rechecked before writes. Pending UCI changes block maintenance.

Restore supports normal configuration files under etc and root, including safe service symlinks. Traversal, special files, duplicates, symlink write-through parents and links escaping that scope are rejected. Custom paths outside those trees require checking/restoring in the original management interface. Limits: 32 MiB compressed, 128 MiB decompressed, 8192 entries and 256 KiB of path names. Payload processing is chunked through temporary files.

Factory reset requires an observed squashfs/overlay configuration and an installed factoryreset or jffs2reset helper. A typed confirmation and verified local safety backup are required. Settings and later-installed packages are deleted. This does not install the manufacturer's stock firmware; configuration backups cannot restore all installed software or user files.

Once a destructive command starts, cancellation cannot interrupt it halfway. Bounded observation reports uncertainty explicitly without automatic retries. Restore/reset request reboot; the user reconnects using the resulting address and password. Uncertain restore workspaces are retained to avoid interrupting a command that may still be running.

The official 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 sources were checked for native -b/-r behavior. The native helper handles opkg/apk metadata differences. The 25.12 etc/uci-defaults service-state member is supported. Firstboot changed its helper from jffs2reset to factoryreset in 25.12; selection uses observed capabilities.

Primary references:

- https://github.com/openwrt/openwrt/blob/openwrt-19.07/package/base-files/files/sbin/sysupgrade
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/sbin/sysupgrade
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/lib/upgrade/tar.sh
- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/sbin/firstboot
- https://github.com/openwrt/fstools/blob/master/factoryreset.c
- https://github.com/openwrt/luci/blob/openwrt-25.12/modules/luci-mod-system/htdocs/luci-static/resources/view/system/flash.js

Tests are offline archive/command-adapter checks and UI renders. No live router backup, restore, reset or reboot was executed.
