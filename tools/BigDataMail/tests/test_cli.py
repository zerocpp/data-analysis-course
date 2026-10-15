from __future__ import annotations

from mailgain.cli import main


def test_help_exits_successfully(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "Collect and audit CodeSetArena email submissions." in capsys.readouterr().out


def test_collect_email_help_exits_successfully(capsys):
    try:
        main(["teacher", "stage1", "collect-email", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "--download" in output
    assert "default mode" in output
    assert "imap.163.com" in output
    assert "CODESETARENA_163_AUTH_CODE" in output


def test_collect_email_defaults_to_dry_run(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_collect(config):
        seen["config"] = config
        return []

    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.cli.collect_stage1_email", fake_collect)

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--username",
            "teacher@example.com",
            "--password-env",
            "MAILGAIN_TEST_PASSWORD",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert seen["config"].dry_run is True
    assert seen["config"].imap_host == "imap.163.com"
    assert seen["config"].imap_port == 993
    assert seen["config"].timeout == 30
    assert seen["config"].duplicate_policy == "preserve-review"
    assert seen["config"].command_args["password_env"] == "MAILGAIN_TEST_PASSWORD"
    assert "secret" not in str(seen["config"].command_args)
    assert "wrote reports" in capsys.readouterr().out


def test_collect_email_download_flag_disables_dry_run(monkeypatch, tmp_path):
    seen = {}

    def fake_collect(config):
        seen["config"] = config
        return []

    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.cli.collect_stage1_email", fake_collect)

    assert (
        main(
            [
                "teacher",
                "stage1",
                "collect-email",
                "--username",
                "teacher@example.com",
                "--password-env",
                "MAILGAIN_TEST_PASSWORD",
                "--output-dir",
                str(tmp_path),
                "--download",
            ]
        )
        == 0
    )
    assert seen["config"].dry_run is False


def test_collect_email_accepts_host_port_aliases_timeout_and_report_paths(monkeypatch, tmp_path):
    seen = {}

    def fake_collect(config):
        seen["config"] = config
        return []

    monkeypatch.setenv("CODESETARENA_163_USERNAME", "teacher@163.com")
    monkeypatch.setenv("CODESETARENA_163_AUTH_CODE", "secret")
    monkeypatch.setattr("mailgain.cli.collect_stage1_email", fake_collect)

    assert (
        main(
            [
                "teacher",
                "stage1",
                "collect-email",
                "--host",
                "imap.163.com",
                "--port",
                "993",
                "--timeout",
                "11",
                "--output-dir",
                str(tmp_path / "downloads"),
                "--report-json",
                str(tmp_path / "report.json"),
                "--report-csv",
                str(tmp_path / "report.csv"),
            ]
        )
        == 0
    )
    assert seen["config"].username == "teacher@163.com"
    assert seen["config"].timeout == 11
    assert seen["config"].report_json == tmp_path / "report.json"
    assert seen["config"].report_csv == tmp_path / "report.csv"


def test_collect_email_missing_credentials_is_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CODESETARENA_163_USERNAME", raising=False)
    monkeypatch.delenv("CODESETARENA_163_AUTH_CODE", raising=False)

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--host",
            "imap.163.com",
            "--port",
            "993",
            "--output-dir",
            str(tmp_path / "downloads"),
        ]
    )

    assert result == 2
    captured = capsys.readouterr()
    assert "missing username" in captured.err
    assert "secret" not in captured.err


def test_collect_email_missing_password_env_does_not_print_value(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MAILGAIN_TEST_PASSWORD", raising=False)

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--username",
            "teacher@example.com",
            "--password-env",
            "MAILGAIN_TEST_PASSWORD",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 2
    assert "MAILGAIN_TEST_PASSWORD" in capsys.readouterr().err


def test_collect_email_rejects_invalid_student_regex(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--username",
            "teacher@example.com",
            "--password-env",
            "MAILGAIN_TEST_PASSWORD",
            "--output-dir",
            str(tmp_path),
            "--student-id-core-regex",
            "[",
        ]
    )

    assert result == 2
    assert "invalid --student-id-core-regex" in capsys.readouterr().err


def test_collect_email_rejects_invalid_since(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--username",
            "teacher@example.com",
            "--password-env",
            "MAILGAIN_TEST_PASSWORD",
            "--output-dir",
            str(tmp_path),
            "--since",
            "2026/06/01",
        ]
    )

    assert result == 2
    assert "invalid --since" in capsys.readouterr().err
