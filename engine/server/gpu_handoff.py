"""Cross-process admission shared by Storyboard and its Fable launcher."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

LOCK = Path(os.environ.get(
    "RAVEN_GPU_LOCK",
    str(Path(__file__).resolve().parents[1] / "local/.gpu-admission.lock"),
)).expanduser()


@contextmanager
def gpu_admission():
    lock = Path(os.environ.get("RAVEN_GPU_LOCK", str(LOCK))).expanduser()
    lock.parent.mkdir(parents=True, exist_ok=True)
    # Separate open descriptions also serialize separate editor threads.
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
