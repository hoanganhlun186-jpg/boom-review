"""Bounded background work; results stay in input order."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

def worker_count(name, default):
    try:
        return max(1,min(4,int(os.getenv(name,str(default)))))
    except (TypeError,ValueError):
        return default

def ordered_parallel(function, jobs, workers=2, progress=None):
    jobs = list(jobs)
    results = [None]*len(jobs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(function,job):index for index,job in enumerate(jobs)}
        completed = 0
        try:
            for future in as_completed(pending):
                results[pending[future]] = future.result()
                completed += 1
                if progress: progress(completed,len(jobs))
        except BaseException:
            for future in pending: future.cancel()
            raise
    return results
