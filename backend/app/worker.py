"""Dedicated scheduler and audit consumer process (no HTTP server)."""

import logging
import signal
import threading

from app.main import on_shutdown, on_startup


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        on_startup()
        stop.wait()
    finally:
        on_shutdown()


if __name__ == '__main__':
    main()
