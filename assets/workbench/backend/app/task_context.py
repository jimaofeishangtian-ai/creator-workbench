"""Thread-local cancellation checks shared by jobs and their provider calls."""
from contextlib import contextmanager
from contextvars import ContextVar

_guard = ContextVar("workbench_job_guard", default=None)
_metadata = ContextVar('workbench_job_metadata', default={})


def task_metadata():
    return _metadata.get()


def check_cancelled():
    callback = _guard.get()
    if callback:
        callback()


@contextmanager
def cancellation_scope(callback, metadata=None):
    token = _guard.set(callback)
    meta = _metadata.set(metadata or {})
    try:
        yield
    finally:
        _guard.reset(token)
        _metadata.reset(meta)
