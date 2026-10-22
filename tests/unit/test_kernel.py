import edda


def test_okf_version_comes_from_the_kernel() -> None:
    assert edda.okf_version() == "0.2"
