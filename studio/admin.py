"""The old way to make or remove an admin: `python -m studio.admin grant you@example.com`. The new way is `python -m anhy.admin grant you@example.com`."""
from anhy.admin import *  # noqa: F401,F403 - so `from studio.admin import …` keeps working too
from anhy.admin import main

if __name__ == "__main__":
    raise SystemExit(main())
