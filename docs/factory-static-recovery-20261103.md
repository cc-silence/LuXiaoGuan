# Factory-reset static client recovery

## Shared implementation
FactoryResetResultDialog passes the firmware defaults to factory_reset_plan.
The factory planner translates proven pre-reset SSH adapter inventory and firmware subnet
metadata to the ordinary client_plan and migration_plan. No independent address-selection,
gateway/DNS rewriting, OS command runner, journal or CAS restore implementation is added.
LanRecoveryService dispatches an explicitly claimed static offer through StaticMigration.
The existing DHCP renewal path is unchanged. Static recovery never enables DHCP.

The user confirms reboot and physical/Wi-Fi connection using the existing checkbox, then
clicks the existing action (label: adjust fixed IP). The same panel handles both modes.
A unique SSH source adapter, usable firmware address/mask from ROM or board metadata and
nonconflicting host-number mapping are required. A generic standard-default guess cannot
authorize a static write. Missing/ambiguous inputs leave manual guidance instead.

## Boundaries
This is a PC-only transaction after reset, not a second reset or a router configuration
application. commit_local shares CAS writes and final-address selection with ordinary
commit, but records local_confirmed rather than router_confirmed. The UI reports only
verified PC settings and still requires router login. It does not trust changed SSH keys.
Gateway/DNS entries equal to the old router are replaced; unrelated entries are kept.
On write/authorization/cancellation failure, compare-and-restore preserves external edits.
One overall deadline covers forward operations; compensation has its existing bounded time.
Verified leftover local journals can be reconciled by the next ordinary LAN migration.

## Firmware metadata
ROM supports legacy address plus netmask and current CIDR address forms. Conflicting masks
and multiple addresses are rejected. Board metadata follows the official generator mask
default. The 50 supported exact config_generate files and SHA-256 values were inspected;
see factory-static-sources-20261103.json and build/factory-static-sources-20261103.
Only read-only metadata probing changed; reset commands and service semantics are unchanged.
This code does not depend on radio numbering, CPU architecture or a router model mapping.

## Validation
Offline tests cover planning/ambiguity/collision/overlap, DHCP preservation, static CAS
success, partial-write compensation, cancellation, external edits, interrupted journal
cleanup followed by ordinary LAN migration, real Qt worker dispatch and reboot-checkbox
gating. Existing LAN, initialization and static Windows/Linux driver regressions also run.
A light-theme render was inspected. No real adapter or router was changed for these tests.
Debian 12 live NetworkManager and actual Windows factory-reset/static-IP tests remain due.
A new release ZIP has not been built for this change.

## Follow-up audit
A persistent journal unlink failure is now covered: after compensation verifies the
original live PC state, save that restored state and clear the local-completion flag
before attempting receipt deletion. A later operation can reconcile the surviving
verified restored record. Static failures name the actual fixed-IP action button.
Native PowerShell return-code test cases each construct a fresh normal request
deadline, so earlier cases cannot expire a later case on a busy machine.

## Live topology and close audit
The shared transaction now rechecks other active adapters before preparation and
each forward write. Windows uses the existing native inventory reader; Linux uses
one bounded ip -j -4 address show query instead of per-adapter NetworkManager calls.
A new Wi-Fi/VPN overlap after preview stops before mutation, or compensates partial
preparation. Compensation itself does not use the forward overlap guard.
The Qt worker close-during-alias-preparation test confirms permission revocation,
original-state restoration, journal cleanup and job/resource release.
This adds no remote router operations, polling timer or visual changes.

## Additional boundary audit
Additional offline cases cover same-IP prefix-only changes without duplicate alias
creation, journal storage failure before mutation, malformed/foreign journal
preservation, external gateway changes after alias preparation and expired/cancelled
requests before driver construction. No production-code change was required by these cases.
