"""Serve a Kev checkpoint on 0.0.0.0 (kev.serve binds 127.0.0.1, which a container port map cannot reach).

Same approach as the Spark's existing kev_launch.py, with the run taken from KEV_RUN instead of hard-coded:

    KEV_RUN=/work/runs/kafka-triage-v1/checkpoint python3 serve_launch.py
"""

import os
import sys

import uvicorn

_orig_run = uvicorn.run


def _run(app, **kw):  # type: ignore[no-untyped-def]
    kw["host"] = "0.0.0.0"  # noqa: S104  (inside a container; the host port map decides exposure)
    return _orig_run(app, **kw)


uvicorn.run = _run
sys.argv = ["kev.serve", "--run", os.environ["KEV_RUN"], "--port", os.environ.get("KEV_PORT", "8000")]
from kev.serve import main  # noqa: E402  (import after the patch)

main()
