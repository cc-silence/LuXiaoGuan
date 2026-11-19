"""Execute the actual read-only version shell against controlled databases."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from modules.capture_services.openwrt.curl_version_diagnostics import VERSION_QUERY, parse_versions

BASH='C:/Program Files/Git/usr/bin/bash.exe' if os.name=='nt' else shutil.which('bash')


def shell_path(path):
    value=Path(path).as_posix()
    return '/'+value[0].lower()+value[2:] if os.name=='nt' and len(value)>2 and value[1]==':' else value


@unittest.skipUnless(BASH and Path(BASH).is_file(),'POSIX shell unavailable')
class CurlVersionShellTests(unittest.TestCase):
    def run_shell(self,manager,body,db=''):
        with TemporaryDirectory() as d:
            root=Path(d);command=root/manager
            command.write_text('#!/bin/sh\n'+body+'\n',encoding='utf-8',newline='\n');command.chmod(0o755)
            database=root/'installed';database.write_text(db,encoding='utf-8',newline='\n')
            query=VERSION_QUERY.replace('/lib/apk/db/installed',shlex.quote(shell_path(database)))
            script='PATH='+shlex.quote(shell_path(root))+':/usr/bin:/bin\n'+query
            return subprocess.run([BASH,'--noprofile','--norc','-s'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=10)

    def test_opkg_records_are_filtered_and_versions_preserved(self):
        result=self.run_shell('opkg','printf "curl - 8.22.0-r1\\nlibcurl4 - 8.12.1-r2\\nsecret - 2\\n"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(parse_versions(result.stdout),{'curl':'8.22.0-r1','libcurl4':'8.12.1-r2'})
        self.assertNotIn('secret',result.stdout)

    def test_opkg_error_is_not_reported_as_empty_inventory(self):
        result=self.run_shell('opkg','exit 1')
        self.assertNotEqual(result.returncode,0)
        with self.assertRaises(ValueError):parse_versions(result.stdout)

    def test_apk_database_records_are_filtered(self):
        db='P:curl\nV:8.22.0-r1\n\nP:other\nV:1-r1\n\nP:libcurl4\nV:8.12.1-r2\n'
        result=self.run_shell('apk','exit 99',db)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(parse_versions(result.stdout),{'curl':'8.22.0-r1','libcurl4':'8.12.1-r2'})
        self.assertNotIn('other',result.stdout)
