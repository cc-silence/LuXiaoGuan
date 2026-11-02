# Wireless startup evidence and cancellation - 2026-11-01

## Feedback and scope

Five TL-XDR6088 feedback ZIPs (October 31 16:34:42, 17:20:33,
18:52:21; November 1 10:11:40 and 10:16:59) report version
1.0.28_alpha. Several wireless saves fail after approximately 49 seconds.
The logs retain an unready result, not the native observation that caused it.
The user confirms the same 5 GHz AP starts through the router web UI.
This does not yet distinguish an apply-path difference from a readiness mismatch.
The homepage issue is deferred at the user's request.

## Changes

- Reuse the existing wireless status query; add only numeric/boolean diagnostic
  fields and a fixed enum for native AP state. No extra SSH commands, raw
  hostapd output, SSIDs, passwords, addresses or full configurations are logged.
- Record radio up/pending/disabled/retry failure, interface presence/link,
  configuration/identity mismatch reason, native error, frequency/channel,
  and DFS CAC active/duration when those optional fields exist.
- Keep at most 12 state transitions plus one terminal observation per wait.
  The terminal failure is retained in the existing bounded configuration
  failure collection even when ordinary command events roll over.
- Missing optional fields stay unknown; they do not imply success or failure.
  Existing AP readiness rules and the 45-second deadline are unchanged.
- Allow cooperative cancellation only during readiness verification, which
  already has an independent restore path. Actual staging, confirmation and
  restoration remain protected. Closing requests cancellation and waits for
  restoration; it does not interrupt a write or claim restoration succeeded.
- This changes behavior, not styling. Protocol and diagnostic code is shared
  by Windows and Debian 12, and is independent of CPU/model. No firmware
  package installation or driver changes are performed.

## Official sources checked

Observed firmware: official OpenWrt 23.05.5, mediatek/filogic.

- https://raw.githubusercontent.com/openwrt/openwrt/v23.05.5/package/network/services/hostapd/src/src/ap/ubus.c
  SHA256 3c1a67c33e9665b60c33c2c034cf0104d47f5a2755d9e1cf095ff3c6a1a1913a
  get_status supplies status/freq/channel and optional DFS CAC fields.
- https://raw.githubusercontent.com/openwrt/openwrt/v23.05.5/package/network/config/netifd/Makefile
  SHA256 68432ce8c0ecab75943a021970ac0ac02be40a012dd292efc688da75537743dc
  Pins netifd c18cc79d50002ab8529c21184aceb016c61ac61c.
- https://raw.githubusercontent.com/openwrt/netifd/c18cc79d50002ab8529c21184aceb016c61ac61c/wireless.c
  SHA256 ef15c152c334f8288b62be2e094542989d8f86aca5e0ce6b8144a7f28a6744e2
  wireless_device_status supplies up/pending/disabled/retry_setup_failed.

Cached source bytes and URL/hash manifest are under
build/wireless-startup-evidence-20261101/sources/.
Existing exact-release fixtures cover 50 hostapd source shapes, including
legacy missing-method and early status without SSID. Those contracts are
regressions, not a claim that every firmware/hardware combination was tested.

## Verification and remaining acceptance

95 targeted offline tests passed before the final band annotation; rerun
results are reported in the task. Tests cover actual Lua status responses,
legacy/modern protocol shapes, secret exclusion, export, bounded transitions,
retained failures and UI cancellation through actual Lua rollback.
Python syntax checks pass. No unrelated dirty files are part of this fix.
No real router changes have been made. Debian 12 runtime testing is pending.

Next acceptance: run this source build on the affected router, reproduce the
same 5 GHz enable operation, export Signal Management diagnostics immediately,
and compare the final native observation with the web UI result. Verify cancel
restores the previous configuration and the progress window then closes.
The underlying 5 GHz failure remains unresolved until that evidence exists.
No release or GitHub push has been performed.

## Follow-up: failed signal toggle leaves actions locked (2026-11-02)

The single-row change/repair path is owned by RouterSignalService itself.
On failure it marks the inventory unverified, but its own controller completion
is intentionally excluded from the foreign-task auto-refresh handler. No fresh
read was scheduled, so controls requiring a verified inventory remained disabled
until the user manually refreshed. This is a confirmed local lifecycle gap,
separate from the unresolved 5 GHz startup failure.

Track whether the completed own task was a write. On an ordinary write failure,
schedule one existing queued readback after task release. Successful readback
re-enables operations using the observed state; another read failure stays locked
without retrying. Cancelled, disconnected, transport and uncertain recovery
results retain existing protection. No writes are retried, no timers or styling
changes are introduced. This is local task orchestration; no new router command
or protocol semantics are introduced, so additional firmware API research is
not needed for this follow-up.

40 targeted tests passed: failed-start readback/unlock, failed readback without
loops, initial read failure, cancellation/recovery/transport protection,
foreground handoff and service lifecycle regressions. Windows offscreen only;
Debian 12 runtime and the reported real router remain unverified. No release.

## Follow-up: avoid a second disabled-button cycle (2026-11-02)

A successful row mutation already reads back the inventory. Publishing its new
revision invalidated the cached editor environment and scheduled a separate
wireless_inspect worker. The first task unlocked controls, then that worker
locked them again. Disabled theme styling made the existing second lock visible.

When the editing service is enabled, coordinate its existing inspect call inside
the mutation worker, before publishing completion. Transfer the inventory and
editing environment together in a short-lived SignalMutationResult, record the
matching inspection revision, and clear queued duplicate inspection requests.
If their wireless revisions differ, do not publish them as verified. Setup not
yet enabled adds no probe. Failed mutations do not enter metadata inspection.

No additional remote commands or timers are introduced: the existing follow-up
inspection moves into the original bounded worker. The transport remains
serialized and controls unlock once after the complete verification. This
optimizes lifecycle ordering, not firmware readiness rules or visual design.
51 targeted offline tests passed, including one busy cycle, matching editor
metadata, no duplicate worker, changed configuration rejection, failure readback,
foreground handoff and read-only lifecycle protection. Windows only; real-router
and Debian 12 runtime verification remain pending. No release or restart.
