"""Execute the generated probes against official APK/opkg output contracts."""
import os
import subprocess
import unittest

from modules.capture_services.openwrt.hcx_primary_installer import library_probe, parse_libraries
from tests.posix_shell import local_shell

MOCK = r"""
apk() {
    if [ "$QUERY_ERROR" = 1 ]; then printf 'database read failed\n' >&2; return 37; fi
    [ "$1" = info ] || return 90
    shift
    if [ "$#" = 0 ]; then
        printf 'libopenssl3\nlibpcap1-extra\n'
        if [ "$PCAP_PRESENT" = 1 ]; then printf 'libpcap1\n'; fi
        return 0
    fi
    exists=0; verbose=0; name=
    for arg in "$@"; do
        case "$arg" in -e) exists=1;; -v) verbose=1;; *) name=$arg;; esac
    done
    if [ "$QUERY_ERROR" = version ]; then printf 'installed query failed\n' >&2; return 1; fi
    case "$name" in
        libpcap1) version=1.10.6-r1; present=$PCAP_PRESENT;;
        libopenssl3) version=3.5.9-r1; present=1;;
        *) return 91;;
    esac
    [ "$present" = 1 ] || return 1
    if [ "$exists" = 1 ]; then
        if [ "$verbose" = 1 ]; then printf '%s-%s\n' "$name" "$version"
        else printf '%s\n' "$name"; fi
    else
        printf '%s: package description\n%s: https://example.invalid\n%s: 123 KiB\n' "$name" "$name" "$name"
    fi
}
opkg() {
    if [ "$QUERY_ERROR" = 1 ]; then printf 'database read failed\n' >&2; return 37; fi
    [ "$1" = status ] || return 92
    case "$2" in
        libpcap1) version=1.10.4-1; present=$PCAP_PRESENT;;
        libopenssl3) version=3.0.9-1; present=1;;
        *) return 93;;
    esac
    if [ "$present" = 1 ]; then
        printf 'Package: %s\nVersion: %s\nStatus: install ok installed\n\n' "$2" "$version"
    else
        printf 'Package: %s\nStatus: deinstall ok not-installed\n' "$2"
    fi
}
"""


def probe_result(pm, command=None, pcap=True, query_error=False):
    shell = local_shell()
    if shell is None:
        raise RuntimeError("A local POSIX shell is required for probe verification")
    env = dict(os.environ, PCAP_PRESENT="1" if pcap else "0", QUERY_ERROR=str(query_error) if isinstance(query_error, str) else "1" if query_error else "0")
    result = subprocess.run([str(shell)], input=MOCK + "\n" + (command or library_probe(pm)),
                            text=True, encoding="utf-8", capture_output=True,
                            timeout=10, env=env)
    return result


def run_probe(pm, command=None, pcap=True):
    result = probe_result(pm, command=command, pcap=pcap)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


class HcxLibraryProbeTests(unittest.TestCase):
    def test_apk_installed_query_returns_versions(self):
        self.assertEqual(parse_libraries(run_probe("apk")),
                         {"libpcap1": "1.10.6-r1", "libopenssl3": "3.5.9-r1"})

    def test_original_apk_query_reproduces_false_missing(self):
        old = library_probe("apk").replace("apk info -v -e ", "apk info -v ")
        with self.assertRaisesRegex(RuntimeError, "libpcap1"):
            parse_libraries(run_probe("apk", command=old))

    def test_apk_real_missing_library_remains_missing(self):
        self.assertEqual(parse_libraries(run_probe("apk", pcap=False)),
                         {"libopenssl3": "3.5.9-r1"})

    def test_old_opkg_status_contract_is_preserved(self):
        self.assertEqual(parse_libraries(run_probe("opkg")),
                         {"libpcap1": "1.10.4-1", "libopenssl3": "3.0.9-1"})
        self.assertEqual(parse_libraries(run_probe("opkg", pcap=False)),
                         {"libopenssl3": "3.0.9-1"})

    def test_all_50_exact_release_query_contracts(self):
        import json
        from pathlib import Path
        data = json.loads((Path(__file__).parent / "fixtures/hcx_installed_library_query_sources.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["releases"]), 50)
        opkg = {r["commit"] for r in data["opkg_sources"]}
        apk = {r["commit"] for r in data["apk_sources"]}
        for row in data["releases"]:
            with self.subTest(release=row["release"]):
                self.assertEqual(row["status"], "read")
                self.assertIn(row["source_commit"], apk if row["manager"] == "apk" else opkg)
                self.assertEqual(set(parse_libraries(run_probe(row["manager"]))), {"libpcap1", "libopenssl3"})

    def test_descriptions_and_invalid_versions_are_not_pins(self):
        with self.assertRaisesRegex(RuntimeError, "libpcap1"):
            parse_libraries("libpcap1=description text\nlibopenssl3=\n")

    def test_query_failures_propagate_for_both_managers(self):
        for pm in ("opkg", "apk"):
            with self.subTest(manager=pm):
                result = probe_result(pm, query_error=True)
                self.assertEqual(result.returncode, 37)
                self.assertEqual(result.stdout, "")
                self.assertIn("database read failed", result.stderr)

    def test_existing_apk_library_version_query_failure_is_not_missing(self):
        result = probe_result("apk", query_error="version")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("installed query failed", result.stderr)

    def test_duplicate_or_empty_versions_are_unknown_not_missing(self):
        for text in ("libpcap1=\n", "libpcap1=1\nlibpcap1=2\n"):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, "libpcap1"):
                parse_libraries(text)


if __name__ == "__main__":
    unittest.main()
