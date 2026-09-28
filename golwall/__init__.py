"""Conway's Game of Life as a live, drawable Windows wallpaper."""

import os as _os

# NumPy's bundled OpenBLAS starts a worker per core and commits a large buffer
# for each as soon as numpy is imported -- about 350 MB on a 12-thread CPU --
# for linear algebra this program never does.  It must be set before the first
# numpy import, which is why it lives in the package's first line of code.
for _name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    _os.environ.setdefault(_name, "1")

__version__ = "2.0.0"
