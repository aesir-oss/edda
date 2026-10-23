import threading
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.jobs.background import Progress
from edda.kernel import ConflictError, Kernel
from edda.models import EditChange, FlagChange, JobOutcome, JournalEntry, Proposal, ProposalPreview, Source, TextEdit

HASH = "ab12" + "0" * 60


def _proposal(id: str, status: str = "pending") -> Proposal:
    change = EditChange(kind="edit", page="a.md", base_rev=1, edits=[TextEdit(old="x", new="y")])
    return Proposal.model_validate(
        {
            "id": id,
            "job": "ingest:test",
            "created": "2026-10-22T00:00:00Z",
            "change": change.model_dump(),
            "rationale": "r",
            "status": status,
            "preview": ProposalPreview(diff="-x\n+y\n", issues=[]),
        }
    )


def _entry(action: str) -> JournalEntry:
    return JournalEntry(seq=9, at="2026-10-22T00:00:00Z", actor="owner", action=action, summary="s", prev="0", hash="h")


@pytest.fixture
def kernel() -> MagicMock:
    fake = MagicMock(spec=Kernel)
    fake.writes = threading.RLock()
    fake.proposals.return_value = [_proposal("prop_000001"), _proposal("prop_000002", "accepted")]
    fake.proposal.side_effect = lambda id: _proposal(id, "accepted")
    fake.read_source.return_value = Source(hash=HASH, text="hello")
    return fake


def test_lists_filters_and_reads_proposals(kernel: MagicMock) -> None:
    client = TestClient(create_app(kernel=kernel))
    assert [p["id"] for p in client.get("/api/proposals").json()] == ["prop_000001", "prop_000002"]
    assert [p["id"] for p in client.get("/api/proposals?status=pending").json()] == ["prop_000001"]
    assert client.get("/api/proposals/prop_000003").json()["status"] == "accepted"


def test_accepts_and_rejects(kernel: MagicMock) -> None:
    client = TestClient(create_app(kernel=kernel))
    kernel.accept.return_value = _entry("edit_page")
    accepted = client.post("/api/proposals/prop_000001/accept")
    assert accepted.status_code == 200
    assert accepted.json()["entry"]["action"] == "edit_page"
    kernel.accept.assert_called_once_with("prop_000001", "owner")

    kernel.reject.return_value = _entry("reject_proposal")
    rejected = client.post("/api/proposals/prop_000001/reject", json={"reason": "wrong"})
    assert rejected.json()["entry"]["action"] == "reject_proposal"
    kernel.reject.assert_called_once_with("prop_000001", "wrong", "owner")
    assert client.post("/api/proposals/prop_000001/reject", json={"reason": ""}).status_code == 422

    kernel.accept.side_effect = ConflictError("page a.md is at revision 2, not 1")
    conflict = client.post("/api/proposals/prop_000001/accept", json={})
    assert (conflict.status_code, conflict.json()["detail"]) == (409, "page a.md is at revision 2, not 1")


def test_a_flag_cannot_be_revised(kernel: MagicMock) -> None:
    flag = _proposal("prop_000001").model_copy(
        update={"change": FlagChange(kind="flag", page="a.md", flag="gap", note="n")}
    )
    kernel.proposal.side_effect = None
    kernel.proposal.return_value = flag
    client = TestClient(create_app(kernel=kernel))
    response = client.post("/api/proposals/prop_000001/accept", json={"edits": [{"old": "a", "new": "b"}]})
    assert response.status_code == 400
    assert "is a flag" in response.json()["detail"]


def test_starts_ingest_jobs_and_reports_them(kernel: MagicMock) -> None:
    seen: list[str] = []

    def ingest(_: Kernel, source: str, progress: Progress) -> JobOutcome:
        seen.append(source)
        progress(3, 32)
        return JobOutcome(proposals=["prop_000003"])

    app = create_app(kernel=kernel, ingest=ingest)
    client = TestClient(app)
    started = client.post("/api/jobs/ingest", json={"source": "ab12"})
    assert started.status_code == 202
    run = started.json()
    assert (run["job"], run["target"]) == ("ingest", HASH)
    app.state.jobs.wait(5)
    assert seen == [HASH]
    finished = client.get(f"/api/jobs/{run['id']}").json()
    assert (finished["proposals"], finished["turn"], finished["max_turns"]) == (["prop_000003"], 3, 32)
    assert [r["id"] for r in client.get("/api/jobs").json()] == [run["id"]]
    assert client.get("/api/jobs/run_9999").status_code == 404
    assert client.post("/api/jobs/ingest", json={"source": ""}).status_code == 422


def test_the_event_stream_starts_with_ready_and_ends_on_shutdown(kernel: MagicMock) -> None:
    app = create_app(kernel=kernel)
    kernel.listen.assert_called_once_with(app.state.events.publish)
    app.state.events.close()
    with TestClient(app) as client, client.stream("GET", "/api/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert body.startswith("event: ready\ndata: {}")


def test_accepts_several_proposals_together(kernel: MagicMock) -> None:
    kernel.accept_together.return_value = [
        _entry("create_page").model_copy(update={"data": {"proposal": "prop_000002"}}),
        _entry("create_page").model_copy(update={"data": {"proposal": "prop_000001"}}),
    ]
    client = TestClient(create_app(kernel=kernel))
    response = client.post("/api/proposals/accept-together", json={"ids": ["prop_000001", "prop_000002"]})
    assert response.status_code == 200
    assert [d["proposal"]["id"] for d in response.json()] == ["prop_000002", "prop_000001"]
    kernel.accept_together.assert_called_once_with(["prop_000001", "prop_000002"], "owner")
    assert client.post("/api/proposals/accept-together", json={"ids": ["prop_000001"]}).status_code == 422
