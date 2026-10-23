"""\u8def\u7531\u5668\u7ba1\u7406\u72ec\u7acb\u5165\u53e3\uff1b\u5148\u5206\u6d41\u5de5\u4f5c\u8fdb\u7a0b\uff0c\u518d\u542f\u52a8\u754c\u9762\u3002"""
import sys
APP_VERSION = "1.0.12_alpha"


def main():
    """\u542f\u52a8\u72ec\u7acb\u5e94\u7528\uff0c\u8fd4\u56de\u8fdb\u7a0b\u9000\u51fa\u7801\u3002"""
    from multiprocessing import freeze_support
    freeze_support()
    if "--firewall-session" in sys.argv:
        from modules.platform_services.firewall_session_worker import run_worker
        return run_worker(sys.argv)
    if "--local-gpo-firewall" in sys.argv:
        from modules.platform_services.local_gpo_firewall import run_request_file
        return run_request_file(sys.argv)
    if "--hidden-command-worker" in sys.argv:
        from modules.platform_services.elevated_background_command import run_worker
        return run_worker(sys.argv)
    if "--smoke-test-ssh-dependencies" in sys.argv:
        import paramiko, bcrypt, cryptography, nacl, cffi, invoke
        return 0
    from router_app.bootstrap import run
    return run(APP_VERSION)


if __name__ == "__main__":
    raise SystemExit(main())
