import sys

import autobotvideo
from db_retry import run_with_retry

_original_db_execute = autobotvideo.db_execute


def _retrying_db_execute(query, params=()):
    return run_with_retry(lambda: _original_db_execute(query, params))


autobotvideo.db_execute = _retrying_db_execute


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Vui lòng truyền action")
    autobotvideo.main()
