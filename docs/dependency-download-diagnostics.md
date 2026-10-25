# Dependency download diagnostics

The dependency installer uses the existing PC-side urllib download path. This change adds evidence only: it does not change proxy ordering, retries, timeouts, certificate verification, package selection, or router configuration.

## Recorded evidence

- Once per download session: detected HTTP, SOCKS and system proxy endpoints; configuration-read exception classes; whether NO_PROXY is present; candidate routes.
- Per actual attempt: destination scheme/host/port, candidate proxy endpoint, proxy-bypass match, round, timeout, elapsed time, HTTP status, exception classes, numeric OS/TLS verification codes, response length and received bytes.
- Open failures remain labelled connection/waiting for response headers. urllib.open does not distinguish TCP connection, HTTPS proxy CONNECT, TLS negotiation and response-header waiting in every timeout. No more specific cause is invented.
- Body-read failures are distinguishable after response headers arrive. Partial successfully returned chunks are counted.

No username, password, raw exception string, URL path/query/fragment, response body, environment value or NO_PROXY contents is recorded. No probing requests, background timer, retained transfer buffers or unbounded diagnostic cache is added. Existing information-log export retains the new messages.

## Dependency proxy routing

The earlier 1.0.15 release only read HTTP and system proxy settings. The current source fixes that omission: configured HTTP, configured SOCKS, and system proxy are read in that order, deduplicated, followed by the existing direct fallback. Each file starts again at HTTP; successful SOCKS or direct transfers never reorder the next file. Empty proxy settings are skipped without a network attempt; system proxy is attempted only when detected. Information messages identify HTTP, SOCKS, system proxy or direct for each actual attempt, with skipped empty routes explained.

DependencyProxyHandler adapts SOCKS through the existing modules.http_socks_bridge.ProxyBridgePool. The pool is allocated only when a SOCKS route is actually attempted and is owned by the outer download_session. Nested index/package requests share it; success, failure and cancellation unwind through the same cleanup. No system proxy, environment variable or global socket is modified. The adapter applies urllib proxy exclusions before opening a bridge; HTTPS certificate checks remain in urllib.

An OS-level transparent tunnel cannot be inferred from explicit proxy settings. Proxy bypass is logged as a rule match, not proof of the entire physical network route. Offline bundle downloads already use the same shared bridge; this fix addresses per-package dependency downloads.

## Compatibility and verification

This is PC-side diagnostic metadata, independent of router CPU, radio driver and target. It sends no router command and changes no IPK/APK content. It therefore applies equally to the existing official OpenWrt 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 installation flows and all supported target architectures; this is not a claim of new physical-device certification.

References checked against Python 3.10 used by the application:
- https://docs.python.org/3.10/library/urllib.request.html#urllib.request.ProxyHandler
- https://docs.python.org/3.10/library/urllib.error.html#urllib.error.URLError

Validation: 99 targeted tests passed, covering existing route retries and deadlines plus connection/header timeout, body timeout after partial data, success, HTTP 407, bypass rules, configuration-read errors and secret exclusion. No real-router installation was performed.

## Pre-publication source audit

Rechecked CPython v3.10.11 source, not only the current documentation:
https://github.com/python/cpython/blob/v3.10.11/Lib/urllib/request.py
https://docs.python.org/3.10/library/urllib.parse.html#urllib.parse.urlsplit

ProxyHandler.proxy_open passes request.host (including any explicit port) to proxy_bypass. Diagnostic matching now uses the same input. Previously it stripped the port and could misreport host:port exclusions. Result lines say candidate endpoint rather than asserting that a configured proxy necessarily carried the request. IPv6 endpoints retain brackets.

Added real loopback HTTP tests for host:port bypass, partial body timeout, and HTTPS CONNECT proxy authentication rejection. Added real ZIP export verification and checks that proxy configuration is logged once per session, with no diagnostic configuration reads when there is no observer. No router, remote proxy or public network is contacted by these regression tests. The later SOCKS routing fix is described above and tested separately below.

## SOCKS routing verification

Official references checked:
- https://www.rfc-editor.org/rfc/rfc1928 (SOCKS5 CONNECT and destination address types)
- https://www.rfc-editor.org/rfc/rfc1929 (username/password negotiation)
- https://github.com/python/cpython/blob/v3.10.11/Lib/urllib/request.py (ProxyHandler.proxy_open and HTTPS CONNECT)

Ten additional tests cover real loopback SOCKS5 HTTP download, proxy-side DNS, HTTPS with a trusted test certificate, rejection of an untrusted certificate, authenticated SOCKS with no secrets in logs, HTTP-to-SOCKS failover, SOCKS-to-direct fallback, candidate ordering/deduplication, lazy allocation, and cleanup after success/failure. The combined 99 tests include the existing whole-bundle downloader regressions. Tests do not contact the user's router or an external SOCKS service. No new third-party runtime dependency is introduced.

## Concise information window

Technical configuration, per-attempt timing/status/byte evidence and low-level failures use the diagnostic message level. Public InfoPanel stores those entries within its existing line/byte limits and includes them in ZIP exports, but never paints them, including after privacy/theme refresh or history trimming. Dependency task progress ignores diagnostic-only updates. No duplicate log buffer or timer is introduced.

Visible download messages identify only the current channel and success/failure, for example the SOCKS proxy start and completion. The detailed evidence remains available in information.txt of exported logs, redacted as before. Proxy bypass summaries identify direct rather than claiming a configured proxy was used. The final installation route no longer repeats the parenthesized earlier router-online failure.

Validation covers the real rendered text in light/dark mode, detailed ZIP content, secret exclusion, redraw/trim/clear behavior, unchanged task progress, and the final installation-route string. This is a presentation/log separation change; proxy order, package matching, router commands and certificate validation are unchanged.
