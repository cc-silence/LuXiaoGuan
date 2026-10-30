# Relocation: retain and rename access points (2026-10-30)

## User flow and transaction

1. Read-only preview separates upstream STA entries from downstream AP hotspots.
2. The first dialog lists each old hotspot name and an editable random replacement,
   with individual/all random buttons. Each AP address is generated automatically
   and is hidden from editing. Cancel leaves the router untouched.
3. The final confirmation lists upstream removal and old/new AP names. Connection
   identity is rechecked after both dialogs. Only accepted values enter the existing
   background service and rpcd rollback transaction.

AP entries, passwords, encryption, networks, hidden state, interface switches and
radio switches are retained. Only their SSID and MAC are changed. Retained wireless
networks are protected from upstream cleanup, including shared network references.
Other wireless modes are retained. Cleanup removes STA entries and their removable
upstream/relay networks, with the existing DHCP restoration and reference cleanup.

Successful completion now requires retained enabled APs to run with the requested
SSID and actual kernel MAC, through the common ap_ready/runtime_mac verifier.
Disabled APs stay disabled. A status mismatch, cancellation or failed reconnect
uses the existing five-config rollback and pinned-device reconnect path.
The transaction is applied once, never retried as a write.

Changing a connected AP's SSID/MAC can interrupt Wi-Fi. Users must reconnect to a
displayed new name using the original password. The UI explicitly explains the
90-second rollback window; it does not promise uninterrupted Wi-Fi or configure
the computer's wireless credentials.

## Shared components and bounds

AccessPointBatchDialog remains the common initial-safety/relocation editor.
Its optional title, description and continue_text customize the flow.
mac_editable defaults to True for existing callers; False hides the generated
MAC field and removes manual editing for relocation. Existing initial-safety
behavior remains covered by its original regressions.
relocation_access_points adapts the password-free preview into the existing
ap_batch_identity generator/validator, so names, UTF-8 byte limits and MAC collision
rules share one implementation. Preview/plan lists are bounded to 256 entries.
No extra timers, cache, worker pool or platform-specific branch was added.

## Compatibility evidence and limits

This changes router execution and therefore the OpenWrt compatibility rule applies.
On 2026-10-30, 78 cached official source files were reread and SHA-256 checked:
50 exact-release hostapd implementations, 14 fixed rpcd uci implementations,
10 fixed netifd wireless implementations, and four new wireless-script/schema
files. Version URLs and per-file hashes are recorded in the existing manifest.json,
pinned-manifest.json and supplement-wireless-manifest.json under
build/compatibility-audit-20261029/sources/. This run's matching-source index:
build/relocation-ap-source-check-20261030.json (SHA-256 e47c982135891004aae212d3bb754bb2d60b84c0d7507e5f93f8381e3cd7b125).

The 50 release fixtures cover supported 19.07/21.02/22.03/23.05/24.10/25.12
hostapd response differences. Existing ap_ready checks are reused, including
SSID cross-checks for early get_status implementations without an SSID field.
The device-reported SSID/MAC option support must be confirmed before preview;
lack of capability blocks relocation rather than omitting AP changes.
Runtime verification still gates driver acceptance of the generated BSSID.

No model or radio-number assumptions were added. Hardware support depends on the
actual driver, available rpcd/Lua components and rollback capability. This source
review and offline execution do not claim validation on every supported model,
nor on JCG Q30 (distinct from Q30 Pro). Real-router Wi-Fi reconnection and driver
MAC acceptance remain to be tested on authorized hardware.

## Validation

- 118 targeted regressions passed: relocation, cleanup, JSON/Lua contracts,
  reconnect/rollback, dialog flow, existing initial AP editing, version fixtures,
  signal foreground handoff and management logs.
- New tests execute the production Lua for mixed AP/STA cleanup, Unicode SSIDs,
  disabled/hidden AP preservation, shared networks, address conflicts, capability
  rejection and exact rollback when runtime SSID/MAC/AP presence is wrong.
- Native Windows Qt checks use the production relocation dialog factory in both
  themes and 100/150 percent scale. Images are under
  build/review/relocation-preserve-hotspots/.
- Syntax/UTF-8 and scoped diff checks are run for the changed files.

Windows and Debian 12 share this Python/Qt implementation. Debian 12 runtime and
live-router acceptance remain unverified; no Linux device test is implied.

## Optional discard choice (2026-10-30)

The first dialog additionally offers "Do not retain; clean directly".
AccessPointBatchDialog's discard_text parameter adds this button only for callers
that request it. It returns Discarded (2), separately from Accepted/Rejected and
without validating irrelevant rename fields. Initial-safety callers are unchanged.

The discard choice does not submit a write. It requests a new read-only preview
with preserve_access_points=False. That preview includes APs as deletion targets
and recomputes removable networks; it reaches the existing final confirmation
without reopening the rename editor. The UI checks the returned mode against the
user's choice and rechecks connection identity. Final cancellation does nothing.

The actual transaction includes the selected mode and the exact approved wireless
deletion rows. The router recomputes and compares those rows before making changes,
so a retention preview cannot simply be relabelled as a deletion request. Drop
mode verifies that neither STA nor AP configurations remain, and rejects live
interfaces left behind after deletion. The same mode is used for rollback checks.
Passwords and names are restored on rollback; LAN address and login are retained.

The deletion confirmation explicitly warns that removing the hotspot disconnects
Wi-Fi administration and asks the user to connect by Ethernet before proceeding.
Existing rollback/reconnect protection and single-write execution remain intact.
No new remote API, timer or platform branch was added. The 78 fixed official source
files cited above were re-read and hash-verified for this addition.

Additional regressions cover read-only discard previews, actual hotspot/network
removal, renamed-preview tampering, exact failure restoration, omitted AP identity
capability, bypass of irrelevant name validation, the second confirmation, and
absence of the button in existing initial-safety editing. Native Windows checks
also verify button visibility, label width and non-overlap at 100/150 percent.
Live-router and Debian 12 validation are still pending.
