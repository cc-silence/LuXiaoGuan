"""Execute the actual remote command template without desktop GUI dependencies."""
import ast
import unittest

class NativeUsageProbeTests(unittest.TestCase):
    def test_real_shell_checks_down_unassociated_carrier_and_owned_placeholder(self):
        import os,shlex,subprocess,tempfile
        from pathlib import Path
        tree=ast.parse((Path(__file__).resolve().parents[1]/'modules/capture_services/openwrt/radio_usage.py').read_text(encoding='utf-8'))
        RUNTIME_PROBE=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                           and any(isinstance(t,ast.Name) and t.id=='RUNTIME_PROBE' for t in n.targets))
        bash=Path('C:/Program Files/Git/bin/bash.exe') if os.name=='nt' else Path('/bin/sh')
        if not bash.is_file():self.skipTest('POSIX shell unavailable')
        with tempfile.TemporaryDirectory(prefix='usage-probe-') as folder:
            root=Path(folder);(root/'phys/phy2').mkdir(parents=True)
            for name,flags,carrier,alias in (('down','0x1002','0',''),('sta','0x1003','0',''),('ap','0x1003','1',''),('hcxstub2','0x1003','0','routermanager:autocapture-stub'),('other','0x1003','0','')):
                path=root/'net'/name;(path/'phy80211').mkdir(parents=True)
                for filename,value in (('phy80211/name','phy2'),('flags',flags),('carrier',carrier),('ifalias',alias)):
                    (path/filename).write_text(value+'\n',encoding='ascii')
            native=root.as_posix();posix='/'+native[0].lower()+native[2:] if os.name=='nt' else native
            command=RUNTIME_PROBE.replace('/sys/class/ieee80211',shlex.quote(posix+'/phys')).replace('/sys/class/net',shlex.quote(posix+'/net'))
            setup=r"""iw(){
 if [ "$3" = link ]; then printf 'Not connected.\n'; return; fi
 case "$2" in sta|down) mode=managed;; hcxstub2|other) mode=monitor;; *) mode=AP;; esac
 printf 'Interface %s\n wiphy 2\n type %s\n' "$2" "$mode"
}
"""
            result=subprocess.run([str(bash)],input=setup+command,text=True,capture_output=True,encoding='utf-8',timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('I|phy2|down|managed|0|0',result.stdout)
            self.assertIn('I|phy2|sta|managed|1|0',result.stdout)
            self.assertIn('I|phy2|ap|AP|1|1',result.stdout)
            self.assertNotIn('hcxstub2',result.stdout)
            self.assertIn('I|phy2|other|monitor|1|0',result.stdout)
