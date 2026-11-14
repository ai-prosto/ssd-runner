"""BEH-21: код возврата и команда запуска тестов сохраняются в логе."""


def test_attempt_records_returncode_and_command(tmp_path, run_logged):
    config, _, events, _ = run_logged(tmp_path, [3, 0, 0])
    finished = [e for e in events if e["event"] == "attempt_finished"]
    assert [a["tests_returncode"] for a in finished][:2] == [3, 0]
    assert all(a["tests_command"] == config.test_command for a in finished)
    steps = [
        e for e in events if e["event"] == "step_finished" and e["stage"] == "tests"
    ]
    assert [s["returncode"] for s in steps] == [3, 0, 0]
    assert all(s["command"] == config.test_command for s in steps)


def test_returncode_not_derived_from_model_text(tmp_path, run_logged):
    _, _, events, result = run_logged(tmp_path, [7, 7], max_attempts=2)
    assert [a.success for a in result.attempts[:2]] == [False, False]
    tests = [e for e in events if e.get("stage") == "tests"]
    assert [t["returncode"] for t in tests][:2] == [7, 7]
