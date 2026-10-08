"""Run independent fits on several CPU cores.

The fits of different benchmark pairs (and the feature searches of different
multivariate targets) do not depend on each other, so they run in a process
pool; results come back in input order, and the caller stores them in that
order, so the database is the same whatever the number of jobs.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def ipmap(
    fn: Callable[[T], R],
    items: Iterable[T],
    jobs: Optional[int] = None,
    initializer: Optional[Callable] = None,
    initargs: tuple = (),
) -> Iterator[R]:
    """``fn(x) for x in items`` over ``jobs`` processes (none or 0: every core; 1: in this
    process), yielded in input order as the results come in."""
    items = list(items)
    workers = min(jobs if jobs and jobs > 0 else (os.cpu_count() or 1), len(items))
    if workers <= 1:
        if initializer is not None:
            initializer(*initargs)
        yield from map(fn, items)
        return
    with ProcessPoolExecutor(workers, initializer=initializer, initargs=initargs) as pool:
        yield from pool.map(fn, items, chunksize=max(1, len(items) // (workers * 8)))


def pmap(
    fn: Callable[[T], R],
    items: Iterable[T],
    jobs: Optional[int] = None,
    initializer: Optional[Callable] = None,
    initargs: tuple = (),
) -> list[R]:
    """``[fn(x) for x in items]`` over ``jobs`` processes (none or 0: every core; 1: in this process)."""
    return list(ipmap(fn, items, jobs, initializer, initargs))
