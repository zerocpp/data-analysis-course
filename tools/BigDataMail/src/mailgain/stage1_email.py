"""Secure Stage 1 email collection primitives."""

from __future__ import annotations

import csv
import hashlib
import imaplib
import json
import os
import quopri
import re
import ssl
import tarfile
from base64 import b64decode
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from mailgain import __version__

imaplib.Commands["ID"] = ("AUTH",)

STUDENT_ID_CORE_RE = r"\d{11}"
STAGE1_ATTACHMENT_PREFIX = "bigdata_p1_"
STAGE1_ATTACHMENT_SUFFIX = ".tar.gz"
DEFAULT_USERNAME_ENV = "CODESETARENA_163_USERNAME"
DEFAULT_PASSWORD_ENV = "CODESETARENA_163_AUTH_CODE"
STAGE1_HEADER_STRUCTURE_QUERY = (
    "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS"
    " (MESSAGE-ID DATE FROM SUBJECT)] BODYSTRUCTURE)"
)
IMAP_ID_PAYLOAD = (
    f'("name" "MailGain" "version" "{__version__}" "vendor" "MailGain")'
)
REPORT_FIELDS = [
    "uid",
    "message_id",
    "date",
    "from_addr",
    "subject",
    "subject_student_id",
    "attachment_filename",
    "attachment_student_id",
    "part_id",
    "action",
    "reason",
    "error_type",
    "error_message_sanitized",
    "saved_path",
    "sha256",
    "size_bytes",
    "content_transfer_encoding",
    "encoded_size_bytes",
    "decoded_size_bytes",
    "canonical_path",
    "preserved_path",
    "received_at_utc",
    "duplicate_group_id",
    "overwritten",
    "requires_manual_review",
]


class MailGainError(RuntimeError):
    """Base error with a stable report reason."""

    reason = "unexpected_error"

    def __init__(
        self,
        message: str = "",
        *,
        diagnostics: ConnectionDiagnostics | None = None,
    ) -> None:
        super().__init__(message or self.reason)
        self.diagnostics = diagnostics


class IMAPConnectFailedError(MailGainError):
    reason = "imap_connect_failed"


class IMAPLoginFailedError(MailGainError):
    reason = "imap_login_failed"


class IMAPSelectFailedError(MailGainError):
    reason = "imap_select_failed"


class IMAPSearchFailedError(MailGainError):
    reason = "imap_search_failed"


class IMAPFetchFailedError(MailGainError):
    reason = "imap_fetch_error"


class IMAPIdFailedError(MailGainError):
    reason = "imap_id_failed"


class ReportWriteFailedError(MailGainError):
    reason = "report_write_failed"


class ContentTransferEncodingError(MailGainError):
    reason = "content_transfer_encoding_error"


@dataclass
class ConnectionDiagnostics:
    tls_connected: bool = False
    login_ok: bool = False
    capability_after_login: str = ""
    imap_id_attempted: bool = False
    imap_id_status: str = ""
    imap_id_response_sanitized: str = ""
    mailbox_examine_ok: bool = False


@dataclass(frozen=True)
class AttachmentMeta:
    part_id: str
    filename: str
    content_type: str = ""
    content_subtype: str = ""
    disposition: str = ""
    content_transfer_encoding: str = ""
    encoded_size_bytes: int = 0


@dataclass(frozen=True)
class MessageMeta:
    uid: str
    message_id: str = ""
    from_addr: str = ""
    date: str = ""
    subject: str = ""
    internal_date: str = ""


@dataclass(frozen=True)
class CollectorConfig:
    provider: str
    imap_host: str
    imap_port: int
    username: str
    password_env: str
    mailbox: str
    output_dir: Path
    student_id_core_regex: str = STUDENT_ID_CORE_RE
    since: str | None = None
    dry_run: bool = True
    duplicate_policy: str = "manual"
    timeout: int = 30
    report_json: Path | None = None
    report_csv: Path | None = None
    command_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportRow:
    uid: str = ""
    message_id: str = ""
    date: str = ""
    from_addr: str = ""
    subject: str = ""
    subject_student_id: str = ""
    attachment_filename: str = ""
    attachment_student_id: str = ""
    part_id: str = ""
    action: str = ""
    reason: str = ""
    error_type: str = ""
    error_message_sanitized: str = ""
    saved_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    content_transfer_encoding: str = ""
    encoded_size_bytes: int = 0
    decoded_size_bytes: int = 0
    canonical_path: str = ""
    preserved_path: str = ""
    received_at_utc: str = ""
    duplicate_group_id: str = ""
    overwritten: bool = False
    requires_manual_review: bool = False


@dataclass(frozen=True)
class DownloadPlan:
    attachment: AttachmentMeta
    student_id: str
    saved_path: Path
    overwrite: bool = False
    is_duplicate: bool = False
    received_at_utc: str = ""
    duplicate_group_id: str = ""
    canonical_path: str = ""


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""


class ImapClient(Protocol):
    def login(self, user: str, password: str) -> Any: ...

    def select(self, mailbox: str, readonly: bool = False) -> Any: ...

    def uid(self, command: str, *args: Any) -> Any: ...

    def logout(self) -> Any: ...


def _student_id_pattern(student_id_core_regex: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\d)(?P<student_id>{student_id_core_regex})(?!\d)")


def _stage1_attachment_pattern(student_id_core_regex: str) -> str:
    return (
        rf"{re.escape(STAGE1_ATTACHMENT_PREFIX)}"
        rf"(?P<student_id>{student_id_core_regex})"
        rf"{re.escape(STAGE1_ATTACHMENT_SUFFIX)}"
    )


def validate_student_id_core_regex(student_id_core_regex: str) -> None:
    re.compile(student_id_core_regex)
    _student_id_pattern(student_id_core_regex)
    re.compile(_stage1_attachment_pattern(student_id_core_regex))


def validate_since(since: str | None) -> None:
    if since is None:
        return
    date.fromisoformat(since)


def extract_student_id_from_subject(
    subject: str, student_id_core_regex: str = STUDENT_ID_CORE_RE
) -> str:
    matches = [
        match.group("student_id")
        for match in _student_id_pattern(student_id_core_regex).finditer(subject)
    ]
    unique_matches = sorted(set(matches))
    if not unique_matches:
        raise ValueError("no_student_id_in_subject")
    if len(unique_matches) > 1:
        raise ValueError("multiple_student_ids_in_subject")
    return unique_matches[0]


def _has_path_fragment(filename: str) -> bool:
    return (
        not filename
        or filename.strip() != filename
        or filename in (".", "..")
        or any(token in filename for token in ("/", "\\", "\x00"))
        or ".." in filename
    )


def match_stage1_attachment_filename(
    filename: str, student_id_core_regex: str = STUDENT_ID_CORE_RE
) -> str | None:
    if _has_path_fragment(filename):
        return None
    attachment_re = re.compile(_stage1_attachment_pattern(student_id_core_regex))
    match = attachment_re.fullmatch(filename)
    if match is None:
        return None
    return match.group("student_id")


def _base_report(meta: MessageMeta, subject_student_id: str = "") -> ReportRow:
    return ReportRow(
        uid=meta.uid,
        message_id=meta.message_id,
        date=meta.date,
        from_addr=meta.from_addr,
        subject=meta.subject,
        subject_student_id=subject_student_id,
    )


def _attachment_report(
    meta: MessageMeta,
    attachment: AttachmentMeta,
    action: str,
    reason: str,
    subject_student_id: str = "",
    attachment_student_id: str = "",
    saved_path: Path | None = None,
) -> ReportRow:
    row = _base_report(meta, subject_student_id)
    row.attachment_filename = attachment.filename
    row.attachment_student_id = attachment_student_id
    row.part_id = attachment.part_id
    row.action = action
    row.reason = reason
    row.content_transfer_encoding = attachment.content_transfer_encoding
    row.encoded_size_bytes = attachment.encoded_size_bytes
    if saved_path is not None:
        row.saved_path = str(saved_path)
    return row


def _target_path(output_dir: Path, student_id: str) -> Path:
    filename = f"{STAGE1_ATTACHMENT_PREFIX}{student_id}{STAGE1_ATTACHMENT_SUFFIX}"
    return output_dir / student_id / filename


def _safe_target_path(output_dir: Path, student_id: str) -> Path:
    base = output_dir.resolve()
    target = _target_path(output_dir, student_id).resolve()
    if not target.is_relative_to(base):
        raise ValueError("unsafe_output_path")
    return target


def _duplicate_preserve_path(
    output_dir: Path,
    student_id: str,
    uid: str,
    utc_timestamp: str,
) -> Path:
    filename = (
        f"{STAGE1_ATTACHMENT_PREFIX}{student_id}"
        f"__dup-{utc_timestamp}__uid-{uid}{STAGE1_ATTACHMENT_SUFFIX}"
    )
    return output_dir / student_id / "duplicates" / filename


def _safe_duplicate_path(
    output_dir: Path,
    student_id: str,
    uid: str,
    utc_timestamp: str,
) -> Path:
    base = output_dir.resolve()
    target = _duplicate_preserve_path(
        output_dir, student_id, uid, utc_timestamp
    ).resolve()
    if not target.is_relative_to(base):
        raise ValueError("unsafe_output_path")
    return target


def _existing_file_decision(
    path: Path, meta: MessageMeta, duplicate_policy: str
) -> tuple[str, str, bool]:
    if not path.exists():
        return "", "", False
    if duplicate_policy == "preserve-review":
        return "", "", False
    if duplicate_policy == "manual":
        return "manual_review", "already_exists_manual_review", False
    if duplicate_policy == "skip":
        return "skipped", "already_exists_skipped", False
    if duplicate_policy == "overwrite":
        return "", "", True
    if duplicate_policy == "keep-newest":
        try:
            message_dt = parsedate_to_datetime(meta.date)
            if message_dt.tzinfo is None:
                message_dt = message_dt.replace(tzinfo=UTC)
            file_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except (OSError, TypeError, ValueError):
            return "manual_review", "keep_newest_needs_valid_message_date", False
        if message_dt <= file_dt:
            return "skipped", "already_exists_newer_or_same", False
        return "", "", True
    return "manual_review", "unknown_duplicate_policy", False


def evaluate_message(
    meta: MessageMeta,
    attachments: list[AttachmentMeta],
    config: CollectorConfig,
) -> tuple[list[ReportRow], DownloadPlan | None]:
    rows: list[ReportRow] = []
    try:
        subject_student_id = extract_student_id_from_subject(
            meta.subject, config.student_id_core_regex
        )
    except ValueError as exc:
        reason = str(exc)
        if attachments:
            for attachment in attachments:
                rows.append(_attachment_report(meta, attachment, "rejected", reason))
        else:
            row = _base_report(meta)
            row.action = "rejected"
            row.reason = reason
            rows.append(row)
        return rows, None

    valid: list[tuple[AttachmentMeta, str]] = []
    for attachment in attachments:
        attachment_student_id = match_stage1_attachment_filename(
            attachment.filename, config.student_id_core_regex
        )
        if attachment_student_id is None:
            rows.append(
                _attachment_report(
                    meta,
                    attachment,
                    "rejected",
                    "invalid_attachment_filename",
                    subject_student_id,
                )
            )
            continue
        valid.append((attachment, attachment_student_id))

    if not valid:
        if not attachments:
            row = _base_report(meta, subject_student_id)
            row.action = "skipped"
            row.reason = "no_valid_attachment"
            rows.append(row)
        return rows, None

    valid_ids = {student_id for _, student_id in valid}
    if len(valid_ids) > 1:
        for attachment, attachment_student_id in valid:
            rows.append(
                _attachment_report(
                    meta,
                    attachment,
                    "rejected",
                    "conflicting_valid_attachments",
                    subject_student_id,
                    attachment_student_id,
                )
            )
        return rows, None

    if len(valid) > 1:
        for attachment, attachment_student_id in valid:
            rows.append(
                _attachment_report(
                    meta,
                    attachment,
                    "manual_review",
                    "duplicate_valid_attachment",
                    subject_student_id,
                    attachment_student_id,
                )
            )
        return rows, None

    attachment, attachment_student_id = valid[0]
    canonical_path = _safe_target_path(config.output_dir, subject_student_id)
    if attachment_student_id != subject_student_id:
        rows.append(
            _attachment_report(
                meta,
                attachment,
                "rejected",
                "student_id_mismatch",
                subject_student_id,
                attachment_student_id,
                canonical_path,
            )
        )
        return rows, None

    is_duplicate = (
        config.duplicate_policy == "preserve-review"
        and canonical_path.exists()
    )

    if is_duplicate:
        utc_ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        saved_path = _safe_duplicate_path(
            config.output_dir, subject_student_id, meta.uid, utc_ts
        )
        dup_group = f"{subject_student_id}-{utc_ts}-{meta.uid}"
    else:
        saved_path = canonical_path

    action, reason, may_overwrite = _existing_file_decision(
        canonical_path, meta, config.duplicate_policy
    )
    if action:
        rows.append(
            _attachment_report(
                meta,
                attachment,
                action,
                reason,
                subject_student_id,
                attachment_student_id,
                canonical_path,
            )
        )
        return rows, None

    if config.dry_run:
        rows.append(
            _attachment_report(
                meta,
                attachment,
                "skipped",
                "dry_run",
                subject_student_id,
                attachment_student_id,
                saved_path,
            )
        )
        return rows, None

    if is_duplicate:
        row = _attachment_report(
            meta,
            attachment,
            "manual_review",
            "duplicate_preserved",
            subject_student_id,
            attachment_student_id,
            canonical_path,
        )
        row.preserved_path = str(saved_path)
        row.canonical_path = str(canonical_path)
        row.overwritten = False
        row.requires_manual_review = True
        rows.append(row)
        return rows, DownloadPlan(
            attachment,
            subject_student_id,
            saved_path,
            overwrite=False,
            is_duplicate=True,
            received_at_utc=utc_ts,
            duplicate_group_id=dup_group,
            canonical_path=str(canonical_path),
        )

    row = _attachment_report(
        meta,
        attachment,
        "downloaded",
        "downloaded",
        subject_student_id,
        attachment_student_id,
        saved_path,
    )
    row.canonical_path = str(canonical_path)
    row.overwritten = may_overwrite
    rows.append(row)
    return rows, DownloadPlan(
        attachment,
        subject_student_id,
        saved_path,
        overwrite=may_overwrite,
        is_duplicate=False,
        canonical_path=str(canonical_path),
    )


def _status_ok(status: object) -> bool:
    if isinstance(status, bytes):
        status = status.decode("ascii", errors="ignore")
    return str(status).upper() == "OK"


def _decode_imap_text(data: Any) -> str:
    if not data:
        return "unknown"
    first = data[0]
    if isinstance(first, bytes):
        return first.decode("utf-8", errors="replace")
    return str(first)


def send_imap_id(
    imap: ImapClient, diagnostics: ConnectionDiagnostics | None = None
) -> None:
    if diagnostics is None:
        diagnostics = ConnectionDiagnostics()
    diagnostics.imap_id_attempted = True
    command = getattr(imap, "_simple_command", None)
    if command is None:
        diagnostics.imap_id_status = "unsupported"
        raise IMAPIdFailedError("imap_id_unsupported", diagnostics=diagnostics)
    try:
        status, data = command("ID", IMAP_ID_PAYLOAD)
    except Exception as exc:  # noqa: BLE001 - must not expose account or auth code.
        diagnostics.imap_id_status = "exception"
        diagnostics.imap_id_response_sanitized = sanitize_error_message(exc)
        raise IMAPIdFailedError("imap_id_exception", diagnostics=diagnostics) from exc
    response_text = _decode_imap_text(data)
    diagnostics.imap_id_response_sanitized = sanitize_error_message(response_text)
    if _status_ok(status):
        diagnostics.imap_id_status = "OK"
        return
    diagnostics.imap_id_status = str(status).upper() if status else "UNKNOWN"
    raise IMAPIdFailedError(
        f"imap_id_failed:{diagnostics.imap_id_status}",
        diagnostics=diagnostics,
    )


def _needs_163_id(config: CollectorConfig) -> bool:
    return config.provider == "163" or config.imap_host.lower() == "imap.163.com"


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def connect_imap(config: CollectorConfig) -> imaplib.IMAP4_SSL:
    password = os.environ.get(config.password_env)
    if not password:
        raise RuntimeError(f"missing_password_env:{config.password_env}")
    diagnostics = ConnectionDiagnostics()
    try:
        imap = imaplib.IMAP4_SSL(
            host=config.imap_host,
            port=config.imap_port,
            ssl_context=_build_ssl_context(),
            timeout=config.timeout,
        )
    except Exception as exc:  # noqa: BLE001 - sanitize before reporting.
        raise IMAPConnectFailedError(str(exc), diagnostics=diagnostics) from exc
    diagnostics.tls_connected = True
    try:
        imap.login(config.username, password)
    except Exception as exc:  # noqa: BLE001 - sanitize before reporting.
        raise IMAPLoginFailedError(str(exc), diagnostics=diagnostics) from exc
    diagnostics.login_ok = True
    try:
        cap_status, cap_data = imap.capability()
        if _status_ok(cap_status):
            diagnostics.capability_after_login = _decode_imap_text(cap_data)
    except Exception:  # noqa: BLE001 - capability is diagnostic only.
        diagnostics.capability_after_login = ""
    if _needs_163_id(config):
        send_imap_id(imap, diagnostics)
    status, data = imap.select(config.mailbox, readonly=True)
    if not _status_ok(status):
        raise IMAPSelectFailedError(
            _decode_imap_text(data), diagnostics=diagnostics
        )
    diagnostics.mailbox_examine_ok = True
    imap._mailgain_diagnostics = diagnostics  # type: ignore[attr-defined]
    return imap


def _ensure_safe_fetch_query(*parts: object) -> None:
    query = " ".join(str(part).upper() for part in parts)
    forbidden = ("RFC" + "822", "BODY" + "[]", "BODY.PEEK" + "[]")
    if any(token in query for token in forbidden):
        raise ValueError("unsafe_full_message_fetch_forbidden")


def safe_uid_fetch(imap: ImapClient, uid: str, query: str) -> Any:
    _ensure_safe_fetch_query("FETCH", uid, query)
    return imap.uid("FETCH", uid, query)


def search_candidate_uids(imap: ImapClient, config: CollectorConfig) -> list[str]:
    if config.since:
        since_date = date.fromisoformat(config.since).strftime("%d-%b-%Y")
        status, data = imap.uid("SEARCH", None, "SINCE", since_date)
    else:
        status, data = imap.uid("SEARCH", None, "ALL")
    if not _status_ok(status):
        raise IMAPSearchFailedError(_decode_imap_text(data))
    if not data or data[0] is None:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    return [uid for uid in str(raw).split() if uid]


def _first_tuple_payload(data: list[Any]) -> bytes:
    """Extract the literal payload bytes from an imaplib FETCH response tuple.

    imaplib returns tuples of (protocol_description_bytes, literal_payload_bytes).
    The protocol description (item[0]) contains ``BODY[2] {1234}`` markers and
    must never be treated as attachment content.
    """
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _extract_literal_payload(data: list[Any]) -> bytes:
    """Return the unique literal payload from a FETCH response, or raise."""
    literal = _first_tuple_payload(data)
    if not literal:
        raise IMAPFetchFailedError("invalid_fetch_literal_response")
    return literal


def _extract_bodystructure_text(data: list[Any]) -> str:
    candidates: list[str] = []
    for item in data:
        if isinstance(item, bytes):
            candidates.append(item.decode("utf-8", errors="replace"))
        elif isinstance(item, tuple):
            for value in item:
                if isinstance(value, bytes):
                    candidates.append(value.decode("utf-8", errors="replace"))
                elif isinstance(value, str):
                    candidates.append(value)
    for text in candidates:
        marker = "BODYSTRUCTURE"
        if marker in text:
            return text.split(marker, 1)[1].strip().rstrip(")")
    return ""


def _extract_internal_date(data: list[Any]) -> str:
    for item in data:
        if isinstance(item, bytes):
            text = item.decode("utf-8", errors="replace")
            if "INTERNALDATE" in text.upper():
                import re as _re

                match = _re.search(r'INTERNALDATE\s+"([^"]+)"', text)
                if match:
                    return match.group(1)
        elif isinstance(item, tuple):
            for value in item:
                if isinstance(value, bytes):
                    text = value.decode("utf-8", errors="replace")
                    if "INTERNALDATE" in text.upper():
                        import re as _re

                        match = _re.search(r'INTERNALDATE\s+"([^"]+)"', text)
                        if match:
                            return match.group(1)
    return ""


def fetch_message_overview(imap: ImapClient, uid: str) -> tuple[MessageMeta, str]:
    status, data = safe_uid_fetch(imap, uid, STAGE1_HEADER_STRUCTURE_QUERY)
    if not _status_ok(status):
        raise IMAPFetchFailedError(_decode_imap_text(data))
    headers = BytesParser(policy=policy.default).parsebytes(_first_tuple_payload(data))
    meta = MessageMeta(
        uid=uid,
        message_id=str(headers.get("Message-ID", "")),
        from_addr=str(headers.get("From", "")),
        date=str(headers.get("Date", "")),
        subject=str(headers.get("Subject", "")),
        internal_date=_extract_internal_date(data),
    )
    return meta, _extract_bodystructure_text(data)


def fetch_message_metadata(imap: ImapClient, uid: str) -> MessageMeta:
    meta, _structure = fetch_message_overview(imap, uid)
    return meta


def fetch_message_structure(imap: ImapClient, uid: str) -> str:
    _meta, structure = fetch_message_overview(imap, uid)
    return structure


def fetch_attachment_part(imap: ImapClient, uid: str, part_id: str) -> bytes:
    if re.fullmatch(r"\d+(?:\.\d+)*", part_id) is None:
        raise ValueError("invalid_part_id")
    status, data = safe_uid_fetch(imap, uid, f"(BODY.PEEK[{part_id}])")
    if not _status_ok(status):
        raise IMAPFetchFailedError(_decode_imap_text(data))
    return _extract_literal_payload(data)


def fetch_part_mime(imap: ImapClient, uid: str, part_id: str) -> dict[str, str]:
    """Fetch only the MIME headers of a validated part to resolve its encoding.

    This fetches ``BODY.PEEK[<part_id>.MIME]`` which is the MIME header section
    of a single part — NOT the full message and NOT other parts.
    """
    if re.fullmatch(r"\d+(?:\.\d+)*", part_id) is None:
        raise ValueError("invalid_part_id")
    status, data = safe_uid_fetch(imap, uid, f"(BODY.PEEK[{part_id}.MIME])")
    if not _status_ok(status):
        raise IMAPFetchFailedError(_decode_imap_text(data))
    headers = BytesParser(policy=policy.default).parsebytes(
        _first_tuple_payload(data)
    )
    result: dict[str, str] = {}
    cte = headers.get("Content-Transfer-Encoding")
    if cte:
        result["content_transfer_encoding"] = str(cte).strip().lower()
    return result


_PASS_THROUGH_ENCODINGS = {"7bit", "8bit", "binary", ""}


def decode_content_transfer_encoding(payload: bytes, encoding: str | None) -> bytes:
    """Decode a FETCH literal payload according to its Content-Transfer-Encoding."""
    enc = (encoding or "").strip().lower()
    if enc in _PASS_THROUGH_ENCODINGS:
        return payload
    if enc == "base64":
        compact = bytes(payload.translate(None, b" \t\r\n"))
        try:
            return b64decode(compact, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ContentTransferEncodingError(
                "invalid_content_transfer_encoding"
            ) from exc
    if enc == "quoted-printable":
        return quopri.decodestring(payload)
    raise ContentTransferEncodingError("unsupported_content_transfer_encoding")


def _tokenize_bodystructure(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            value: list[str] = []
            while index < len(text):
                current = text[index]
                if current == "\\" and index + 1 < len(text):
                    value.append(text[index + 1])
                    index += 2
                    continue
                if current == '"':
                    index += 1
                    break
                value.append(current)
                index += 1
            tokens.append("".join(value))
            continue
        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in "()":
            index += 1
        tokens.append(text[start:index])
    return tokens


def _parse_tokens(tokens: list[str], index: int = 0) -> tuple[Any, int]:
    if tokens[index] != "(":
        token = tokens[index]
        if token.upper() == "NIL":
            return None, index + 1
        return token, index + 1
    index += 1
    values: list[Any] = []
    while index < len(tokens) and tokens[index] != ")":
        value, index = _parse_tokens(tokens, index)
        values.append(value)
    return values, index + 1


def _params_to_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    result: dict[str, str] = {}
    for index in range(0, len(value) - 1, 2):
        key = value[index]
        val = value[index + 1]
        if isinstance(key, str) and isinstance(val, str):
            result[key.upper()] = val
    return result


def _walk_bodystructure(node: Any, prefix: str = "") -> list[AttachmentMeta]:
    if not isinstance(node, list) or not node:
        return []
    if isinstance(node[0], list):
        attachments: list[AttachmentMeta] = []
        child_index = 1
        for child in node:
            if not isinstance(child, list):
                break
            child_prefix = f"{prefix}.{child_index}" if prefix else str(child_index)
            attachments.extend(_walk_bodystructure(child, child_prefix))
            child_index += 1
        return attachments

    media_type = str(node[0]).lower() if len(node) > 0 and node[0] else ""
    subtype = str(node[1]).lower() if len(node) > 1 and node[1] else ""
    content_type = f"{media_type}/{subtype}" if media_type and subtype else ""
    params = _params_to_dict(node[2] if len(node) > 2 else None)
    encoding = str(node[5]).lower() if len(node) > 5 and node[5] else ""
    encoded_octets = 0
    if len(node) > 6 and node[6]:
        try:
            encoded_octets = int(node[6])
        except (TypeError, ValueError):
            encoded_octets = 0
    disposition = ""
    disposition_params: dict[str, str] = {}
    if len(node) > 8 and isinstance(node[8], list) and node[8]:
        disposition = str(node[8][0]).lower() if node[8][0] else ""
        disposition_params = _params_to_dict(node[8][1] if len(node[8]) > 1 else None)
    filename = disposition_params.get("FILENAME") or params.get("NAME") or ""
    if not filename:
        return []
    return [
        AttachmentMeta(
            part_id=prefix or "1",
            filename=filename,
            content_type=content_type,
            content_subtype=subtype,
            disposition=disposition,
            content_transfer_encoding=encoding,
            encoded_size_bytes=encoded_octets,
        )
    ]


def parse_bodystructure_attachments(bodystructure: str) -> list[AttachmentMeta]:
    if not bodystructure.strip():
        return []
    tokens = _tokenize_bodystructure(bodystructure)
    if not tokens:
        return []
    parsed, _ = _parse_tokens(tokens)
    return _walk_bodystructure(parsed)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_tar_path(name: str) -> bool:
    path = PurePosixPath(name)
    return "\x00" in name or path.is_absolute() or ".." in path.parts


def reject_tar_path_traversal(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        if _unsafe_tar_path(member.name):
            raise ValueError("unsafe_tar_member")
        if member.issym() or member.islnk():
            raise ValueError("unsafe_tar_member")


def validate_tar_gz(path: Path) -> ValidationResult:
    try:
        if path.stat().st_size <= 0:
            return ValidationResult(False, "invalid_tar_gz")
    except OSError:
        return ValidationResult(False, "invalid_tar_gz")
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            reject_tar_path_traversal(members)
    except ValueError as exc:
        reason = str(exc) or "unsafe_tar_member"
        if reason == "unsafe_tar_member":
            return ValidationResult(False, reason)
        return ValidationResult(False, "invalid_tar_gz")
    except (tarfile.TarError, OSError):
        return ValidationResult(False, "invalid_tar_gz")
    return ValidationResult(True)


def _write_attachment_atomic(path: Path, payload: bytes, overwrite: bool) -> None:
    base = path.parents[1].resolve()
    target = path.resolve()
    if not target.is_relative_to(base):
        raise ValueError("unsafe_output_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError("already_exists_manual_review")
    tmp_path = path.with_name(f".{path.name}.part.{os.getpid()}")
    try:
        tmp_path.write_bytes(payload)
        if path.exists() and not overwrite:
            raise FileExistsError("already_exists_manual_review")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _write_exclusive(path: Path, payload: bytes) -> Path:
    """Write payload using exclusive creation. Never overwrites an existing file.

    On name collision, retries with ``__n-2``, ``__n-3``, ... suffixes.
    Uses ``Path.open("xb")`` so concurrent processes cannot clobber each other.
    """
    base = path.parents[1].resolve()
    target = path.resolve()
    if not target.is_relative_to(base):
        raise ValueError("unsafe_output_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path
    suffix_num = 1
    while True:
        try:
            with candidate.open("xb") as f:
                f.write(payload)
            return candidate
        except FileExistsError:
            suffix_num += 1
            candidate = path.with_name(
                path.stem + f"__n-{suffix_num}" + path.suffix
            )
            if suffix_num > 999:
                raise


def _report_dict(row: ReportRow) -> dict[str, Any]:
    return asdict(row)


def _summary(rows: list[ReportRow]) -> dict[str, int]:
    summary = {
        "total_records": len(rows),
        "downloaded": 0,
        "rejected": 0,
        "skipped": 0,
        "manual_review": 0,
        "errors": 0,
        "imap_select_failed": 0,
        "new_submission_count": 0,
        "duplicate_submission_count": 0,
        "duplicates_preserved": 0,
        "manual_review_total": 0,
    }
    for row in rows:
        if row.action == "error":
            summary["errors"] += 1
        elif row.action == "downloaded":
            summary["downloaded"] += 1
            summary["new_submission_count"] += 1
        elif row.action == "rejected":
            summary["rejected"] += 1
        elif row.action == "skipped":
            summary["skipped"] += 1
        elif row.action == "manual_review":
            summary["manual_review"] += 1
            summary["manual_review_total"] += 1
            if row.reason == "duplicate_preserved":
                summary["duplicates_preserved"] += 1
                summary["duplicate_submission_count"] += 1
        elif row.action in summary:
            summary[row.action] += 1
    return summary


def _sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(data)
    cleaned.pop("password", None)
    cleaned.pop("auth_code", None)
    return cleaned


def _sanitized_command_args(config: CollectorConfig) -> dict[str, Any]:
    if config.command_args:
        data = dict(config.command_args)
    else:
        data = {
            "provider": config.provider,
            "imap_host": config.imap_host,
            "imap_port": config.imap_port,
            "mailbox": config.mailbox,
            "output_dir": str(config.output_dir),
            "student_id_core_regex": config.student_id_core_regex,
            "since": config.since,
            "dry_run": config.dry_run,
            "duplicate_policy": config.duplicate_policy,
            "timeout": config.timeout,
            "password_env": config.password_env,
        }
    data.pop("password", None)
    data.pop("auth_code", None)
    return data


def sanitize_error_message(message: object, config: CollectorConfig | None = None) -> str:
    text = str(message or "")
    replacements = []
    if config is not None:
        replacements.extend([config.username, os.environ.get(config.password_env, "")])
    replacements.extend(
        os.environ.get(name, "") for name in (DEFAULT_USERNAME_ENV, DEFAULT_PASSWORD_ENV)
    )
    for value in replacements:
        if value:
            text = text.replace(value, "[redacted]")
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[:300]


def _error_row(
    reason: str,
    exc: BaseException | None = None,
    config: CollectorConfig | None = None,
    action: str = "error",
    uid: str = "",
) -> ReportRow:
    message = (
        "unexpected_error" if reason == "unexpected_error" else sanitize_error_message(exc, config)
    )
    return ReportRow(
        uid=uid,
        action=action,
        reason=reason,
        error_type=exc.__class__.__name__ if exc is not None else "",
        error_message_sanitized=message if exc is not None else "",
    )


def _reason_for_exception(exc: BaseException) -> str:
    if isinstance(exc, MailGainError):
        return exc.reason
    return "unexpected_error"


def write_reports_safely(
    rows: list[ReportRow],
    output_dir: Path,
    report_json: Path | None = None,
    report_csv: Path | None = None,
    command_args: dict[str, Any] | None = None,
    config: CollectorConfig | None = None,
    connection_diagnostics: ConnectionDiagnostics | None = None,
) -> bool:
    try:
        write_reports(
            rows, output_dir, report_json, report_csv, command_args, connection_diagnostics
        )
    except Exception as exc:  # noqa: BLE001 - preserve stable returned diagnostic.
        rows.append(_error_row("report_write_failed", exc, config))
        return False
    return True


def write_reports(
    rows: list[ReportRow],
    output_dir: Path,
    report_json: Path | None = None,
    report_csv: Path | None = None,
    command_args: dict[str, Any] | None = None,
    connection_diagnostics: ConnectionDiagnostics | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_json or output_dir / "collect-report.json"
    csv_path = report_csv or output_dir / "collect-report.csv"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    report_rows = [_report_dict(row) for row in rows]
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "command_args": _sanitize_mapping(command_args or {}),
        "summary": _summary(rows),
        "records": report_rows,
    }
    if connection_diagnostics is not None:
        payload["connection"] = asdict(connection_diagnostics)
    json_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(report_rows)
    return json_path, csv_path


def _validation_failure_reason(validation: ValidationResult) -> str:
    if validation.reason == "unsafe_tar_member":
        return "unsafe_tar_member"
    if validation.reason == "invalid_tar_gz":
        return "invalid_tar_gz"
    return "post_download_validation_failed"


MAX_ATTACHMENT_BYTES = 256 * 1024 * 1024


def _parse_internal_date(date_str: str) -> datetime:
    """Parse an IMAP INTERNALDATE string into a timezone-aware datetime.

    INTERNALDATE format: ``01-Jun-2026 12:00:00 +0000``
    Falls back to epoch if parsing fails (earliest = canonical candidate).
    """
    if not date_str:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        return datetime.strptime(date_str, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        try:
            naive = datetime.strptime(date_str, "%d-%b-%Y %H:%M:%S")
            return naive.replace(tzinfo=UTC)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=UTC)


def _sort_key_internaldate(meta: MessageMeta) -> tuple[datetime, int]:
    return (_parse_internal_date(meta.internal_date), int(meta.uid) if meta.uid.isdigit() else 0)


def _resolve_encoding(
    imap: ImapClient,
    uid: str,
    attachment: AttachmentMeta,
) -> str:
    """Resolve the content-transfer-encoding, falling back to part MIME headers."""
    if attachment.content_transfer_encoding:
        return attachment.content_transfer_encoding
    mime_headers = fetch_part_mime(imap, uid, attachment.part_id)
    bodystructure_cte = attachment.content_transfer_encoding or ""
    mime_cte = mime_headers.get("content_transfer_encoding", "")
    if bodystructure_cte and mime_cte and bodystructure_cte != mime_cte:
        raise ContentTransferEncodingError("content_transfer_encoding_mismatch")
    return mime_cte


def _cleanup_empty_student_dir(path: Path) -> None:
    parent = path.parent
    try:
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _process_download(
    imap: ImapClient,
    uid: str,
    plan: DownloadPlan,
    target_row: ReportRow,
) -> None:
    """Execute the full validated-download pipeline for a single accepted email."""
    encoding = _resolve_encoding(imap, uid, plan.attachment)
    target_row.content_transfer_encoding = encoding
    raw_payload = fetch_attachment_part(imap, uid, plan.attachment.part_id)
    target_row.encoded_size_bytes = len(raw_payload)
    decoded = decode_content_transfer_encoding(raw_payload, encoding)
    target_row.decoded_size_bytes = len(decoded)
    if not decoded:
        target_row.action = "error"
        target_row.reason = "empty_decoded_payload"
        target_row.error_type = "ContentTransferEncodingError"
        return
    if len(decoded) > MAX_ATTACHMENT_BYTES:
        target_row.action = "error"
        target_row.reason = "attachment_too_large"
        target_row.error_type = "ValueError"
        _cleanup_empty_student_dir(plan.saved_path)
        return
    if plan.is_duplicate:
        actual_path = _write_exclusive(plan.saved_path, decoded)
        target_row.preserved_path = str(actual_path)
        target_row.canonical_path = plan.canonical_path
        target_row.received_at_utc = plan.received_at_utc
        target_row.duplicate_group_id = plan.duplicate_group_id
        target_row.overwritten = False
        target_row.requires_manual_review = True
    else:
        _write_attachment_atomic(plan.saved_path, decoded, plan.overwrite)
        target_row.canonical_path = str(plan.saved_path)
        target_row.overwritten = plan.overwrite
    validation = validate_tar_gz(
        Path(target_row.preserved_path) if plan.is_duplicate else plan.saved_path
    )
    if validation.ok:
        validated_path = (
            Path(target_row.preserved_path) if plan.is_duplicate else plan.saved_path
        )
        target_row.size_bytes = validated_path.stat().st_size
        target_row.sha256 = sha256_file(validated_path)
    else:
        failed_path = (
            Path(target_row.preserved_path) if plan.is_duplicate else plan.saved_path
        )
        if failed_path.exists():
            failed_path.unlink()
        _cleanup_empty_student_dir(failed_path)
        target_row.action = "error"
        target_row.reason = _validation_failure_reason(validation)
        target_row.error_type = "ValidationError"
        target_row.error_message_sanitized = validation.reason
        target_row.size_bytes = 0
        target_row.sha256 = ""


def _collect_preserve_review(
    imap: ImapClient,
    uids: list[str],
    config: CollectorConfig,
    rows: list[ReportRow],
) -> list[ReportRow]:
    """Two-pass collection for preserve-review: fetch metadata, sort, then download.

    Phase 1: Fetch overview + evaluate for all UIDs (no attachment body fetch).
    Phase 2: Group accepted plans by student_id, sort by INTERNALDATE then UID,
             download earliest as canonical, rest as duplicates.
    """
    pending: list[tuple[MessageMeta, list[ReportRow], DownloadPlan]] = []
    for uid in uids:
        try:
            meta, structure = fetch_message_overview(imap, uid)
            attachments = parse_bodystructure_attachments(structure)
            message_rows, plan = evaluate_message(meta, attachments, config)
            if plan is not None:
                pending.append((meta, message_rows, plan))
            else:
                rows.extend(message_rows)
        except Exception as exc:  # noqa: BLE001
            rows.append(_error_row("imap_fetch_error", exc, config, uid=uid))

    by_student: dict[str, list[tuple[MessageMeta, list[ReportRow], DownloadPlan]]] = {}
    for meta, message_rows, plan in pending:
        by_student.setdefault(plan.student_id, []).append(
            (meta, message_rows, plan)
        )

    for student_id, group in by_student.items():
        group.sort(key=lambda item: _sort_key_internaldate(item[0]))
        for index, (meta, message_rows, plan) in enumerate(group):
            if index == 0 and not plan.is_duplicate:
                target_row = message_rows[-1]
                _process_download(imap, meta.uid, plan, target_row)
            else:
                utc_ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                dup_plan = DownloadPlan(
                    attachment=plan.attachment,
                    student_id=plan.student_id,
                    saved_path=_safe_duplicate_path(
                        config.output_dir, plan.student_id, meta.uid, utc_ts
                    ),
                    overwrite=False,
                    is_duplicate=True,
                    received_at_utc=utc_ts,
                    duplicate_group_id=f"{student_id}-{utc_ts}-{meta.uid}",
                    canonical_path=str(
                        _safe_target_path(config.output_dir, student_id)
                    ),
                )
                target_row = message_rows[-1]
                target_row.action = "manual_review"
                target_row.reason = "duplicate_preserved"
                target_row.overwritten = False
                target_row.requires_manual_review = True
                target_row.canonical_path = dup_plan.canonical_path
                _process_download(imap, meta.uid, dup_plan, target_row)
            rows.extend(message_rows)
    return rows


def collect_stage1_email(
    config: CollectorConfig,
    imap_factory: Any = connect_imap,
) -> list[ReportRow]:
    rows: list[ReportRow] = []
    imap: ImapClient | None = None
    diagnostics: ConnectionDiagnostics | None = None
    command_args = _sanitized_command_args(config)
    try:
        imap = imap_factory(config)
        diagnostics = getattr(imap, "_mailgain_diagnostics", None)
    except IMAPSelectFailedError as exc:
        diagnostics = getattr(exc, "diagnostics", None)
        rows.append(_error_row("imap_select_failed", exc, config, action="imap_select_failed"))
        write_reports_safely(
            rows, config.output_dir, config.report_json, config.report_csv,
            command_args, config, diagnostics,
        )
        return rows
    except Exception as exc:  # noqa: BLE001 - connection failures must not expose secrets.
        diagnostics = getattr(exc, "diagnostics", None)
        rows.append(_error_row(_reason_for_exception(exc), exc, config))
        write_reports_safely(
            rows, config.output_dir, config.report_json, config.report_csv,
            command_args, config, diagnostics,
        )
        return rows

    try:
        uids = search_candidate_uids(imap, config)
    except Exception as exc:  # noqa: BLE001 - search failure is a top-level run failure.
        rows.append(_error_row(_reason_for_exception(exc), exc, config))
        try:
            if imap is not None:
                imap.logout()
        finally:
            write_reports_safely(
                rows, config.output_dir, config.report_json, config.report_csv,
                command_args, config, diagnostics,
            )
        return rows

    try:
        if config.duplicate_policy == "preserve-review":
            rows = _collect_preserve_review(imap, uids, config, rows)
        else:
            for uid in uids:
                try:
                    meta, structure = fetch_message_overview(imap, uid)
                    attachments = parse_bodystructure_attachments(structure)
                    message_rows, plan = evaluate_message(meta, attachments, config)
                    if plan is not None:
                        target_row = message_rows[-1]
                        _process_download(imap, uid, plan, target_row)
                    rows.extend(message_rows)
                except Exception as exc:  # noqa: BLE001 - report per-message failures without secrets.
                    rows.append(_error_row("imap_fetch_error", exc, config, uid=uid))
    except Exception as exc:  # noqa: BLE001 - anything else is unexpected, but still stable.
        rows.append(_error_row("unexpected_error", exc, config))
    finally:
        try:
            if imap is not None:
                imap.logout()
        finally:
            write_reports_safely(
                rows, config.output_dir, config.report_json, config.report_csv,
                command_args, config, diagnostics,
            )
    return rows
