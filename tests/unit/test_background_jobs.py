import threading
import time

import pytest

from edda.jobs.background import Jobs, Progress
from edda.models import JobOutcome, JobRun

NOTHING = JobOutcome()


def test_runs_jobs_in_order_and_reports_each_state() -> None:
    heard: list[tuple[str, str]] = []
    jobs = Jobs(listener=lambda run: heard.append((run.id, run.state)))
    gate = threading.Event()

    def first(progress: Progress) -> JobOutcome:
        progress(1, 32)
        gate.wait(5)
        return JobOutcome(proposals=["prop_000001"], note="stopped at its limit of 32 model turns")

    one = jobs.start("ingest", "aaaa", first)
    two = jobs.start("ingest", "bbbb", lambda _: NOTHING)
    assert jobs.run(two.id) is not None
    assert [r.id for r in jobs.runs()] == ["run_0001", "run_0002"]
    gate.set()
    jobs.wait(5)

    done = jobs.run(one.id)
    assert isinstance(done, JobRun)
    assert (done.state, done.proposals, done.turn, done.max_turns) == ("done", ["prop_000001"], 1, 32)
    assert done.note == "stopped at its limit of 32 model turns"
    assert done.started is not None and done.finished is not None
    for run in (one, two):
        states = [state for run_id, state in heard if run_id == run.id]
        assert states[0] == "queued" and states[-1] == "done" and set(states) == {"queued", "running", "done"}
    assert jobs.run("run_9999") is None


def test_a_failing_job_is_recorded_not_lost() -> None:
    jobs = Jobs()

    def broken(_: Progress) -> JobOutcome:
        raise RuntimeError("model went away")

    def silent(_: Progress) -> JobOutcome:
        raise RuntimeError

    run = jobs.start("ingest", "aaaa", broken)
    other = jobs.start("ingest", "bbbb", silent)
    jobs.wait(5)
    failed = jobs.run(run.id)
    assert failed is not None
    assert (failed.state, failed.error) == ("failed", "model went away")
    assert jobs.run(other.id).error == "RuntimeError"  # type: ignore[union-attr]


def test_jobs_still_queued_at_shutdown_are_marked_failed() -> None:
    jobs = Jobs()
    gate = threading.Event()
    jobs.start("ingest", "aaaa", lambda _: JobOutcome(proposals=[str(gate.wait(5))]))
    queued = jobs.start("ingest", "bbbb", lambda _: NOTHING)
    stopping = threading.Thread(target=jobs.shutdown)
    stopping.start()
    deadline = time.monotonic() + 5
    while not jobs._futures[1].cancelled() and time.monotonic() < deadline:
        time.sleep(0.01)
    gate.set()
    stopping.join(5)
    run = jobs.run(queued.id)
    assert run is not None
    assert (run.state, run.error) == ("failed", "the server stopped before the job ran")


def test_expected_failures_are_one_log_line(capsys: pytest.CaptureFixture[str]) -> None:
    from edda.llm.errors import LLMError

    jobs = Jobs()

    def offline(_: Progress) -> JobOutcome:
        raise LLMError("http://127.0.0.1:8080/v1/chat/completions did not answer; is the model server running?")

    run = jobs.start("ingest", "aaaa", offline)
    jobs.wait(5)
    failed = jobs.run(run.id)
    assert failed is not None and failed.state == "failed"
    out = capsys.readouterr().out
    assert "is the model server running?" in out
    assert "Traceback" not in out
