"""The old way to start the server: `python -m studio.server`. The new way is `python -m anhy.server`."""
from anhy.server import *  # noqa: F401,F403 - so `from studio.server import …` keeps working too
from anhy.server import main

if __name__ == "__main__":
    main()
