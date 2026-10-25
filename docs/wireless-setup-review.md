# Wireless setup review - 2026-10-21

## Delivery boundary

1.0.3_alpha was published and independently downloaded/verified before wireless development continued. It contains the improved failure diagnostics, dependency source logs and local bundle version/update detection. Its ZIP SHA256 is 861404510f4dab6ddec5c9abe489c7e5bde869e8e94c465d020da01e14e9dcf0.

The subsequent wireless work was packaged and published as 1.0.4_alpha, with the public ZIP, signed alpha manifest and catalog verified. Final ZIP SHA256: 375e1db28225740f8dfbc01e61aa701878ea4c87464fb74f71bfa42ac0c76013. Delivery evidence: build/wireless-review/delivery.json. No real router was connected or modified during this work. All device execution evidence below is offline, including execution of the actual device-side scripts in Lua 5.1 with simulated UCI/ubus services.

## Implemented paths

- AP creation/editing: SSID and password visible; all additional groups collapsed. Existing passwords are not read back. Explicit edits preserve unrelated device values and existing multi-network bindings.
- Common settings: actual channels, width modes, country and power lists; personal WPA/WPA2/WPA3, OWE where advertised, current cipher suffixes, visibility, network, isolation, association limit, WMM, management-frame protection, DTIM and beacon interval.
- Advanced settings share a typed service/UI whitelist: MAC access lists, WDS, multicast conversion, low-ack disassociation, short preamble, inactivity controls, 802.11r domain/FT settings, 802.11k/v where supported, distance, RTS and fragmentation. Unchanged advanced fields are omitted. Unsupported advertised features stay unavailable.
- Routed Wi-Fi uplink retains the existing independent-subnet behavior.
- WDS STA joins an existing network, stops local address distribution transactionally and verifies actual bridge membership. Upstream WDS support/configuration is required.
- relayd IPv4 relay uses a separate upstream network, existing permissive downstream firewall zone, native relay network and DHCP/broadcast forwarding. It preserves the original LAN management address. Overlapping upstream/LAN subnets fail and roll back.
- Optional relayd installation requires confirmation, follows the common source policy, then checks the actual executable and init script. Current signed offline bundles are unchanged; missing relayd uses the existing router/PC download paths.
- Stop/restart from the existing signal list delegates managed bridges to the same transaction. Stop restores original DHCP state; restart disables local address distribution again. Editing suspended connections also restores the right state.

## Safety and maintenance

Wireless/network/firewall and any DHCP edits are staged in an isolated rpcd session, applied with a 90-second device rollback timeout, then confirmed only after operation-specific runtime checks. DHCP originals are retained as non-secret metadata on the device. A failed/cancelled operation verifies config hashes, exact prior active interfaces and a settled wireless runtime before reporting restoration. SSH loss reports recovery uncertainty; it never claims success.

Concurrent edits, shared bridge ownership, an unexpected existing relay, modified DHCP values or invalid recovery metadata stop the operation. No replacement of wpad, kernel driver or unrelated package is attempted. No guessed phy/radio mapping or UI timer workaround was added. Long scripts continue through the common SSH stdin transport. No page performs synchronous network I/O.

The inline settings stack uses one shared current-page layout for size hints, minimum size and height-for-width. Hidden long forms no longer create blank space in shorter tabs. Both themes retain the existing shared styles.

## Review evidence

1. Baseline and request coverage: existing 22 native/model tests passed before changes; later 41 related tests passed while assembling the first bridge paths.
2. Failure/runtime review: real Lua tests cover bridge configuration, all-four-config rollback, subnet conflicts, missing runtime relay process, missing bridge membership, repeated edits and returning to routed mode. Source/model/bridge regression passed 37 tests at this stage.
3. UI and cross-entrypoint review: actual dark/light rendering, 100/150 percent form geometry, the existing application's 100/125/150/175 percent checks, signal switches, suspended edits, external DHCP changes, ownership conflicts and exact settled restoration. Final combined result: 112 tests passed, no failures, errors or skips; recorded in build/wireless-review/final-tests.json. Current source six-page smoke test passed.

Rendered artifacts: tests/wireless-review-preview/. Publication and frozen build reports remain in the established build metadata directories.

## Limits that remain explicit

- This is not real-device certification, nor a claim that every OpenWrt fork/model works. Native UCI, ubus, iwinfo, a compatible wireless driver and rpcd rollback interfaces are required.
- WDS requires compatible upstream support; relayd relays IPv4 only. Neither an associated station nor a running relay process proves end-to-end client traffic on physical hardware.
- relayd requires an existing static LAN management subnet different from the upstream subnet. The app rejects overlap instead of automatically renumbering the user's LAN and losing the management connection.
- Enterprise EAP/certificate authentication, mesh/VLAN provisioning and arbitrary vendor-only parameters are not implemented by this form. Existing unsupported encryption configurations fail validation without being rewritten.
- Switching directly between the two managed bridge types requires saving routed mode first so the previous address-distribution state is restored. Retargeting an existing relay uses the same explicit intermediate step.
- Physical router acceptance is still needed for upstream WDS interoperation, relay DHCP/data traffic, reboot persistence, SSH-loss recovery and vendor-specific service behavior.

## Primary references

- OpenWrt WDS: https://openwrt.org/docs/guide-user/network/wifi/wifiextenders/wds
- OpenWrt relayd: https://openwrt.org/docs/guide-user/network/wifi/wifiextenders/relay_configuration
- LuCI wireless implementation: https://github.com/openwrt/luci/blob/master/modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js
- relayd service/configuration: https://github.com/openwrt/openwrt/blob/main/package/network/services/relayd/files/relay.init
- rpcd UCI transaction implementation: https://github.com/openwrt/rpcd/blob/master/uci.c


## Targeted relocation cleanup (2026-10-21)

- Configured-signals page has a fixed bottom action, expanded to the available content width with the existing side margins. Shared page-button property `dangerAction=true` provides the red warning appearance in both themes, including hover, pressed and disabled states.
- Preview runs in the common worker and contains no passwords. Confirmation binds to the SSH connection identity, hashes of wireless/network/firewall/dhcp/system, and the detected runtime MAC targets. Cancel and changed connections do not start cleanup.
- One isolated rpcd session removes Wi-Fi interface sections, application-owned relay/uplink interfaces and unshared wireless-only DHCP uplinks. DHCP settings suspended by application-managed bridges are restored through their validated ownership backups. Static management networks, LAN address, login credentials, installed packages and radio hardware selection remain intact.
- New hostname uses `OpenWrt-` plus eight random hex characters. Independent locally administered unicast MACs apply to explicitly identified Ethernet/bridge devices; legacy interface overrides and modern device sections are supported. Unsupported or ambiguous devices are retained or block cleanup; EEPROM/calibration is never written.
- Remaining DHCP interfaces announce the new hostname and stop using configured old client identifiers. User files, archives, third-party logs, custom provider configuration and other software data are outside this operation; this is not a complete privacy wipe or factory reset.
- Firmware must expose rpcd rollback. Apply uses the device-side 90-second protection. Persisted hashes, stopped wireless interfaces, active hostname and runtime MACs must be verified before confirm. Failure/cancellation requests rollback using an independent cleanup deadline; unconfirmed recovery is reported as uncertain.
- Verified offline using real Lua transactions and Qt UI tests, plus actual standalone-window renders for both themes. No live router was cleaned during implementation; vendor-specific MAC overrides and hardware behavior still require device evidence.


## Signal-management audit (2026-10-21, current source)

The application was shown through its single-instance launcher for the user's testing. The existing interactive process was left running; no live router changes were performed by this audit.

Confirmed and fixed after failing regression cases:

- Pending-change preview kept its waiting flag after disconnect, so reconnect could leave the review button disabled. The controller now invalidates a pending preview when its connection disappears or changes.
- An editor could submit its old request after switching routers, including a switch while the confirmation was open. Every editor now captures its original SSH connection; the common task entry rechecks it for password reads, component installation, apply and save-only. Delete checks the original connection after confirmation. Toggle and runtime repair bind confirmation to the original inventory instance and reject replaced/stale lists.
- A worker finishing after disconnect discarded its completion and recovery warning. The shared service now emits an explicit connection-changed completion without its old value, clears password results, releases its task and preserves recovery errors in the information log. These results never update the next device's environment or scan list.
- Temporary apply/toggle/repair confirmation dialogs are released after use.

The audit includes native Lua staging, exact configuration rollback, radio ownership, AP/STA editing, WDS/relayd DHCP restoration, deleting shared interfaces conservatively, read-only scans, password disclosure/clearing, save-only persistence, pending commits, orphan cleanup and relocation. Qt checks cover numeric scan sorting, stable join targets, privacy projection, detail wrapping, confirmation/cancellation, radio-dependent buttons, both themes and scaling.

This remains offline verification, not hardware certification. Real upstream WDS interoperability, client forwarding/DHCP, driver MAC support, restart persistence and loss-of-management recovery need corresponding router evidence. The existing running process must be reopened after the user's test session to load these source fixes; it was not interrupted or replaced during testing.


## Radio mode and client isolation refinement (2026-10-21)

- AP common wireless settings now separate standard (Wi-Fi 4/5/6/6E/7 or legacy, as detected) and width in MHz. Unchanged controls omit htmode; selecting a standard maps the linked width to a driver-provided native htmode. The actual band comes from configured band, unambiguous channel frequencies, or legacy hwmode; radio numbers are never used.
- Detection, editor and save use wireless_radio_modes.supported_htmodes. 2.4 GHz excludes VHT and widths above 40 MHz, 5 GHz excludes 320 MHz, and 6 GHz excludes HT/VHT/legacy. Firmware feature flags and the driver's mode list are both required. Unknown bands retain existing settings without fabricating selectable support.
- Client isolation is explicitly named and placed in AP common security settings. New APs submit isolate=1 by default. Editing preserves the original value, including the absence of an isolation option (disabled). This is same-Wi-Fi client isolation, not a separate guest firewall or an assertion of isolation from wired LAN devices.
- New offline tests cover linked choices, round-trip encoding, unknown capabilities, original-setting preservation, radio-band rejection at the save boundary, saved native isolation values, and actual dark/light renders at 100/175 percent. No live router settings were changed during this work.


## Missing radio-setting defaults

When htmode is absent/empty, AP configuration selects an explicit supported default: 20 MHz first on 2.4 GHz, 80 MHz first on 5/6 GHz, falling back to supported lower widths (40/20 MHz on 5/6 GHz). At each width prefer HE, VHT, HT, EHT, then legacy, subject to the common band/driver/firmware rules. Existing nonempty radio settings remain preserved, including vendor-specific values the editor cannot safely interpret. Defaults appear in the editor and the backend independently fills them if omitted, before either apply or save-only. Unknown capabilities never cause a guessed mode to be written. Offline tests cover empty and whitespace settings, old radios, limited widths, and the actual native config saved from a request without htmode.


## Relocation and interface review correction (2026-10-25)

- Relocation removes all old Wi-Fi sections, old `wwan`/`wwanN` interfaces (including disconnected leftovers), relay interfaces and dependent old logical interfaces in one rollback-protected transaction. Matching routes, address pools and firewall zone memberships are cleared together. Shared old uplinks are part of this reset, not a reason to skip it. Base LAN/WAN/loopback interfaces remain outside the removal set.
- MAC discovery only controls which retained devices receive a new address. It does not decide whether an old uplink can be deleted. Retained-device MAC skips have individual reasons.
- Orphan review permits an unreferenced, unbound and inactive legacy `wwan` interface even when its creator is unknown. Selection starts empty. A fresh runtime/configuration check is required immediately before cleanup. Orphan cleanup saves configuration without reloading the current network.
- Batch station identity supports ordinary relay clients with independent DHCP uplinks. A shared DHCP hostname, transparent LAN bridge or explicit device binding is described with the actual interface and peer names instead of a generic skip message. The Lua executor rechecks these conditions.
- Finished worker threads remain valid throughout modal confirmation callbacks, then leave the service registry and are deleted. No timer or invalid-object exception suppression is used.
- Official release-pinned netifd status fields and rc.common hooks were checked for 19.07.10, 21.02.1, 22.03.7, 23.05.5, 24.10.0 and 25.12.0. Source URLs and commits are recorded in `build/relocation-boot-fixes-20261025/official-contracts.json`. Offline tests exercise the actual Lua and Qt event loops; live-router validation is still required.


## Shared upstream identities (2026-10-25)

Saved STA profiles on one unbound DHCP interface now form one identity group. The preview assigns a unique MAC to each profile and one hostname to the group. The executor verifies complete membership, validates backups before writing, snapshots live MACs once per group and writes the hostname once through the existing transaction. Batch updates preserve SSIDs, credentials, interface membership and enabled states.

Version-1 identity backups remain readable. Group backups add a `shared` flag and retain the original hostname and each profile's original MAC, including absent values. Repeated batches preserve those originals. Individual restoration restores only that MAC while managed peers remain; the last restoration returns the original hostname. Single-profile hostname changes synchronize peer recovery records. External edits, conflicting backups and unexpected group members stop the transaction.

Single-profile editing can restore identity while retaining common protocol, DNS, firewall membership and relay settings. Attempts to change common settings through one member are rejected explicitly. Existing apply/rollback and save-only lifecycles are retained.

Official DHCP hostname fields were checked for 19.07.10, 21.02.1, 22.03.7, 23.05.5, 24.10.0 and 25.12.0; source URLs are in `build/shared-station-identity-20261025/official-dhcp-contracts.json`. Offline tests run the real Lua and Qt paths. Live hardware behavior remains to be verified.
