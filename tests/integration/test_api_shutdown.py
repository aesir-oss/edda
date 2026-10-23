import re
import socket
import threading
import time
import tomllib
from pathlib import Path

import httpx
import pytest
import uvicorn

from edda.api import create_app

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def dev_graceful_timeout() -> int:
    """The graceful-shutdown limit `pixi run dev` gives uvicorn."""
    dev = tomllib.loads((REPO_ROOT / "pixi.toml").read_text(encoding="utf-8"))["feature"]["dev"]["tasks"]["dev"]
    match = re.search(r"--timeout-graceful-shutdown (\d+)", dev["cmd"])
    assert match, "pixi run dev must limit graceful shutdown, or an open event stream blocks every reload (#77)"
    return int(match.group(1))


def test_the_dev_server_stops_while_a_browser_holds_the_event_stream(vault_copy: Path) -> None:
    timeout = dev_graceful_timeout()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(
        create_app(vault=vault_copy), port=port, log_level="critical", timeout_graceful_shutdown=timeout
    )
    server = uvicorn.Server(config)
    running = threading.Thread(target=server.run, daemon=True)
    running.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started

    ready = threading.Event()
    cut = threading.Event()

    def browser() -> None:
        try:
            with httpx.stream("GET", f"http://127.0.0.1:{port}/api/events", timeout=30) as response:
                for line in response.iter_lines():
                    if line.startswith("event: ready"):
                        ready.set()
        except httpx.RemoteProtocolError:
            cut.set()

    threading.Thread(target=browser, daemon=True).start()
    assert ready.wait(5)

    began = time.monotonic()
    server.should_exit = True
    running.join(timeout + 5)
    assert not running.is_alive(), "the server waited for the event stream instead of stopping"
    assert time.monotonic() - began < timeout + 5
    assert cut.wait(5), "the stream should have been cut, which the app answers by reconnecting"
