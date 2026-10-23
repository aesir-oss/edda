import stat
from pathlib import Path

from edda.llm.localkey import ensure_key, main, read_key, resolve


def test_resolves_home_from_the_given_environment() -> None:
    assert resolve("~/.cache/edda/llm-key", {"HOME": "/h"}) == Path("/h/.cache/edda/llm-key")
    assert resolve("~", {"HOME": "/h"}) == Path("/h")
    assert resolve("/abs/key", {"HOME": "/h"}) == Path("/abs/key")
    assert resolve("~/key", {}) == Path("~/key")


def test_creates_a_private_key_once(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "llm-key"
    assert read_key(path) is None
    assert ensure_key(path) is True
    key = read_key(path)
    assert key is not None and len(key) >= 40
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_key(path) is False
    assert read_key(path) == key


def test_reads_the_first_key_past_comments_and_replaces_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "llm-key"
    path.write_text("# mine\n\n  secret-one  \nsecret-two\n")
    assert read_key(path) == "secret-one"
    path.write_text("# nothing yet\n")
    assert ensure_key(path) is True
    assert read_key(path) not in (None, "")


def test_main_creates_the_default_key_under_home(tmp_path: Path, home: Path) -> None:
    assert main([]) == 0
    assert read_key(home / ".cache" / "edda" / "llm-key")
    assert main([str(tmp_path / "other")]) == 0
    assert read_key(tmp_path / "other")
