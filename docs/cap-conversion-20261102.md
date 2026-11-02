# Automatic CAP conversion - 2026-11-02

## Scope
Pure local-file processing. No router capture commands, installation, wireless configuration or recovery behavior changed. Existing download API calls stat before and after transfer to prevent automatically converting a still-changing snapshot.

## Product policy
- Foreground automatic-capture cleanup, offline artifact pulls and ordinary handshake/PMKID results share one local CAP converter.
- Automatic 22000 and HCCAPX generation is disabled. Legacy result fields and historical format-reading functions remain compatible; old saved files are not deleted.
- Original packet files remain retained; old keep_raw_capture=false and generate_22000=true settings cannot bypass policy.
- Existing native CAP is validated rather than copied. Assisted PCAPNG is saved with its actual extension, fixing the previous suffix-only CAP naming.
- Capture settings reuse the existing theme/layout: the original-retention checkbox was removed; CAP generation remains checked and fixed on.

## Conversion guarantees and limits
- Streaming, bounded blocks (16 MiB), 512 MiB input cap, 30 second conversion/verification budget and cancellation support in the shared API.
- Validate block lengths, interfaces, packet lengths, timestamp resolution/offset and classic PCAP bounds. Never synthesize missing packet bytes or handshakes.
- Write a staging file, independently read its packet records, compare count and SHA256 over timestamps, original/captured lengths and every packet byte; only then publish via the shared local_file_publish service without overwriting existing files.
- Native Windows rename supports FAT/exFAT as well as NTFS; POSIX publication uses the existing hard-link policy and safely fails on unsupported filesystems.
- Multiple used interfaces/sections, unsupported link types, timestamp-less Simple Packet Blocks, subnanosecond timestamps and malformed/truncated files fail closed. Raw files remain available and the user receives a CAP failure warning.
- Output is standard nanosecond classic PCAP (extension .cap). PCAPNG annotations, interface metadata and secrets are not copied into CAP; the retained original preserves them. Compatibility with every third-party legacy importer is not guaranteed.
- Stop/cancel of router capture is distinct from conversion cancellation: normal capture-stop still completes bounded local conversion after download. No new UI timer, thread or conversion dialog is introduced.

## Format references read
Fixed work-in-progress IETF drafts, not claimed as finalized RFCs:
- https://www.ietf.org/archive/id/draft-ietf-opsawg-pcapng-04.html
- https://www.ietf.org/archive/id/draft-ietf-opsawg-pcap-03.html

## Validation
- 9 private PCAPNG files, 2,979 packets: all converted. Existing independent parsing paths found identical packet byte sequences, radiotap classification, handshake statuses and convertible device pairs. Original SHA256 values unchanged.
- Conversion timings with Python allocation tracking: 0.281 to 0.687 seconds per supplied sample, peak tracked allocations at most 46,730 bytes. These are small-sample measurements, not a large-file or process-RSS guarantee.
- Per-sample report: ignored build/converter-audit-20261101/cap-regression.json. Raw samples and resulting captures are private and must not be committed.
- Tests cover both byte orders; micro/nanosecond and binary clocks; timestamp offsets; truncated blocks; malformed lengths; mixed interfaces; missing timestamps; size/time/cancel bounds; existing-file protection; publication failure cleanup; real foreground/offline download call chains with stable and unstable snapshots; old product-setting isolation.
- Windows offscreen settings dialog save/reopen and light/dark rendering checked. No router mutation or live device acceptance was performed. Debian 12 execution and third-party packet tools remain unverified.


## Official-source follow-up audit - 2026-11-02

Pinned sources inspected:
- libpcap 1.10.5 sf-pcap.c, sf-pcapng.c, pcap-common.c and pcap-int.h, at https://github.com/the-tcpdump-group/libpcap/tree/libpcap-1.10.5
- The fixed IETF drafts above, especially PCAPNG if_fcslen / packet flags and classic PCAP link-type extension fields.

Confirmed fixes:
- Interface FCS length and per-packet overrides were previously discarded. Preserve representable, uniform FCS lengths in classic PCAP's P/FCS fields; reject mixed or unrepresentable lengths rather than silently changing interpretation. Per-packet error/offload indicators that cannot be represented now stop conversion with original retained.
- 16 MiB was a block-allocation bound, not libpcap's accepted 802.11 packet bound. Align packet/output-snapshot bound to libpcap's 262,144-byte MAXIMUM_SNAPLEN; reject oversized malformed frames before publishing.
- Avoid materializing all metadata options into lists. Scan lazily and check cancellation/deadline at bounded byte intervals, including varying option lengths.
- Remove the original-retention checkbox per user request. The collection/configuration/backend policy still unconditionally retains originals. Existing CAP-generation control and shared styles remain.

No Npcap/libpcap DLL or Wireshark executable is installed on this Windows host. Source comparisons and offline tests do not constitute an executed official-library acceptance. Debian 12 and third-party legacy importers remain unverified.

Official fixed-version source cache with SHA256: build/converter-audit-20261101/sources/libpcap-manifest.json. Follow-up sample report: cap-official-audit-samples.json (9 samples, 2,979 unchanged packet records).


## Second follow-up audit - 2026-11-02

Rechecked fixed libpcap 1.10.5 sf-pcap.c and PCAPNG draft-04. No new packet-field serialization error was found. Added independent dpkt 1.9.8 reading (its upstream pcap.py/pcapng.py were inspected); package is confined to ignored build/cap-independent-reader and is not an application dependency.

Three deterministic tests reproduced success-at-end races before the fix: native CAP modified after its scan; cancellation arriving after the last record; source PCAPNG changed during verification of staged CAP. The source-signature check is now shared, native CAP sizes/identities are checked on the opened descriptor, cancellation is checked after scanning, and conversion checks source metadata again just before publication. These are metadata consistency checks; they do not claim to detect adversarial same-size changes with deliberately preserved filesystem timestamps.

Validation after repair: 28 targeted container/stability tests passed, including all three previously failing cases. Independent dpkt reading passed on all nine supplied PCAPNG/CAP pairs (2,979 packets, exact packet-byte equality and unchanged original SHA256). Its PCAPNG Reader uses floating-point epoch times, so the real-sample timestamp comparison uses a 1 microsecond tolerance; 64 generated little/big-endian micro/nanosecond cases separately verified exact decimal timestamps through dpkt's CAP reader.

Evidence: ignored build/converter-audit-20261101/cap-independent-verification.json. No Npcap official-library execution, Debian 12 runtime or third-party legacy application acceptance is claimed. Existing unsupported-format rejections and original retention policy remain unchanged.

References:
- https://raw.githubusercontent.com/kbandla/dpkt/v1.9.8/dpkt/pcap.py
- https://raw.githubusercontent.com/kbandla/dpkt/v1.9.8/dpkt/pcapng.py
- https://docs.python.org/3.10/library/os.html#os.fstat
