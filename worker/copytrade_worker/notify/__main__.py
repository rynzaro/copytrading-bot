"""Run the notifier alone: python -m copytrade_worker.notify <sqlite path>.
The full worker (`python -m copytrade_worker`) runs it as a thread."""

import logging
import sys

from .. import db
from .config import Config
from .loop import Notifier


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -m copytrade_worker.notify <db path>")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = db.connect(argv[0])
    Notifier(conn, Config.from_env()).run_forever()


if __name__ == "__main__":
    main()
