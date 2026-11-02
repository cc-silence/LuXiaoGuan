# Handshake conversion audit - 2026-11-01

Reference versions:
- Hashcat 6.2.6 module_22000.c: https://raw.githubusercontent.com/hashcat/hashcat/v6.2.6/src/modules/module_22000.c
- hcxtools 6.3.5 hcxpcapngtool.c: https://raw.githubusercontent.com/ZerBea/hcxtools/6.3.5/hcxpcapngtool.c

Implemented in the shared Python converter, with no UI changes:
- Preserve original SSID octets from unambiguous management frames for serialization. Existing text extraction API remains unchanged; raw mode is explicit.
- Enumerate the existing strict replay-counter/message-pair candidates instead of keeping only one exchange per AP/client. Retain PMKID even when EAPOL is also present.
- Exact output deduplication uses a bounded insertion-ordered mapping. Reject output above 20,000 records or 8 MiB; candidate iteration has a 10 second deadline and optional cancellation.
- Do not emit EAPOL above the 256-byte limit of the pinned Hashcat parser. Existing input bound remains 64 MiB.

Validation:
- Five offline regression tests passed, including official known-password PMKID vector, synthetic known-password EAPOL PCAP conversion with independent MIC verification, 393-byte HCCAPX layout, raw non-UTF8 SSID, multiple sessions plus PMKID, deduplication and output bounds.
- Nine private user PCAPNG samples: all converted; old 43 output records retained, new output 503 candidates. Counts are not quality or password validation. No user sample passwords were supplied.
- Per-file comparison took about 0.11 to 0.27 seconds on this Windows machine, including old and new conversion. Sample SHA256 hashes unchanged. Private samples and produced hashes remain under ignored build/converter-audit-20261101 and must not be committed.

Limits and remaining differences:
- This is not a full reimplementation or an executed comparison against hcxpcapngtool. The pinned official source was inspected; the official executable was not run.
- Python keeps strict replay counters, the existing 10-second exchange window, supported 802.11/radiotap link types and key versions 1/2/3. Official tool additionally supports broader link types, metadata and configurable nonce/replay correction, AKM filtering and quality ranking/cleanup. No speculative correction flags were added.
- More candidates can increase downstream work. The new limits prevent unbounded growth but do not reproduce the official default quality ranking.
- Existing display/manual text paths do not globally preserve arbitrary SSID bytes; raw capture evidence now takes precedence only when unambiguous. Unsupported names without raw evidence must not be considered verified.
- Debian 12 runtime and real user-password verification remain untested. Shared changes use only Python standard library.
