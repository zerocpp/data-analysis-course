from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from mailgain.stage1_email import (
    STAGE1_HEADER_STRUCTURE_QUERY,
    AttachmentMeta,
    CollectorConfig,
    ContentTransferEncodingError,
    IMAPConnectFailedError,
    IMAPIdFailedError,
    MessageMeta,
    collect_stage1_email,
    connect_imap,
    decode_content_transfer_encoding,
    evaluate_message,
    extract_student_id_from_subject,
    fetch_attachment_part,
    match_stage1_attachment_filename,
    parse_bodystructure_attachments,
    safe_uid_fetch,
    validate_tar_gz,
    write_reports,
)

VALID_NAME = "bigdata_p1_20260000001.tar.gz"
OLD_FORMAT_NAME = "20260000001-student-stage1-problems.tar.gz"


def config(
    tmp_path: Path, *, dry_run: bool = True, duplicate_policy: str = "manual"
) -> CollectorConfig:
    return CollectorConfig(
        provider="163",
        imap_host="imap.163.com",
        imap_port=993,
        username="teacher@163.com",
        password_env="MAILGAIN_TEST_PASSWORD",
        mailbox="INBOX",
        output_dir=tmp_path,
        dry_run=dry_run,
        duplicate_policy=duplicate_policy,
        command_args={"password_env": "MAILGAIN_TEST_PASSWORD", "dry_run": dry_run},
    )


def meta(subject: str = "CodeSetArena Stage1 20260000001") -> MessageMeta:
    return MessageMeta(
        uid="101",
        message_id="<m101@example.com>",
        from_addr="student@example.com",
        date="Mon, 01 Jun 2026 10:00:00 +0000",
        subject=subject,
    )


def attachment(filename: str = VALID_NAME, part_id: str = "2") -> AttachmentMeta:
    return AttachmentMeta(part_id=part_id, filename=filename)


def tar_gz_bytes(members: list[tarfile.TarInfo] | None = None) -> bytes:
    file_obj = io.BytesIO()
    with tarfile.open(fileobj=file_obj, mode="w:gz") as tar:
        if members is None:
            payload = b"stage1"
            info = tarfile.TarInfo("problem.txt")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        else:
            for info in members:
                payload = b"x" if info.isfile() else b""
                if info.isfile():
                    info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload))
                else:
                    tar.addfile(info)
    return file_obj.getvalue()


def test_correct_subject_and_attachment_allows_download_plan(tmp_path):
    rows, plan = evaluate_message(meta(), [attachment()], config(tmp_path, dry_run=False))

    assert plan is not None
    assert plan.attachment.part_id == "2"
    assert rows[-1].action == "downloaded"
    assert rows[-1].reason == "downloaded"
    assert rows[-1].saved_path.endswith(f"20260000001/{VALID_NAME}")


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        " bigdata_p1_20260000001.tar.gz",
        "bigdata_p1_20260000001.tar.gz ",
        "bigdata_p1_20260000001.zip",
        "bigdata_p1_20260000001.rar",
        "bigdata_p1_20260000001.tar",
        "bigdata_p1_20260000001.tar.gz.exe",
        "bigdata_p1_20260000001 (1).tar.gz",
        "bigdata_p1_20260000001 .tar.gz",
        "bigdata_p1_20260000001.tar.gz.bak",
        "bigdata_p1_2026000001.tar.gz",
        "bigdata_p1_202600000012.tar.gz",
        "BIGDATA_P1_20260000001.tar.gz",
        "bigdata_p1_20260000001.TAR.GZ",
        OLD_FORMAT_NAME,
        f"{OLD_FORMAT_NAME}.zip",
        "20260000001-student-stage2-reviews.tar.gz",
        "20260000001-student-stage3-revision.tar.gz",
        "\u5f20\u4e09-student-stage1-problems.tar.gz",
        f"../{VALID_NAME}",
        f"folder/{VALID_NAME}",
        f".\\{VALID_NAME}",
        f"..\\{VALID_NAME}",
        f"{VALID_NAME}.zip",
        f"{VALID_NAME}\x00",
    ],
)
def test_invalid_attachment_filenames_are_rejected(filename):
    assert match_stage1_attachment_filename(filename) is None


def test_valid_attachment_filename_extracts_student_id():
    assert match_stage1_attachment_filename(VALID_NAME) == "20260000001"


def test_custom_student_id_core_regex_is_supported_and_cannot_bypass_path_checks():
    assert match_stage1_attachment_filename(
        "bigdata_p1_2026000001.tar.gz", student_id_core_regex=r"\d{10}"
    ) == "2026000001"
    assert (
        match_stage1_attachment_filename(
            "bigdata_p1_2026000001.tar.gz", student_id_core_regex=r"\d{11}"
        )
        is None
    )
    assert (
        match_stage1_attachment_filename(
            "folder/bigdata_p1_2026000001.tar.gz", student_id_core_regex=r"\d{10}"
        )
        is None
    )
    assert (
        match_stage1_attachment_filename(
            "../bigdata_p1_2026000001.tar.gz", student_id_core_regex=r"\d{10}"
        )
        is None
    )


def test_wrong_extension_is_rejected_in_decision(tmp_path):
    rows, plan = evaluate_message(
        meta(), [attachment("bigdata_p1_20260000001.zip")], config(tmp_path)
    )

    assert plan is None
    assert rows[-1].action == "rejected"
    assert rows[-1].reason == "invalid_attachment_filename"


def test_attachment_student_id_mismatch_is_rejected(tmp_path):
    rows, plan = evaluate_message(
        meta(),
        [attachment("bigdata_p1_20260000002.tar.gz")],
        config(tmp_path),
    )

    assert plan is None
    assert rows[-1].action == "rejected"
    assert rows[-1].reason == "student_id_mismatch"


def test_subject_without_student_id_is_rejected():
    with pytest.raises(ValueError, match="no_student_id_in_subject"):
        extract_student_id_from_subject("Stage1 submit")


def test_subject_with_multiple_student_ids_is_rejected():
    with pytest.raises(ValueError, match="multiple_student_ids_in_subject"):
        extract_student_id_from_subject("20260000001 and 20260000002 submit")


def test_multiple_valid_attachments_require_manual_review(tmp_path):
    rows, plan = evaluate_message(
        meta(),
        [attachment(VALID_NAME, "2"), attachment(VALID_NAME, "3")],
        config(tmp_path, dry_run=False),
    )

    assert plan is None
    assert {row.action for row in rows} == {"manual_review"}
    assert {row.reason for row in rows} == {"duplicate_valid_attachment"}


def test_existing_target_file_defaults_to_manual_review_and_does_not_overwrite(tmp_path):
    target = tmp_path / "20260000001" / VALID_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")

    rows, plan = evaluate_message(meta(), [attachment()], config(tmp_path, dry_run=False))

    assert plan is None
    assert rows[-1].action == "manual_review"
    assert rows[-1].reason == "already_exists_manual_review"
    assert target.read_bytes() == b"existing"


def test_duplicate_policy_skip_does_not_fetch_part(tmp_path):
    target = tmp_path / "20260000001" / VALID_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")
    fake = FakeImap(tar_gz_bytes())

    rows = collect_stage1_email(
        config(tmp_path, dry_run=False, duplicate_policy="skip"), imap_factory=lambda _config: fake
    )

    assert rows[-1].reason == "already_exists_skipped"
    assert not any("BODY.PEEK[2]" in call for call in fake.fetch_queries)
    assert target.read_bytes() == b"existing"


def test_duplicate_policy_overwrite_replaces_existing_file(tmp_path):
    target = tmp_path / "20260000001" / VALID_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")
    fake = FakeImap(tar_gz_bytes())

    rows = collect_stage1_email(
        config(tmp_path, dry_run=False, duplicate_policy="overwrite"),
        imap_factory=lambda _config: fake,
    )

    assert any(row.action == "downloaded" for row in rows)
    assert target.read_bytes() != b"existing"


def test_dry_run_does_not_download_any_attachment_part_or_file(tmp_path):
    fake = FakeImap(tar_gz_bytes())

    rows = collect_stage1_email(config(tmp_path, dry_run=True), imap_factory=lambda _config: fake)

    assert rows[-1].action == "skipped"
    assert rows[-1].reason == "dry_run"
    assert STAGE1_HEADER_STRUCTURE_QUERY in fake.fetch_queries
    assert not any("BODY.PEEK[2]" in call for call in fake.fetch_queries)
    assert not (tmp_path / "20260000001" / VALID_NAME).exists()
    assert (tmp_path / "collect-report.json").exists()


def test_download_flag_path_writes_only_valid_attachment_part(tmp_path):
    fake = FakeImap(tar_gz_bytes())

    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _config: fake)

    target = tmp_path / "20260000001" / VALID_NAME
    assert target.exists()
    assert any(row.action == "downloaded" for row in rows)
    assert any(row.sha256 for row in rows if row.action == "downloaded")
    assert any("BODY.PEEK[2]" in call for call in fake.fetch_queries)
    assert not list(target.parent.glob("*.part.*"))


def test_mixed_valid_and_invalid_attachments_fetches_only_valid_part(tmp_path):
    fake = FakeImap(tar_gz_bytes())

    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _config: fake)

    assert any(row.reason == "invalid_attachment_filename" for row in rows)
    assert any(row.action == "downloaded" for row in rows)
    assert any("BODY.PEEK[2]" in call for call in fake.fetch_queries)
    assert not any("BODY.PEEK[3]" in call for call in fake.fetch_queries)


def test_fetch_rfc822_is_forbidden():
    fake = FakeImap(tar_gz_bytes())
    forbidden = "RFC" + "822"

    with pytest.raises(ValueError, match="unsafe_full_message_fetch_forbidden"):
        safe_uid_fetch(fake, "101", f"({forbidden})")


@pytest.mark.parametrize("query", ["(BODY[])", "(BODY.PEEK[])"])
def test_unbounded_body_fetch_is_forbidden(query):
    fake = FakeImap(tar_gz_bytes())

    with pytest.raises(ValueError, match="unsafe_full_message_fetch_forbidden"):
        safe_uid_fetch(fake, "101", query)


def test_any_imap_command_string_does_not_contain_rfc822(tmp_path):
    fake = FakeImap(tar_gz_bytes())

    collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _config: fake)

    forbidden = "RFC" + "822"
    assert all(forbidden not in call for call in fake.fetch_queries)


def test_invalid_tar_gz_is_reported_and_file_removed(tmp_path):
    fake = FakeImap(b"not a gzip tar")

    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _config: fake)

    target = tmp_path / "20260000001" / VALID_NAME
    assert not target.exists()
    assert rows[-1].action == "error"
    assert rows[-1].reason == "invalid_tar_gz"


def test_invalid_tar_gz_validator(tmp_path):
    path = tmp_path / "bad.tar.gz"
    path.write_bytes(b"not a gzip tar")

    result = validate_tar_gz(path)

    assert result.ok is False
    assert result.reason == "invalid_tar_gz"


def test_tar_member_absolute_path_is_unsafe(tmp_path):
    path = tmp_path / "unsafe.tar.gz"
    info = tarfile.TarInfo("/abs/problem.txt")
    path.write_bytes(tar_gz_bytes([info]))

    result = validate_tar_gz(path)

    assert result.ok is False
    assert result.reason == "unsafe_tar_member"


def test_tar_member_parent_path_is_unsafe(tmp_path):
    path = tmp_path / "unsafe.tar.gz"
    info = tarfile.TarInfo("../problem.txt")
    path.write_bytes(tar_gz_bytes([info]))

    result = validate_tar_gz(path)

    assert result.ok is False
    assert result.reason == "unsafe_tar_member"


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_symlink_or_hardlink_is_unsafe(tmp_path, member_type):
    path = tmp_path / "unsafe-link.tar.gz"
    info = tarfile.TarInfo("problem-link")
    info.type = member_type
    info.linkname = "../outside"
    path.write_bytes(tar_gz_bytes([info]))

    result = validate_tar_gz(path)

    assert result.ok is False
    assert result.reason == "unsafe_tar_member"


def test_connect_imap_sends_id_after_login_before_select(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    result = connect_imap(config(tmp_path))

    assert result is fake
    assert fake.events == ["login", "capability", "ID", "select"]
    assert fake.select_readonly is True


def test_connect_imap_passes_verifiable_ssl_context(monkeypatch, tmp_path):
    import ssl

    captured: dict = {}

    def fake_ssl_class(**kwargs):
        captured.update(kwargs)
        return ConnectFakeImap(select_status="OK")

    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", fake_ssl_class)

    connect_imap(config(tmp_path))

    context = captured.get("ssl_context")
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_collect_select_no_records_failure_and_stops_uid(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="NO", select_data=[b"[Unsafe Login]"])
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    rows = collect_stage1_email(config(tmp_path))

    assert rows == [rows[0]]
    assert rows[0].action == "imap_select_failed"
    assert rows[0].reason == "imap_select_failed"
    assert "Unsafe Login" in rows[0].error_message_sanitized
    assert fake.events == ["login", "capability", "ID", "select"]
    assert fake.uid_calls == []
    report = json.loads((tmp_path / "collect-report.json").read_text())
    assert report["records"][0]["action"] == "imap_select_failed"


def test_send_imap_id_ok_allows_select(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK", id_status="OK")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    result = connect_imap(config(tmp_path))

    assert result is fake
    assert "select" in fake.events
    assert fake.select_readonly is True


@pytest.mark.parametrize("id_status", ["BAD", "NO"])
def test_send_imap_id_failure_stops_before_select(monkeypatch, tmp_path, id_status):
    fake = ConnectFakeImap(select_status="OK", id_status=id_status)
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    with pytest.raises(IMAPIdFailedError):
        connect_imap(config(tmp_path))

    assert "select" not in fake.events


def test_send_imap_id_exception_stops_before_select(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK", id_exception=True)
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    with pytest.raises(IMAPIdFailedError):
        connect_imap(config(tmp_path))

    assert "select" not in fake.events


def test_send_imap_id_collect_reports_failure_and_stops(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK", id_status="BAD")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    rows = collect_stage1_email(config(tmp_path))

    assert rows[0].reason == "imap_id_failed"
    assert rows[0].error_type == "IMAPIdFailedError"
    assert fake.uid_calls == []
    assert "select" not in fake.events
    report = json.loads((tmp_path / "collect-report.json").read_text())
    assert report["connection"]["imap_id_attempted"] is True
    assert report["connection"]["imap_id_status"] == "BAD"
    assert report["connection"]["mailbox_examine_ok"] is False


def test_id_payload_excludes_user_secrets(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "supersecret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    connect_imap(config(tmp_path))

    payload = fake.id_payload
    assert '"name" "MailGain"' in payload
    assert '"vendor" "MailGain"' in payload
    assert "supersecret" not in payload
    assert "teacher@163.com" not in payload
    assert "13800000000" not in payload
    assert "MAILGAIN_TEST_PASSWORD" not in payload


def test_connection_diagnostics_in_report_on_success(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    collect_stage1_email(config(tmp_path))

    report = json.loads((tmp_path / "collect-report.json").read_text())
    conn = report["connection"]
    assert conn["tls_connected"] is True
    assert conn["login_ok"] is True
    assert conn["imap_id_attempted"] is True
    assert conn["imap_id_status"] == "OK"
    assert conn["mailbox_examine_ok"] is True


def test_report_json_has_top_level_metadata_and_sanitized_command_args(tmp_path):
    rows = [
        MessageReportRowFactory(
            action="skipped",
            reason="dry_run",
        )
    ]
    write_reports(
        rows,
        tmp_path,
        command_args={"password_env": "MAILGAIN_TEST_PASSWORD", "password": "secret"},
    )

    report = json.loads((tmp_path / "collect-report.json").read_text())
    assert set(report) == {"generated_at", "command_args", "summary", "records"}
    assert report["command_args"] == {"password_env": "MAILGAIN_TEST_PASSWORD"}
    assert report["records"][0]["reason"] == "dry_run"
    assert "secret" not in (tmp_path / "collect-report.json").read_text()
    assert "from_addr" in (tmp_path / "collect-report.csv").read_text().splitlines()[0]


class MessageReportRowFactory:
    def __new__(cls, action: str, reason: str):
        from mailgain.stage1_email import ReportRow

        return ReportRow(uid="101", action=action, reason=reason)


class FakeImap:
    def __init__(
        self,
        payload: bytes,
        transfer_encoding: str = "base64",
        internal_date: str = "01-Jun-2026 10:00:00 +0000",
    ) -> None:
        self.payload = payload
        self.transfer_encoding = transfer_encoding
        self.internal_date = internal_date
        self.fetch_queries: list[str] = []

    def _encoded_payload(self) -> bytes:
        import base64
        import quopri

        if self.transfer_encoding == "base64":
            return base64.b64encode(self.payload)
        if self.transfer_encoding == "quoted-printable":
            return quopri.encodestring(self.payload)
        return self.payload

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"101"]
        if command != "FETCH":
            raise AssertionError(command)
        query = str(args[-1])
        self.fetch_queries.append(query)
        if query == STAGE1_HEADER_STRUCTURE_QUERY:
            return "OK", [
                (
                    b"101 (UID 101 INTERNALDATE "
                    + f'"{self.internal_date}"'.encode()
                    + b" BODY[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)] {120}",
                    b"Message-ID: <m101@example.com>\r\n"
                    b"Date: Mon, 01 Jun 2026 10:00:00 +0000\r\n"
                    b"From: student@example.com\r\n"
                    b"Subject: CodeSetArena Stage1 20260000001\r\n\r\n",
                ),
                f"BODYSTRUCTURE {BODYSTRUCTURE}".encode(),
            ]
        if "BODY.PEEK[2.MIME]" in query:
            return "OK", [
                (
                    b"101 (BODY[2.MIME]",
                    b"Content-Type: application/gzip\r\n"
                    b"Content-Transfer-Encoding: base64\r\n\r\n",
                )
            ]
        if "BODY.PEEK[2]" in query:
            return "OK", [(b"101 (BODY[2]", self._encoded_payload())]
        if "BODY.PEEK[3]" in query:
            raise AssertionError("invalid attachment part was fetched")
        raise AssertionError(query)

    def logout(self):
        return "OK", []


class ConnectFakeImap:
    def __init__(
        self,
        *,
        select_status: str = "OK",
        id_status: str = "OK",
        id_exception: bool = False,
        select_data=None,
    ) -> None:
        self.select_status = select_status
        self.id_status = id_status
        self.id_exception = id_exception
        self.select_data = select_data or []
        self.events: list[str] = []
        self.uid_calls: list[tuple] = []
        self.select_readonly = None
        self.id_payload: str = ""

    def login(self, user, password):
        self.events.append("login")
        return "OK", []

    def capability(self):
        self.events.append("capability")
        return "OK", [b'"IMAP4rev1"']

    def _simple_command(self, command, *args):
        self.events.append(command)
        if command == "CAPABILITY":
            return "OK", [b'"IMAP4rev1"']
        if command == "ID":
            self.id_payload = args[0] if args else ""
            if self.id_exception:
                raise RuntimeError("id blew up")
            return self.id_status, [b"ID ok"]
        raise AssertionError(f"unexpected command: {command}")

    def select(self, mailbox, readonly=False):
        self.events.append("select")
        self.select_readonly = readonly
        return self.select_status, self.select_data

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        raise AssertionError("select failure must stop uid calls")

    def logout(self):
        return "OK", []


BODYSTRUCTURE = (
    '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL NIL)'
    '("APPLICATION" "GZIP" ("NAME" "bigdata_p1_20260000001.tar.gz") '
    'NIL NIL "BASE64" 10 NIL ("ATTACHMENT" '
    '("FILENAME" "bigdata_p1_20260000001.tar.gz")) NIL NIL NIL)'
    '("APPLICATION" "ZIP" ("NAME" "stage1.zip") NIL NIL "BASE64" 10 NIL '
    '("ATTACHMENT" ("FILENAME" "stage1.zip")) NIL NIL NIL)'
    '"MIXED")'
)


def test_cli_missing_username_report_uses_stable_reason(monkeypatch, tmp_path):
    from mailgain.cli import main

    monkeypatch.delenv("CODESETARENA_163_USERNAME", raising=False)
    monkeypatch.delenv("CODESETARENA_163_AUTH_CODE", raising=False)

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 2
    csv_text = (tmp_path / "collect-report.csv").read_text()
    assert "env_username_missing" in csv_text


def test_cli_missing_auth_code_report_uses_stable_reason(monkeypatch, tmp_path):
    from mailgain.cli import main

    monkeypatch.setenv("CODESETARENA_163_USERNAME", "teacher@163.com")
    monkeypatch.delenv("CODESETARENA_163_AUTH_CODE", raising=False)

    result = main(
        [
            "teacher",
            "stage1",
            "collect-email",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 2
    csv_text = (tmp_path / "collect-report.csv").read_text()
    assert "env_auth_code_missing" in csv_text


def test_imap_connect_failed_has_stable_reason_and_sanitized_message(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")

    def fail_connect(**_kwargs):
        raise OSError("network down for teacher@163.com secret")

    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", fail_connect)

    rows = collect_stage1_email(config(tmp_path))

    assert rows[0].reason == "imap_connect_failed"
    assert rows[0].error_type == "IMAPConnectFailedError"
    assert "teacher@163.com" not in rows[0].error_message_sanitized
    assert "secret" not in rows[0].error_message_sanitized


def test_imap_login_failed_has_stable_reason_and_sanitized_message(monkeypatch, tmp_path):
    fake = LoginFailureFakeImap()
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    rows = collect_stage1_email(config(tmp_path))

    assert rows[0].reason == "imap_login_failed"
    assert "teacher@163.com" not in rows[0].error_message_sanitized
    assert "secret" not in rows[0].error_message_sanitized


def test_imap_id_failure_via_collect_reports_and_does_not_continue(monkeypatch, tmp_path):
    fake = ConnectFakeImap(select_status="OK", id_status="NO")
    monkeypatch.setenv("MAILGAIN_TEST_PASSWORD", "secret")
    monkeypatch.setattr("mailgain.stage1_email.imaplib.IMAP4_SSL", lambda **_kwargs: fake)

    rows = collect_stage1_email(config(tmp_path))

    assert rows[0].reason == "imap_id_failed"
    assert fake.uid_calls == []


def test_imap_search_failed_has_stable_reason(tmp_path):
    fake = SearchFailureFakeImap()

    rows = collect_stage1_email(config(tmp_path), imap_factory=lambda _config: fake)

    assert rows[0].reason == "imap_search_failed"
    assert rows[0].error_type == "IMAPSearchFailedError"


def test_imap_fetch_error_has_stable_reason(tmp_path):
    fake = FetchFailureFakeImap()

    rows = collect_stage1_email(config(tmp_path), imap_factory=lambda _config: fake)

    assert rows[0].uid == "101"
    assert rows[0].reason == "imap_fetch_error"
    assert rows[0].error_type == "IMAPFetchFailedError"


def test_report_write_failed_is_returned(monkeypatch, tmp_path):
    def fail_report(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("mailgain.stage1_email.write_reports", fail_report)

    rows = collect_stage1_email(
        config(tmp_path),
        imap_factory=lambda _config: (_ for _ in ()).throw(IMAPConnectFailedError("x")),
    )

    assert rows[0].reason == "imap_connect_failed"
    assert rows[1].reason == "report_write_failed"
    assert rows[1].error_type == "OSError"


def test_unexpected_error_has_stable_reason_and_sanitized_message(tmp_path):
    def fail_unexpected(_config):
        raise ValueError("boom teacher@163.com secret full body should not appear")

    rows = collect_stage1_email(config(tmp_path), imap_factory=fail_unexpected)

    assert rows[0].reason == "unexpected_error"
    assert rows[0].error_type == "ValueError"
    assert "teacher@163.com" not in rows[0].error_message_sanitized
    assert "secret" not in rows[0].error_message_sanitized
    assert "full body" not in rows[0].error_message_sanitized


class LoginFailureFakeImap:
    def login(self, user, password):
        raise RuntimeError(f"bad login {user} {password}")


class SearchFailureFakeImap:
    def uid(self, command, *args):
        assert command == "SEARCH"
        return "NO", [b"search unavailable"]

    def logout(self):
        return "OK", []


class FetchFailureFakeImap:
    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"101"]
        if command == "FETCH":
            return "NO", [b"fetch failed"]
        raise AssertionError(command)

    def logout(self):
        return "OK", []


# ---------------------------------------------------------------------------
# Content-Transfer-Encoding and FETCH literal tests
# ---------------------------------------------------------------------------

BODYSTRUCTURE_BASE64 = (
    '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL NIL)'
    '("APPLICATION" "GZIP" ("NAME" "bigdata_p1_20260000001.tar.gz") '
    'NIL NIL "BASE64" 10 NIL ("ATTACHMENT" '
    '("FILENAME" "bigdata_p1_20260000001.tar.gz")) NIL NIL NIL)'
    '"MIXED")'
)

BODYSTRUCTURE_WITH_EXTENSION = (
    '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL NIL)'
    '("APPLICATION" "GZIP" ("NAME" "bigdata_p1_20260000001.tar.gz") '
    '"<cid-1>" "stage1 problems" "BASE64" 42 '
    '"abc123" ("ATTACHMENT" '
    '("FILENAME" "bigdata_p1_20260000001.tar.gz")) ("en") NIL)'
    '"MIXED")'
)

BODYSTRUCTURE_NO_ENCODING = (
    '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL NIL)'
    '("APPLICATION" "GZIP" ("NAME" "bigdata_p1_20260000001.tar.gz") '
    'NIL NIL NIL 10 NIL ("ATTACHMENT" '
    '("FILENAME" "bigdata_p1_20260000001.tar.gz")) NIL NIL NIL)'
    '"MIXED")'
)


def test_bodystructure_base64_encoding_extracted():
    attachments = parse_bodystructure_attachments(BODYSTRUCTURE_BASE64)
    assert len(attachments) == 1
    att = attachments[0]
    assert att.content_transfer_encoding == "base64"
    assert att.encoded_size_bytes == 10
    assert att.content_type == "application/gzip"
    assert att.content_subtype == "gzip"


def test_bodystructure_extension_fields_do_not_shift_encoding():
    attachments = parse_bodystructure_attachments(BODYSTRUCTURE_WITH_EXTENSION)
    assert len(attachments) == 1
    att = attachments[0]
    assert att.content_transfer_encoding == "base64"
    assert att.encoded_size_bytes == 42
    assert att.filename == "bigdata_p1_20260000001.tar.gz"


class LiteralOnlyFakeImap:
    """Returns a proper (protocol_description, literal_payload) tuple."""

    def __init__(
        self,
        literal: bytes,
        internal_date: str = "01-Jun-2026 10:00:00 +0000",
    ) -> None:
        self.literal = literal
        self.internal_date = internal_date
        self.fetch_queries: list[str] = []

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"101"]
        if command != "FETCH":
            raise AssertionError(command)
        query = str(args[-1])
        self.fetch_queries.append(query)
        if query == STAGE1_HEADER_STRUCTURE_QUERY:
            return "OK", [
                (
                    b"101 (UID 101 INTERNALDATE "
                    + f'"{self.internal_date}"'.encode()
                    + b" BODY[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)] {120}",
                    b"Subject: CodeSetArena Stage1 20260000001\r\n\r\n",
                ),
                f"BODYSTRUCTURE {BODYSTRUCTURE_BASE64}".encode(),
            ]
        if "BODY.PEEK[2.MIME]" in query:
            return "OK", [
                (
                    b"101 (BODY[2.MIME]",
                    b"Content-Transfer-Encoding: base64\r\n\r\n",
                )
            ]
        if "BODY.PEEK[2]" in query:
            return "OK", [
                (b"101 (BODY[2] {" + str(len(self.literal)).encode() + b"}", self.literal),
                b")",
            ]
        raise AssertionError(query)

    def logout(self):
        return "OK", []


def test_fetch_tuple_extracts_only_literal_bytes():
    fake = LiteralOnlyFakeImap(b"some-binary-data-here")
    payload = fetch_attachment_part(fake, "101", "2")
    assert payload == b"some-binary-data-here"
    assert b"BODY[2]" not in payload
    assert b"101" not in payload


def test_fetch_protocol_description_not_in_payload():
    fake = LiteralOnlyFakeImap(b"PAYLOAD123")
    payload = fetch_attachment_part(fake, "101", "2")
    assert payload == b"PAYLOAD123"
    assert payload == b"PAYLOAD123"
    assert b"BODY" not in payload


def test_crlf_base64_tar_gz_decodes_successfully(tmp_path):
    raw_tar_gz = tar_gz_bytes()
    encoded = base64.b64encode(raw_tar_gz)
    crlf_encoded = b"\r\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    fake = LiteralOnlyFakeImap(crlf_encoded)
    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    target = tmp_path / "20260000001" / VALID_NAME
    assert target.exists()
    assert target.read_bytes()[:3] == b"\x1f\x8b\x08"
    assert any(r.action == "downloaded" for r in rows)


def test_base64_decoded_file_has_gzip_magic():
    raw_tar_gz = tar_gz_bytes()
    decoded = decode_content_transfer_encoding(base64.b64encode(raw_tar_gz), "base64")
    assert decoded[:3] == b"\x1f\x8b\x08"


def test_base64_decoded_tarfile_can_open(tmp_path):
    raw_tar_gz = tar_gz_bytes()
    decoded = decode_content_transfer_encoding(base64.b64encode(raw_tar_gz), "base64")
    with tarfile.open(fileobj=io.BytesIO(decoded), mode="r:gz") as tar:
        names = tar.getnames()
    assert "problem.txt" in names


def test_quoted_printable_decodes_correctly():
    decoded = decode_content_transfer_encoding(b"hello=20world", "quoted-printable")
    assert decoded == b"hello world"


def test_invalid_base64_is_rejected():
    with pytest.raises(ContentTransferEncodingError, match="invalid_content_transfer"):
        decode_content_transfer_encoding(b"!!!not-base64!!!", "base64")


def test_unknown_encoding_is_rejected():
    with pytest.raises(ContentTransferEncodingError, match="unsupported_content"):
        decode_content_transfer_encoding(b"data", "weird-encoding")


class NoEncodingMimeFallbackFakeImap:
    """BODYSTRUCTURE has no encoding; part MIME header provides it."""

    def __init__(
        self,
        literal: bytes,
        internal_date: str = "01-Jun-2026 10:00:00 +0000",
    ) -> None:
        self.literal = literal
        self.internal_date = internal_date
        self.fetch_queries: list[str] = []

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"101"]
        if command != "FETCH":
            raise AssertionError(command)
        query = str(args[-1])
        self.fetch_queries.append(query)
        if query == STAGE1_HEADER_STRUCTURE_QUERY:
            return "OK", [
                (
                    b"101 (UID 101 INTERNALDATE "
                    + f'"{self.internal_date}"'.encode()
                    + b" BODY[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)] {50}",
                    b"Subject: CodeSetArena Stage1 20260000001\r\n\r\n",
                ),
                f"BODYSTRUCTURE {BODYSTRUCTURE_NO_ENCODING}".encode(),
            ]
        if "BODY.PEEK[2.MIME]" in query:
            return "OK", [
                (
                    b"101 (BODY[2.MIME]",
                    b"Content-Transfer-Encoding: base64\r\n\r\n",
                )
            ]
        if "BODY.PEEK[2]" in query:
            return "OK", [(b"101 (BODY[2]", self.literal)]
        raise AssertionError(query)

    def logout(self):
        return "OK", []


def test_missing_encoding_falls_back_to_part_mime(tmp_path):
    raw_tar_gz = tar_gz_bytes()
    encoded = base64.b64encode(raw_tar_gz)
    fake = NoEncodingMimeFallbackFakeImap(encoded)
    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    target = tmp_path / "20260000001" / VALID_NAME
    assert target.exists()
    assert target.read_bytes()[:3] == b"\x1f\x8b\x08"
    assert any(".MIME" in q for q in fake.fetch_queries)
    assert any(r.action == "downloaded" for r in rows)


def test_dry_run_does_not_fetch_attachment_body(tmp_path):
    fake = FakeImap(tar_gz_bytes())
    collect_stage1_email(config(tmp_path, dry_run=True), imap_factory=lambda _c: fake)
    body_fetches = [
        q for q in fake.fetch_queries
        if "BODY.PEEK[2]" in q and "MIME" not in q
    ]
    assert body_fetches == []
    assert all("BODY.PEEK[3]" not in q for q in fake.fetch_queries)


def test_only_validated_part_id_is_fetched(tmp_path):
    fake = FakeImap(tar_gz_bytes())
    collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    part_fetches = [q for q in fake.fetch_queries if "BODY.PEEK[2]" in q and "MIME" not in q]
    assert len(part_fetches) == 1
    assert all("BODY.PEEK[3]" not in q for q in fake.fetch_queries)


def test_no_rfc822_or_unbounded_body_in_any_fetch(tmp_path):
    fake = FakeImap(tar_gz_bytes())
    collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    forbidden_rfc = "RFC" + "822"
    for q in fake.fetch_queries:
        assert forbidden_rfc not in q
        assert "BODY[]" not in q
        assert "BODY.PEEK[]" not in q


def test_validation_failure_removes_file_and_cleans_empty_dir(tmp_path):
    invalid_b64 = base64.b64encode(b"not a gzip tar at all")
    fake = LiteralOnlyFakeImap(invalid_b64)
    rows = collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    target = tmp_path / "20260000001" / VALID_NAME
    assert not target.exists()
    student_dir = tmp_path / "20260000001"
    assert not student_dir.exists()
    assert rows[-1].action == "error"


def test_report_includes_encoding_and_size_fields(tmp_path):
    raw_tar_gz = tar_gz_bytes()
    encoded = base64.b64encode(raw_tar_gz)
    fake = LiteralOnlyFakeImap(encoded)
    collect_stage1_email(config(tmp_path, dry_run=False), imap_factory=lambda _c: fake)
    report = json.loads((tmp_path / "collect-report.json").read_text())
    record = [r for r in report["records"] if r["action"] == "downloaded"][0]
    assert record["content_transfer_encoding"] == "base64"
    assert record["encoded_size_bytes"] == len(encoded)
    assert record["decoded_size_bytes"] == len(raw_tar_gz)
    assert record["size_bytes"] == len(raw_tar_gz)


# ---------------------------------------------------------------------------
# preserve-review duplicate policy tests
# ---------------------------------------------------------------------------


class MultiUidFakeImap:
    """FakeImap supporting multiple UIDs with configurable INTERNALDATE."""

    def __init__(self, messages: list[dict]) -> None:
        self.messages = {m["uid"]: m for m in messages}
        self.fetch_queries: list[str] = []

    def uid(self, command, *args):
        if command == "SEARCH":
            uids = sorted(self.messages.keys())
            return "OK", [" ".join(uids).encode()]
        if command != "FETCH":
            raise AssertionError(command)
        query = str(args[-1])
        self.fetch_queries.append(query)
        uid_str = str(args[0])
        msg = self.messages[uid_str]
        if query == STAGE1_HEADER_STRUCTURE_QUERY:
            header_bytes = (
                f'Message-ID: <m{uid_str}@example.com>\r\n'
                "Date: Mon, 01 Jun 2026 10:00:00 +0000\r\n"
                "From: student@example.com\r\n"
                f'Subject: CodeSetArena Stage1 {msg["student_id"]}\r\n\r\n'
            ).encode()
            return "OK", [
                (
                    f'{uid_str} (UID {uid_str} INTERNALDATE "{msg["internal_date"]}"'
                    f" BODY[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)] {{120}}".encode(),
                    header_bytes,
                ),
                f'BODYSTRUCTURE {msg["bodystructure"]}'.encode(),
            ]
        if "BODY.PEEK[2.MIME]" in query:
            enc = self.messages[uid_str].get("encoding", "base64")
            return "OK", [
                (
                    f"{uid_str} (BODY[2.MIME]".encode(),
                    f"Content-Transfer-Encoding: {enc}\r\n\r\n".encode(),
                )
            ]
        if "BODY.PEEK[2]" in query:
            payload = self.messages[uid_str]["payload"]
            return "OK", [
                (f"{uid_str} (BODY[2] {{{len(payload)}}}".encode(), payload),
            ]
        raise AssertionError(query)

    def logout(self):
        return "OK", []


def _msg(
    uid: str,
    student_id: str = "20260000001",
    internal_date: str = "01-Jun-2026 10:00:00 +0000",
    payload: bytes | None = None,
) -> dict:
    if payload is None:
        payload = base64.b64encode(tar_gz_bytes())
    return {
        "uid": uid,
        "student_id": student_id,
        "internal_date": internal_date,
        "payload": payload,
        "bodystructure": BODYSTRUCTURE_BASE64,
        "encoding": "base64",
    }


def _preserve_config(tmp_path: Path, *, dry_run: bool = False) -> CollectorConfig:
    return CollectorConfig(
        provider="163",
        imap_host="imap.163.com",
        imap_port=993,
        username="teacher@163.com",
        password_env="MAILGAIN_TEST_PASSWORD",
        mailbox="INBOX",
        output_dir=tmp_path,
        dry_run=dry_run,
        duplicate_policy="preserve-review",
        command_args={"password_env": "MAILGAIN_TEST_PASSWORD", "dry_run": dry_run},
    )


def test_preserve_review_existing_canonical_unchanged(tmp_path):
    canonical = tmp_path / "20260000001" / VALID_NAME
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"original-canonical-content")
    original_stat = canonical.stat()
    original_sha = hashlib.sha256(b"original-canonical-content").hexdigest()

    fake = MultiUidFakeImap([_msg(uid="102", internal_date="02-Jun-2026 12:00:00 +0000")])
    rows = collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )

    assert canonical.read_bytes() == b"original-canonical-content"
    assert canonical.stat().st_size == original_stat.st_size
    assert canonical.stat().st_mtime == original_stat.st_mtime
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == original_sha

    dup_files = list((tmp_path / "20260000001" / "duplicates").glob("*.tar.gz"))
    assert len(dup_files) == 1
    assert rows[-1].action == "manual_review"
    assert rows[-1].reason == "duplicate_preserved"
    assert rows[-1].overwritten is False
    assert rows[-1].requires_manual_review is True


def test_preserve_review_duplicate_written_to_duplicates_dir(tmp_path):
    fake = MultiUidFakeImap([_msg(uid="101", internal_date="01-Jun-2026 10:00:00 +0000")])
    collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )
    canonical = tmp_path / "20260000001" / VALID_NAME
    assert canonical.exists()

    fake2 = MultiUidFakeImap([_msg(uid="102", internal_date="02-Jun-2026 12:00:00 +0000")])
    rows = collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake2
    )

    dup_files = list((tmp_path / "20260000001" / "duplicates").glob("*.tar.gz"))
    assert len(dup_files) == 1
    assert "__dup-" in dup_files[0].name
    assert "__uid-102" in dup_files[0].name
    assert rows[-1].action == "manual_review"
    assert rows[-1].reason == "duplicate_preserved"


def test_preserve_review_three_same_student_one_canonical_two_duplicates(tmp_path):
    msgs = [
        _msg(uid="101", internal_date="01-Jun-2026 10:00:00 +0000"),
        _msg(uid="102", internal_date="02-Jun-2026 12:00:00 +0000"),
        _msg(uid="103", internal_date="03-Jun-2026 14:00:00 +0000"),
    ]
    fake = MultiUidFakeImap(msgs)
    rows = collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )

    canonical = tmp_path / "20260000001" / VALID_NAME
    assert canonical.exists()
    dup_files = sorted((tmp_path / "20260000001" / "duplicates").glob("*.tar.gz"))
    assert len(dup_files) == 2
    downloaded = [r for r in rows if r.action == "downloaded"]
    preserved = [r for r in rows if r.reason == "duplicate_preserved"]
    assert len(downloaded) == 1
    assert len(preserved) == 2


def test_preserve_review_same_timestamp_uid_breaks_tie(tmp_path):
    same_date = "01-Jun-2026 10:00:00 +0000"
    msgs = [
        _msg(uid="103", internal_date=same_date),
        _msg(uid="101", internal_date=same_date),
        _msg(uid="102", internal_date=same_date),
    ]
    fake = MultiUidFakeImap(msgs)
    rows = collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )

    canonical = tmp_path / "20260000001" / VALID_NAME
    assert canonical.exists()
    dup_files = list((tmp_path / "20260000001" / "duplicates").glob("*.tar.gz"))
    assert len(dup_files) == 2
    downloaded = [r for r in rows if r.action == "downloaded"]
    assert len(downloaded) == 1
    assert downloaded[0].uid == "101"


def test_preserve_review_concurrent_create_does_not_overwrite(tmp_path):
    dup_dir = tmp_path / "20260000001" / "duplicates"
    dup_dir.mkdir(parents=True)
    canonical = tmp_path / "20260000001" / VALID_NAME
    canonical.write_bytes(tar_gz_bytes())

    ts = "20260701-120000"
    pre_existing = (
        dup_dir
        / f"bigdata_p1_20260000001__dup-{ts}__uid-102.tar.gz"
    )
    pre_existing.write_bytes(b"already-here")

    fake = MultiUidFakeImap([_msg(uid="102", internal_date="02-Jun-2026 12:00:00 +0000")])
    collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )

    assert pre_existing.read_bytes() == b"already-here"
    all_dups = list(dup_dir.glob("*.tar.gz"))
    assert len(all_dups) == 2


def test_preserve_review_invalid_attachment_not_saved_to_duplicates(tmp_path):
    canonical = tmp_path / "20260000001" / VALID_NAME
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(tar_gz_bytes())

    bad_bodystructure = (
        '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1 NIL NIL NIL NIL)'
        '("APPLICATION" "ZIP" ("NAME" "stage1.zip") '
        'NIL NIL "BASE64" 10 NIL ("ATTACHMENT" '
        '("FILENAME" "stage1.zip")) NIL NIL NIL)'
        '"MIXED")'
    )
    msg = _msg(uid="102", internal_date="02-Jun-2026 12:00:00 +0000")
    msg["bodystructure"] = bad_bodystructure
    fake = MultiUidFakeImap([msg])
    rows = collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )

    dup_dir = tmp_path / "20260000001" / "duplicates"
    assert not dup_dir.exists() or not list(dup_dir.glob("*.tar.gz"))
    assert any(r.reason == "invalid_attachment_filename" for r in rows)


def test_preserve_review_dry_run_no_attachment_body_fetch(tmp_path):
    fake = MultiUidFakeImap([_msg(uid="101", internal_date="01-Jun-2026 10:00:00 +0000")])
    collect_stage1_email(
        _preserve_config(tmp_path, dry_run=True), imap_factory=lambda _c: fake
    )

    body_fetches = [
        q for q in fake.fetch_queries
        if "BODY.PEEK[2]" in q and "MIME" not in q
    ]
    assert body_fetches == []
    canonical = tmp_path / "20260000001" / VALID_NAME
    assert not canonical.exists()


def test_preserve_review_report_has_new_fields(tmp_path):
    fake = MultiUidFakeImap([_msg(uid="101", internal_date="01-Jun-2026 10:00:00 +0000")])
    collect_stage1_email(
        _preserve_config(tmp_path, dry_run=False), imap_factory=lambda _c: fake
    )
    report = json.loads((tmp_path / "collect-report.json").read_text())
    record = report["records"][0]
    assert "canonical_path" in record
    assert "preserved_path" in record
    assert "received_at_utc" in record
    assert "duplicate_group_id" in record
    assert "overwritten" in record
    assert "requires_manual_review" in record
    summary = report["summary"]
    assert "duplicates_preserved" in summary
    assert "manual_review_total" in summary
    assert "new_submission_count" in summary
    assert "duplicate_submission_count" in summary
