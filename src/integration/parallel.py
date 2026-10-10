"""Run independent studies (fleets, groups of candidate buses) in parallel processes, one CPU core each.

The power flows run on the CPU only (no GPU). Each process is limited to one math-library thread
(OMP/OpenBLAS/MKL), which measured ~2.3x faster than letting the processes compete for cores
(5 tasks on a 6-core Ryzen 5 3600: 96 s instead of 225 s in sequence).

Number of processes: environment variable EVCS_WORKERS if set, otherwise the physical cores
(logical CPUs / 2) minus one, at least 1, and never more than the number of tasks.
"""
import os
from concurrent.futures import ProcessPoolExecutor

SINGLE_THREAD = ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS")


def default_workers(tasks):
    """Processes to use for `tasks` independent jobs on this machine."""
    if os.environ.get("EVCS_WORKERS"):
        workers = int(os.environ["EVCS_WORKERS"])
    else:
        physical = max(1,(os.cpu_count() or 2)//2)
        workers = max(1,physical-1)  # keep one core for the system
    return max(1,min(workers,tasks))


def run_parallel(function, tasks, workers=None):
    """function(*task) for every task, results in the same order. workers=None: default_workers;
    1 (or a single task) runs in this process, in sequence."""
    tasks = list(tasks)
    workers = default_workers(len(tasks)) if workers is None else max(1,min(workers,len(tasks)))
    if workers <= 1:
        return [function(*task) for task in tasks]
    saved = {k: os.environ.get(k) for k in SINGLE_THREAD}
    os.environ.update({k: "1" for k in SINGLE_THREAD})  # inherited by the worker processes
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(function,*task) for task in tasks]
            return [f.result() for f in futures]
    finally:
        for k,v in saved.items():
            if v is None:
                os.environ.pop(k,None)
            else:
                os.environ[k] = v
