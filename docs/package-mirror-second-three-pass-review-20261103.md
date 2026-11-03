# Second independent three-pass review, 2026-11-03

## Pass 1: mirror and HTTP contracts

Read current USTC operator help and Python 3.10 urllib documentation. Historical
and snapshot mirror completeness is not guaranteed. No new confirmed source-address
or proxy/direct-routing defect was found. Retain exact HTTPS source-family restrictions,
version/architecture matching and existing integrity checks.

## Pass 2: native execution and lost receipts

The real command runner can return cancellation after a remote command has exited,
before completing output collection. Previously domestic_sources only knew a random
mktemp path after receiving it: cancellation or transport failure could skip cleanup.
A failing regression reproduced that missing cleanup call for both exception types.

Use a validated random 128-bit task identifier as the source-directory suffix. The
path is known before sending preparation. Atomic mkdir refuses to overwrite an existing
entry. Cleanup runs even if preparation does not return its receipt; it verifies the
exact owner and rejects directory/owner symlinks. Missing directories are a successful
no-op. Never trust a returned foreign path as a cleanup target. Original router source
files are not changed. Both native installers use the same context.

This uses existing mkdir/test/cat/rm command syntax; no new BusyBox options or
hardware-specific operations are introduced. Verified all 50 recorded release
Makefile hashes and 18 fixed-commit component hashes. The blocked OpenWrt wiki and
POSIX mkdir page were not treated as evidence; exact source and executable fixtures
supply the review evidence.

## Pass 3: failure and integration checks

149 targeted tests passed. Added regressions for cancellation/lost preparation replies, idempotent cleanup,
conflicting ownership, existing-directory protection and foreign path receipts.
Retained real loopback HTTP redirect, native shell repository isolation, proxy/direct
routing, integrity, cancellation and both install entrypoint regressions. Shell test
output uses explicit UTF-8 decoding on Windows.

Cleanup cannot be guaranteed when the router is unreachable; the warning remains.
The task path allows a precise cleanup attempt, not a guarantee that an unknown
remote process has exited. No real router modification, package installation,
full Debian runtime verification, repackaging or publication occurred.

Sources and conclusions: package-mirror-second-review-sources-20261103.json.
Operator help: https://mirrors.ustc.edu.cn/help/openwrt.html
Python reference: https://docs.python.org/3.10/library/urllib.request.html
