"""Portable executable dispatch; child workers never enter the interactive flow."""
import sys


def main():
    if "--probe-worker" in sys.argv[1:]:
        from scripts.slow_network_probe.worker import main as worker_main
        worker_main()
        return 0
    if "--self-test" in sys.argv[1:]:
        from scripts.slow_network_probe.frozen_smoke import main as self_test
        return self_test()
    from scripts.slow_network_probe.console import customer_main
    return customer_main(automated="--automated" in sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
