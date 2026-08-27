import sys

import autobot
import runner
from db_retry import run_with_retry

_original_execute_db = autobot.execute_db


def _retrying_execute_db(query, params=()):
    return run_with_retry(lambda: _original_execute_db(query, params))


autobot.execute_db = _retrying_execute_db


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Vui lòng truyền action")
    runner.main()
