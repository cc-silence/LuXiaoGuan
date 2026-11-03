# Dependency source routing, 2026-11-03

Applies to OpenWrt package indexes and IPK/APK payloads in the shared PC downloader,
and native dependency installation in both dependency and signal services.

- Direct: HTTPS Tsinghua, USTC, then NJU mirrors. Preserve the exact repository path,
  release, architecture, filename and existing integrity checks.
- Proxy: retain official downloads/archive/CDN endpoints. If an actual request falls
  back to direct or matches NO_PROXY, use domestic sources for that request.
- Redirects cannot cross source families, downgrade HTTPS or bypass a selected proxy.
- A configured PC proxy sends native installation to the existing PC-download/upload
  route. Native direct uses a private temporary repository copy; persistent router
  sources are unchanged. Cleanup checks its task owner token.
- Missing native sources can fall back to the PC mirror resolver. All mirror 404/410
  responses retain the typed status needed for gzip/plain-index fallback, with an
  explicit suggestion to use a proxy or matching offline packages.
- Existing custom project artifact URLs (whole offline bundles and approved pinned
  hcx bodies) are not OpenWrt repositories and remain at their original addresses.
- Mirror coverage is incomplete for historical releases. No official direct fallback,
  firmware-version substitution, insecure TLS, or signature bypass is added.

Exact component audit: package-mirror-sources-20261103.json covers 50 supported
release Makefiles and 18 fixed-commit package-manager source files. This operation
is hardware independent; package architecture and kernel compatibility remain under
existing checks. Offline tests include actual POSIX scripts executed against local
fixture directories. No live router installation or Debian runtime test was performed.

Review follow-up: terminal HTTP errors from one domestic mirror (including 403)
advance to the next mirror without retrying that same rejection. A confirmed
404/410 retains gzip/plain-index fallback even alongside transient route failures;
cancellation takes precedence. Rejected redirects explicitly close their response.
These boundaries have dedicated regressions, including the actual index fallback.

Three-pass follow-up: accepted HTTP(S) redirects close their unused page body before
following. Native source isolation covers quoted opkg fields and embedded URLs;
dynamic APK set-based repository configurations use the existing PC resolver because
static rewriting cannot establish their expanded host. See the three-pass review.

Second three-pass follow-up: the private source directory is identified by a random
128-bit task token before sending preparation. Lost/cancelled replies still trigger
an owner-checked cleanup attempt; unexpected returned paths are rejected. Existing
paths and symbolic links are not adopted. Unreachable-router cleanup remains an
explicit unconfirmed warning. See package-mirror-second-three-pass-review-20261103.md.
