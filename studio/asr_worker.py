"""The old way to start the speech machine: `python -m studio.asr_worker`. The new way is `python -m anhy.asr_worker`."""
from anhy.asr_worker import *  # noqa: F401,F403 - so `from studio.asr_worker import …` keeps working too
from anhy.asr_worker import main

if __name__ == "__main__":
    main()
