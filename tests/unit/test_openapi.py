import json

import pytest

from edda.api.openapi import main, schema_json


def test_schema_documents_every_route() -> None:
    schema = json.loads(schema_json())
    assert schema["info"]["title"] == "Edda"
    assert {"/api/pages", "/api/pages/{page_id}", "/api/search", "/api/sources/{source_hash}"} <= set(schema["paths"])
    assert "from" in schema["components"]["schemas"]["BrokenLink"]["properties"]


def test_main_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["openapi"].startswith("3.")
