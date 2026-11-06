"""Run: python -m copytrade_worker.notify <sqlite path>. Schema is applied by copytrade_worker.db (0005)."""

import logging
import sqlite3
import sys

from .config import Config
from .loop import Notifier


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -m copytrade_worker.notify <db path>")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = sqlite3.connect(argv[0], timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    Notifier(conn, Config.from_env()).run_forever()


if __name__ == "__main__":
    main()
