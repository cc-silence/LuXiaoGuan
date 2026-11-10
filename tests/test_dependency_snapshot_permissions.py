import unittest
from modules.capture_services.openwrt.dependency_snapshot_permissions import parse_directory_metadata


class PermissionTests(unittest.TestCase):
    def test_stock_busybox_directory_permissions_and_numeric_owner(self):
        output="/etc/private\tls\tdrwx------ 2 123 456 4096 Nov 8 12:00 /etc/private"
        self.assertEqual(parse_directory_metadata(output,{"etc/private"}),{"etc/private":(0o700,123,456,0)})

    def test_special_directory_bits_are_preserved(self):
        for mode,wanted in (("drwxrwsr-t",0o3775),("drwsr-S--T",0o7740)):
            output="/private\tls\t"+mode+" 2 0 0 4096 Nov 8 12:00 /private"
            self.assertEqual(parse_directory_metadata(output,{"private"})["private"][0],wanted)

    def test_optional_stat_preserves_epoch(self):
        self.assertEqual(parse_directory_metadata("/private\t2700 12 34 100",{"private"}),
                         {"private":(0o2700,12,34,100)})

    def test_invalid_permissions_owners_and_unexpected_paths_are_rejected(self):
        for output in ("/private\tls\t-rwx------ 1 0 0 file",
                       "/private\tls\tdrwx------ 1 user group dir",
                       "/private\tls\tdrwxrwxrwx+ 1 0 0 dir",
                       "/other\t755 0 0 0", "/private\t999 0 0 0",
                       "/private\t755 0 0 0\n/private\t755 0 0 0"):
            with self.assertRaises(ValueError):parse_directory_metadata(output,{"private"})


class FilePermissionTests(unittest.TestCase):
    def test_stat_and_stock_ls_records_agree(self):
        from modules.capture_services.openwrt.dependency_snapshot_permissions import parse_file_metadata
        self.assertEqual(parse_file_metadata('stat\t640 123 456'),(0o640,123,456))
        self.assertEqual(parse_file_metadata('ls\t-rw-r----- 1 123 456 10 Nov 9 12:00 /etc/config/a b'),(0o640,123,456))

    def test_links_non_numeric_owners_and_multiline_records_are_rejected(self):
        from modules.capture_services.openwrt.dependency_snapshot_permissions import parse_file_metadata
        for text in ('stat\t999 0 0','stat\t640 user group','ls\tlrwxrwxrwx 1 0 0 link','stat\t640 0 0\nstat\t644 0 0'):
            with self.assertRaises(ValueError):parse_file_metadata(text)
