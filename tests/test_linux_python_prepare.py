"""Private runtime source boundaries and failed-build preservation."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from scripts import prepare_linux_python as build


def source_archive(path, entries=None):
    entries = entries or [(build.SOURCE_ROOT + "/configure", b"configure", tarfile.REGTYPE)]
    with tarfile.open(path, "w:xz") as archive:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name); info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data); archive.addfile(info, io.BytesIO(data))
            else:
                info.linkname = "../../outside"; archive.addfile(info)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PrivatePythonPreparationTests(unittest.TestCase):
    def test_wrong_host_never_creates_output_or_reads_archive(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, "admit_host", side_effect=ValueError("wrong host")), patch.object(build, "verified_source") as read:
            root = Path(folder)
            with self.assertRaises(ValueError): build.build_private_python(root / "source", root=root)
            self.assertEqual(list(root.iterdir()), []); read.assert_not_called()

    def test_invalid_configuration_budget_never_reads_source_or_creates_output(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build, "admit_host"), patch.object(build, "verified_source") as read:
            root = Path(folder)
            for budget in (0, 59, 1801, True, "900"):
                with self.subTest(budget=budget), self.assertRaises(ValueError):
                    build.build_private_python(root / "source", root=root, configure_timeout=budget)
            read.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_corrupt_source_is_rejected_before_extracting(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "Python.tar.xz"; source.write_bytes(b"incorrect source")
            with self.assertRaises(ValueError): build.verified_source(source)

    def test_archive_traversal_links_and_duplicates_leave_destination_absent(self):
        bad = [(build.SOURCE_ROOT + "/../outside", tarfile.REGTYPE),
               (build.SOURCE_ROOT + "/link", tarfile.SYMTYPE),
               ("/outside", tarfile.REGTYPE)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, kind in bad:
                source_archive(root / "source.tar.xz", [(build.SOURCE_ROOT + "/configure", b"ok", tarfile.REGTYPE), (name, b"bad", kind)])
                with self.assertRaises(ValueError): build.extract_source(root / "source.tar.xz", root / "out")
                self.assertFalse((root / "out").exists())
            entries = [(build.SOURCE_ROOT + "/configure", b"ok", tarfile.REGTYPE)] * 2
            source_archive(root / "source.tar.xz", entries)
            with self.assertRaises(ValueError): build.extract_source(root / "source.tar.xz", root / "out")
            self.assertFalse((root / "out").exists())

    def test_failed_compile_preserves_previous_verified_runtime_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.tar.xz"
            digest = source_archive(source)
            marker = root / "build/debian12/python-runtime/current.json"
            marker.parent.mkdir(parents=True); marker.write_bytes(b"previous verified runtime")
            def run(command, stage, phase, timeout, env=None):
                if phase == "compile": raise RuntimeError("synthetic compiler failure")
            with patch.object(build, "admit_host"), patch.object(build, "SOURCE_SHA256", digest), patch.object(build, "run_build", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    build.build_private_python(source, root=root)
            self.assertEqual(marker.read_bytes(), b"previous verified runtime")
            reports = list(marker.parent.glob("*/report.json"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(json.loads(reports[0].read_text(encoding="utf-8"))["status"], "failed")

    def test_archive_changed_after_initial_check_is_rejected_before_execute(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.tar.xz"
            digest = source_archive(source)
            original = build.verified_source
            def verify(path):
                result = original(path)
                if Path(path) == source: source.write_bytes(b"changed after first fingerprint")
                return result
            with patch.object(build, "admit_host"), patch.object(build, "SOURCE_SHA256", digest), patch.object(build, "verified_source", side_effect=verify), patch.object(build, "run_build") as execute:
                with self.assertRaises(ValueError): build.build_private_python(source, root=root)
                execute.assert_not_called()
            self.assertFalse((root / "build/debian12/python-runtime/current.json").exists())

    def test_valid_archive_stays_under_private_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.tar.xz"; digest = source_archive(source)
            with patch.object(build, "SOURCE_SHA256", digest): self.assertEqual(build.verified_source(source), source)
            extracted = build.extract_source(source, root / "out")
            self.assertEqual((extracted / "configure").read_bytes(), b"configure")
