import pytest

from sprc_runner import main


def test_main_prints_name(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert capsys.readouterr().out.strip() == "sprc-runner"
