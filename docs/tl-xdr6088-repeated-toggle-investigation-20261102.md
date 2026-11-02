# TL-XDR6088 repeated AP enable investigation - 2026-11-02

## Evidence inputs

- 20261031_163442 ZIP SHA256 3e928a285a5f1bebcb822ec174d6de33c69ff4507a862162fd2a9dff7ae823a7
- 20261031_172033 ZIP SHA256 435debce08de0e62413cb4e421a05cfc67eda3d095b3418ccd4da6875184caae
- 20261031_185221 ZIP SHA256 4700a10d53c7c196b1fa4b3221a34954af1abf13d01a1d9b4d77dadce0ac11d3
- 20261101_101140 ZIP SHA256 da1768c3faca37c3e792966438e2745237a86e7694555d4f476af0cac4220b20
- 20261101_101659 ZIP SHA256 7d57b7ccaec93dce3f1be4fe54c0c33a665727a22149ddfd5c9fa7c94f628fe7

## Confirmed observations

- The user reports that enabling through the web UI temporarily permits later toggles in the application, but repeated application use eventually fails again. The existing ZIPs do not contain the web requests or a full sequence of per-toggle observations.
- October 31 failures include wireless_save readiness timeouts near 49 seconds. A separate uci.apply native error also occurred; it must not be conflated with all timeouts.
- November 1 10:10:10 local time: a simple toggle reports wireless configuration conflict. That is not proof of invalid password or a hostapd startup error.
- November 1 10:12:56 local time: startup confirmation fails.
- November 1 about 10:14 local time: capture takeover, process stop and interface cleanup report failures. This is later than the 10:12:56 failure, so it cannot establish causation for that earlier failure. It may affect later operations and remains a separate unresolved observation.

## Execution paths checked

- Ordinary row toggle: isolated UCI save of the selected interface disabled flag, then /sbin/wifi reload and inventory verification.
- Parent-radio enable or managed bridge: the rollback session toggle path with native uci.apply and detailed readiness checking.
- Save/apply: the rollback session stage path; additional settings can legitimately differ from a simple toggle.
- OpenWrt 23.05.5 /sbin/wifi source was read at https://raw.githubusercontent.com/openwrt/openwrt/v23.05.5/package/base-files/files/sbin/wifi . Its reload command invokes network reload; it is not proof of identical behavior to the browser UI. Exact LuCI feed metadata could not be retrieved, so no exact browser equivalence is claimed.
- Existing pinned netifd source shows disabled, autostart and retry_setup_failed can prevent setup. No supplied observation proves one of these is the cause on this device. Do not add forced down/up, retries or timeout extensions on this basis.

## Diagnostic gap repaired

Earlier detailed readiness evidence covered the rollback-session path. Ordinary direct toggles now use the same bounded evidence collection and identify source=direct_toggle versus source=session_apply. Their requested switch, observed configured switch, native inventory state and failure reason are retained separately per attempt. No raw configuration, SSID or password is captured. No extra remote command or changed restart semantics is introduced.
54 targeted offline tests passed, covering repeated toggles, distinguishable configuration/runtime failure, pre-poll configuration conflict, export redaction, parent-radio operations, foreground handoff and single busy-cycle completion. Python compilation and diff checks pass. No real-router or Debian 12 runtime test has been performed.

## Next evidence required

Restart the updated source application. With the same 5 GHz AP, record whether capture or scanning is involved, reproduce the web-enable then application-toggle sequence, and export Signal Management diagnostics immediately after recurrence. The 1.0.28_alpha ZIPs cannot retroactively contain the new diagnostic fields. Underlying AP startup behavior has not been changed or claimed fixed.
