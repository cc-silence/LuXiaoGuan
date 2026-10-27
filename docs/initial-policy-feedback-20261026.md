# Initial security setup feedback - 2026-10-26

## Evidence from the supplied archive

Archive: the supplied LuXiaoGuan ZIP ending in _20261026_145115.zip. operation-diagnostics.json records two initial policy failures at commit with reason save_commit, task IDs 1410 and 1566. In task 1566, the replacement AP commit and readiness check completed before files_apply, the second session/stage/commit and the save failure. SSH stayed connected; command exit codes were zero because the Lua protocol returns its own structured result. This establishes the failing stage, not every internal verification branch. The archive does not retain uhttpd configuration rows, so it cannot establish whether the second screenshot came from absent or multiple server sections.

## Root cause reproduced and changes

Official libuci generates anonymous names using a counter and a hash of section type/options. Modifying options or changing order can therefore change names after save/reload. The old content-map comparison and firewall-order check treated these transient names as stable IDs. An offline fixture which regenerates anonymous names reproduces save_commit with the old comparison. The corrected code runs the real Lua transaction against that fixture, comparing ordered full section contents while retaining names for explicitly named sections. Reviewed anonymous sections carry checked position/type references for post-save and post-apply validation. Runtime wireless selection is remapped through those references. The common hostname/timezone save path uses the same resolver. No anonymous section is renamed in the router configuration to work around the problem.

uhttpd selection now identifies the unique enabled standard /www server with listeners, rather than demanding one uhttpd section in the whole file. Unrelated document roots and disabled instances do not become initialization targets. HTTP-only switching, homepage and management-port firewall rules use the same selected instance. Absent or ambiguous management servers are listed as skipped for affected choices; other reviewed settings can proceed. Unsupported/custom web implementations are not overwritten or silently claimed as configured.

Station identity changes are LAN-management operations. Existing live LAN SSH is preserved. If the old connection was actually lost after a verified successful station operation, its independently authenticated verification connection can be moved into the shared client. This requires the original public key, unchanged endpoint/user and unchanged connection-intent generation; a user disconnect, new login or changed host is never overridden. The temporary verification channel cannot close the transferred transport. Main-thread state publication returns the software to connected state. A physical wireless interruption can still occur when station and local AP share a radio; this is separate from deliberately logging the user out. This assumption is recorded in AGENTS.md.

The replacement-hotspot checkbox now defaults off. Both hotspot input rows are hidden until checked; toggling keeps the entered values. The original confirmed-default-AP replacement safety rules still apply. Maintenance results now use the shared modal MessageDialog in addition to the information window. Restore notices distinguish command completion/reboot request from observed boot completion and explain waiting several minutes with power maintained, then using restored management credentials.

## Validation and limits

202 targeted regressions passed, covering the new failure cases, policy/apply workers, actual Lua commands, shared save-only paths, hostname/timezone, LAN, wireless setup, orphan cleanup, local SSH authentication and backup/restore. The new tests include a control which demonstrates the old comparator failure, anonymous renumbering through native apply, multi/absent web servers, handoff ownership, rejection of user-disconnected/replaced sessions, connected-state publication, optional hotspot fields and maintenance dialogs. Initial editors were rendered in light/dark themes at 100/150 percent with the option both off and on, plus a scrolled view of the inputs.

No live router configuration, reboot, reset or local network mutation was performed. Debian 12 desktop execution and real-router acceptance remain pending. Data/reference logic and Paramiko transport ownership are shared by Windows and Debian; there is no new CPU/driver-specific dependency. This does not certify every router or wireless driver. Official uhttpd configuration sources were checked for the supported 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 branches (24.10 init and existing source contracts also reviewed).

## Official sources

- libuci anonymous-name generation: https://github.com/openwrt/uci/blob/master/list.c
- uhttpd standard configuration (19.07): https://raw.githubusercontent.com/openwrt/openwrt/openwrt-19.07/package/network/services/uhttpd/files/uhttpd.config
- 21.02: https://raw.githubusercontent.com/openwrt/openwrt/openwrt-21.02/package/network/services/uhttpd/files/uhttpd.config
- 22.03: https://raw.githubusercontent.com/openwrt/openwrt/openwrt-22.03/package/network/services/uhttpd/files/uhttpd.config
- 23.05: https://raw.githubusercontent.com/openwrt/openwrt/openwrt-23.05/package/network/services/uhttpd/files/uhttpd.config
- 24.10 init: https://raw.githubusercontent.com/openwrt/openwrt/openwrt-24.10/package/network/services/uhttpd/files/uhttpd.init
- 25.12: https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/network/services/uhttpd/files/uhttpd.config
