"""Retained terminal history uses the tmux control channel."""
import subprocess
import unittest
from tests.posix_shell import local_shell
from modules.capture_services.terminal_scroll import scroll_command, parse_scroll_output


class TerminalScrollTests(unittest.TestCase):
    def run_command(self, target=None, mode="normal"):
        shell=local_shell()
        self.assertIsNotNone(shell)
        state="100|0||" if mode=="normal" else "100|1|20|"+mode
        stub="""
tmux() {
 case "$*" in
  *pane_id*) printf '%%7\\n';;
  *history_size*) printf '%s\\n' '__STATE__';;
  *copy-mode*) printf 'ACTION:copy\\n' >&2;;
  *send-keys*) printf 'ACTION:%s\\n' "$*" >&2;;
  *) return 1;;
 esac
}
""".replace("__STATE__",state)
        return subprocess.run([str(shell),"-c",stub+scroll_command(target)],capture_output=True,timeout=8)

    def test_read_position_and_bounds(self):
        self.assertEqual(parse_scroll_output("SCROLL:100|1|25|copy-mode\n"),(100,25))
        self.assertEqual(parse_scroll_output("SCROLL:100|0||\n"),(100,0))
        for text in ("SCROLL:3|1|9|copy-mode", "SCROLL:2|1|1|other", "SCROLL:bad|0||"):
            with self.assertRaises(ValueError):parse_scroll_output(text)
        for value in (-1,10001,"1"):
            with self.assertRaises(ValueError):scroll_command(value)

    def test_actual_shell_only_scrolls_tmux_control_mode(self):
        result=self.run_command(5000)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b"ACTION:copy",result.stderr)
        self.assertIn(b"scroll-up",result.stderr)
        self.assertEqual(parse_scroll_output(result.stdout.decode()),(100,0))
        self.assertNotIn(b"terminal input",result.stderr)

    def test_other_tmux_mode_is_not_modified(self):
        result=self.run_command(5000,"choose-tree")
        self.assertEqual(result.returncode,45)
        self.assertNotIn(b"ACTION:",result.stderr)

    def test_old_session_cancellation_preserves_new_session_refresh(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from modules.capture_services.terminal_scroll import TerminalScroll
        connection=object()
        service=SimpleNamespace(state="connected",session_mode="persistent",worker=object(),
            ssh=SimpleNamespace(_client=connection,connected=True),
            owner=SimpleNamespace(openwrt_backend=SimpleNamespace(command_runner=object())))
        jobs=[]
        def start(owner, token, operation, cancel, complete, fail):
            jobs.append((cancel,complete,fail))
        with patch("modules.capture_services.task_completion.start_capture_task",side_effect=start):
            controller=TerminalScroll(service)
            controller.request()
            self.assertEqual(len(jobs),1)
            controller.invalidate()
            self.assertTrue(jobs[0][0].is_set())
            service.worker=object()
            controller.request()
            jobs[0][2]("cancelled","")
            self.assertEqual(len(jobs),2)
            controller.dispose()
            self.assertTrue(jobs[1][0].is_set())

    def test_bottom_exits_copy_mode(self):
        result=self.run_command(10000,"copy-mode")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b"-X cancel",result.stderr)


if __name__=="__main__": unittest.main()
