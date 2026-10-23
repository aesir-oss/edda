"""Jobs started from the API, run one at a time on a worker thread so the server keeps answering."""

import itertools
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime

import structlog

from edda.config import ConfigError
from edda.drivers.errors import DriverError
from edda.kernel import KernelError
from edda.llm.errors import LLMError
from edda.models import JobRun

log = structlog.get_logger("edda.jobs.background")

# Failures whose cause is outside the code, such as a model server that is not running: logged as one line.
EXPECTED = (LLMError, KernelError, ConfigError, DriverError)

Work = Callable[[], list[str]]
Listener = Callable[[JobRun], None]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Jobs:
    """Jobs in the order they were started, run one after another: a local model serves one request at a time."""

    def __init__(self, listener: Listener | None = None) -> None:
        """``listener`` hears about every change to a job's state."""
        self._listener = listener
        self._lock = threading.Lock()
        self._runs: dict[str, JobRun] = {}
        self._ids = itertools.count(1)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edda-job")
        self._futures: list[Future[None]] = []

    def start(self, job: str, target: str, work: Work) -> JobRun:
        """Queues ``work``, which returns the ids of the proposals it made."""
        with self._lock:
            run = JobRun(id=f"run_{next(self._ids):04d}", job=job, target=target, state="queued", queued=_now())
            self._runs[run.id] = run
        self._changed(run)
        self._futures.append(self._pool.submit(self._run, run.id, work))
        return run

    def runs(self) -> list[JobRun]:
        """Every job, oldest first."""
        with self._lock:
            return list(self._runs.values())

    def run(self, run_id: str) -> JobRun | None:
        """One job, or None."""
        with self._lock:
            return self._runs.get(run_id)

    def wait(self, timeout: float | None = None) -> None:
        """Waits until every job started so far has finished."""
        wait(list(self._futures), timeout=timeout)

    def shutdown(self) -> None:
        """Waits for the running job; queued ones are marked failed without running."""
        self._pool.shutdown(wait=True, cancel_futures=True)
        for run in self.runs():
            if run.state == "queued":
                self._update(run.id, state="failed", finished=_now(), error="the server stopped before the job ran")

    def _run(self, run_id: str, work: Work) -> None:
        self._update(run_id, state="running", started=_now())
        try:
            proposals = work()
        except EXPECTED as exc:
            log.error("job_failed", run=run_id, error=str(exc))
            self._update(run_id, state="failed", finished=_now(), error=str(exc) or type(exc).__name__)
            return
        except Exception as exc:
            log.exception("job_failed", run=run_id)
            self._update(run_id, state="failed", finished=_now(), error=str(exc) or type(exc).__name__)
            return
        self._update(run_id, state="done", finished=_now(), proposals=proposals)

    def _update(self, run_id: str, **changes: object) -> None:
        with self._lock:
            run = self._runs[run_id].model_copy(update=changes)
            self._runs[run_id] = run
        self._changed(run)

    def _changed(self, run: JobRun) -> None:
        if self._listener is not None:
            self._listener(run)
