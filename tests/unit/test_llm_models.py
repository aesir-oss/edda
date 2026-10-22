import hashlib
from pathlib import Path

import httpx
import pytest

from edda.llm import models
from edda.llm.models import FetchError, KnownModel, fetch, main

PAYLOAD = b"gguf weights"


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> KnownModel:
    model = KnownModel(
        name="tiny",
        file="tiny.gguf",
        url="https://example.test/tiny.gguf",
        size=len(PAYLOAD),
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        license="MIT",
    )
    monkeypatch.setitem(models.KNOWN, "tiny", model)
    return model


def _client(body: bytes, status: int = 200) -> tuple[httpx.Client, list[str]]:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


def test_downloads_verifies_and_reuses(tmp_path: Path, fake_model: KnownModel) -> None:
    client, seen = _client(PAYLOAD)
    path = fetch("tiny", tmp_path, client)
    assert path.read_bytes() == PAYLOAD
    assert not (tmp_path / "tiny.gguf.part").exists()
    assert fetch("tiny", tmp_path, client) == path
    assert seen == [fake_model.url], "a verified copy is not downloaded again"


def test_refuses_tampered_failed_and_unknown_downloads(tmp_path: Path, fake_model: KnownModel) -> None:
    with pytest.raises(FetchError, match="not the pinned"):
        fetch("tiny", tmp_path, _client(b"something else")[0])
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(FetchError, match="failed"):
        fetch("tiny", tmp_path, _client(b"", status=404)[0])
    with pytest.raises(FetchError, match="unknown model"):
        fetch("nope", tmp_path)
    (tmp_path / "tiny.gguf").write_bytes(b"stale")
    assert fetch("tiny", tmp_path, _client(PAYLOAD)[0]).read_bytes() == PAYLOAD


def test_the_known_models_are_pinned() -> None:
    for model in models.KNOWN.values():
        assert model.url.startswith("https://huggingface.co/")
        assert len(model.sha256) == 64 and model.size > 0


def test_main_reports_usage_and_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_model: KnownModel
) -> None:
    assert main([]) == 2
    assert main(["nope"]) == 1
    monkeypatch.setattr(models, "fetch", lambda name: tmp_path)
    assert main(["tiny"]) == 0
