"""Entry point for the packaged executable and for ``pythonw main.py``.

Running this file directly puts the project folder on ``sys.path``, so it works
from any working directory -- which is how Windows launches it at sign-in.
"""

import sys

from golwall.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
