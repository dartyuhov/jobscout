"""Process-safe browser workflow ownership for macOS and Linux.

Children inherit the *open lock description*, not a bypass token. If a parent
dies, an active CLI child continues holding ownership until its own exit.
Never unlink the lock file: waiters must always coordinate on the same inode.
"""

from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

from job_search_cli.runtime_paths import data_dir, private_file

LOCK_FD_ENV = "JOB_SEARCH_BROWSER_LOCK_FD"
_thread_lock = threading.RLock()
_active_fd: int | None = None
P = ParamSpec("P")
T = TypeVar("T")


class BrowserCoordinationError(ValueError):
    """Browser ownership cannot be acquired in private runtime storage."""


def inherited_browser_fds() -> tuple[int, ...]:
    """Only propagate a real descriptor for this application's lock file."""
    value = os.environ.get(LOCK_FD_ENV)
    if value is None:
        return ()
    try:
        fd = int(value)
        if fd < 3:
            return ()
        actual = os.fstat(fd)
        expected = (data_dir() / "runtime" / "browser.lock").stat()
        if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
            return (fd,)
    except (OSError, ValueError):
        pass
    return ()


@contextmanager
def browser_operation() -> Iterator[None]:
    """Queue browser work, reentrantly across a workflow's CLI children."""
    global _active_fd
    with _thread_lock:
        if _active_fd is not None:
            yield
            return
        inherited = inherited_browser_fds()
        path = data_dir() / "runtime" / "browser.lock"
        fd = None
        try:
            private_file(path)
            fd = inherited[0] if inherited else os.open(path, os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            if fd is not None and not inherited:
                os.close(fd)
            raise BrowserCoordinationError(
                f"Cannot acquire browser lock '{path}': {exc}. Check runtime directory permissions and retry."
            ) from exc
        previous = os.environ.get(LOCK_FD_ENV)
        try:
            _active_fd = fd
            os.environ[LOCK_FD_ENV] = str(fd)
            yield
        finally:
            _active_fd = None
            if previous is None:
                os.environ.pop(LOCK_FD_ENV, None)
            else:
                os.environ[LOCK_FD_ENV] = previous
            # Do not LOCK_UN: that would unlock a surviving inherited child too.
            if not inherited:
                os.close(fd)


def serialized_browser_operation(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            with browser_operation():
                return function(*args, **kwargs)
        except BrowserCoordinationError as exc:
            import click

            if click.get_current_context(silent=True) is not None:
                raise click.ClickException(str(exc)) from exc
            raise

    return wrapped
