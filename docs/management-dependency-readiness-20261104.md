# Shared management dependency readiness (2026-11-04)

Common settings queues the existing RouterSignalManager.probe_dependencies when shown. Constructors
perform no network I/O. ManagementReadiness is owned by the persistent signal service, coalesces pending
requests, waits for the shared task slot and uses the normal worker-completion handoff. There are no
new timers or polling retries. Results bind to the SSH client and saved host identity; cancellation,
disconnection, invalid results and shutdown cannot become a successful readiness notification.

Both common-settings and signal-page install buttons now call RouterSignalService.install_dependencies,
which delegates to OpenWrtDependencyService.install_async({}, scope='signal'). Login installation uses
that same service and manager. Source selection, local-bundle preference, native/PC fallback, package
identity, architecture, version/hash checks and native plan guards are unchanged. The existing background
signal read can yield to the unified task; the signal page retains cancellation of this exact install.

The shared readiness observer listens to task completion for installs and uninstalls from all entrypoints,
then re-probes actual Lua/module availability, root and sha256sum. A complete result emits READY_NOTICE
once per installation or transition to readiness. The signal information panel now displays this success
message as well as warnings/errors, including replay when the page is constructed later. Missing-module
banners use the shared page button factory and existing dependency-highlight styling in both themes.
Missing Lua/module support disables only configuration-dependent settings; password and maintenance
controls retain their existing checks. Confirming on a different connection cannot start installation.

Scope: UI and desktop task orchestration using existing remote commands and the existing installer;
no router configuration, package source, dependency identity or hardware behavior was added. Read the
existing login-dependency source review, common-settings compatibility review and shared capability
requirements. Also inspected six immutable OpenWrt package Makefiles at v19.07.0 and v25.12.6; URLs and
SHA-256 are in management-dependency-sources-20261104.json. These are source checks, not a new claim
of live validation for all supported router releases or models.

Validation: 103 related offline tests passed, including 11 new readiness/UI cases. Checked both light
and dark actual Qt renders, unified delegation, login completion, repeat notification suppression,
missing/invalid/failed checks, shared-slot contention, cancellation, reconnect, changed-dialog identity,
page subscriptions and normal install regressions. Saved render evidence is under
build/management-dependencies-ui. Python syntax, UTF-8 and diff checks passed.

Shared Python/Qt logic has no new Windows-only dependency. Debian 12 live desktop and router tests
remain pending. No packages were installed on a real router and no application ZIP was rebuilt.
