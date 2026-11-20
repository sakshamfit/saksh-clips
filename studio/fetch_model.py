"""The old way to fetch the speech model: `python -m studio.fetch_model`. The new way is `python -m anhy.fetch_model`."""
from anhy.fetch_model import *  # noqa: F401,F403 - so `from studio.fetch_model import …` keeps working too
from anhy.fetch_model import main

if __name__ == "__main__":
    main()
