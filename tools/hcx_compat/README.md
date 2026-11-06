# hcxdumptool selected-interface correction

Upstream: hcxdumptool 6.3.4, commit 8dad077bfee93281bdf8f1115ba0c047d22230d7.
The complete corresponding upstream source archive and its SHA256 are provided here.

The parser correction preserves the explicitly selected interface index when
several interfaces share a wiphy. It changes no transmission, capture, retry or PIN
logic. No native router interfaces need to be deleted or enabled to work around the
upstream enumeration bug.

Build with the verified Zig 0.13.0 compiler and matching Linux UAPI headers using
`python3 tools/hcx_compat/build.py`. Environment variables
ROUTERMANAGER_HCX_TOOLCHAIN and ROUTERMANAGER_HCX_BUILD_ROOT can locate temporary
build resources. The script validates the archived source, applies the exact
correction and emits five static musl variants plus an integrity-checked local
manifest into bin/hcx_compat. MIPS variants require soft-float; ARM uses baseline EABI.
Byte-swap macros use compiler builtins so side-effect arguments are evaluated once,
including on big-endian MIPS; no wireless frame logic is changed.
The optional textual BPF compiler is disabled to avoid libpcap runtime replacement;
the existing precompiled BPF file input remains available.

`test_enumeration.c` exercises the actual upstream netlink parser. The original
source fails with hcxmon0 followed by wlan0; the corrected source passes both
orders and preserves automatic selection and other physical radios.

License: upstream hcxdumptool is MIT licensed (see the full upstream archive).
Zig and musl license notices must accompany the generated artifacts.
