# Three-pass dependency source review, 2026-11-03

## Pass 1: PC routing and HTTP lifecycle

Read the exact CPython 3.10.11 urllib implementation used by the local runtime,
not only the current online documentation. HTTPRedirectHandler drains the entire
redirect body with read() before following. A continuously streaming redirect could
therefore consume unbounded memory. Accepted HTTP(S) redirects now close their
unused response before the standard handler opens the target. Existing source,
HTTPS and proxy-boundary validation remains intact.

## Pass 2: router native repository isolation

Verified SHA256 for all 50 recorded release Makefiles and 18 fixed-commit component
sources. All seven distinct opkg configuration parsers support quoted fields.
The previous token-prefix checks missed quoted URLs; plain-only proxy filtering
missed quoted option names. Added quoted proxy filtering and embedded URL validation
in the common temporary-source builder, preserving signature options and originals.
Read both exact APK repoparser commits: variable values are expanded into repository
URLs. Dynamic set-based configurations therefore use the existing PC profile resolver
instead of an unproven static native rewrite. Original router repositories are unchanged.

## Pass 3: integration, cancellation and resources

145 targeted tests passed. Added a real local HTTP server that advertises a stalled
1 GB redirect body: the client skips that body and retrieves the 2-byte target under
an 8-byte download limit. Native POSIX shell fixtures cover quoted proxy options,
quoted unknown sources, APK variables, rollback of temporary preparation and original
file preservation. Existing regressions cover mirror 403/404/timeouts, gzip/plain
fallback, cancellation, signature/hash safeguards, diagnostics and both native
installation entrypoints. No UI style change was made.

## Evidence and limits

- Exact source URLs, checksums and conclusions: package-mirror-review-sources-20261103.json.
- Baseline 50-release index: package-mirror-sources-20261103.json.
- CPython source: https://raw.githubusercontent.com/python/cpython/v3.10.11/Lib/urllib/request.py
- Mirror operator help: https://mirrors.tuna.tsinghua.edu.cn/help/openwrt/
- Mirror operator help: https://mirrors.ustc.edu.cn/help/openwrt.html
- APK source: https://raw.githubusercontent.com/alpinelinux/apk-tools/d093f7c198a64bff0cd58afeaf638909fda24ca8/src/repoparser.c

This is source inspection, offline shell validation and real loopback HTTP validation.
No live router installation or full Debian runtime test occurred. Current local HEAD
probes could not verify USTC due to an expired-certificate error; certificate checking
was not disabled. TUNA/NJU snapshots probes returned 404. Mirror completeness and
China-wide network reachability are not claimed. Custom project artifact addresses
remain unchanged. The updated source has not been repackaged.
