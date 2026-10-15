"""Command line interface for MailGain."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mailgain import __version__
from mailgain.stage1_email import (
    DEFAULT_PASSWORD_ENV,
    DEFAULT_USERNAME_ENV,
    CollectorConfig,
    ReportRow,
    collect_stage1_email,
    validate_since,
    validate_student_id_core_regex,
    write_reports_safely,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailgain",
        description="Collect and audit CodeSetArena email submissions.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    teacher_parser = subparsers.add_parser("teacher", help="Teacher and TA workflows.")
    teacher_subparsers = teacher_parser.add_subparsers(dest="teacher_command")

    stage1_parser = teacher_subparsers.add_parser("stage1", help="Stage 1 workflows.")
    stage1_subparsers = stage1_parser.add_subparsers(dest="stage1_command")

    collect_parser = stage1_subparsers.add_parser(
        "collect-email",
        help="Collect Stage 1 submission packages from email.",
    )
    collect_parser.add_argument("--provider", default="163", choices=["163"], help="Mail provider.")
    collect_parser.add_argument(
        "--imap-host",
        "--host",
        dest="imap_host",
        default="imap.163.com",
        help="IMAP SSL host. Default: imap.163.com.",
    )
    collect_parser.add_argument(
        "--imap-port",
        "--port",
        dest="imap_port",
        type=int,
        default=993,
        help="IMAP SSL port. Default: 993.",
    )
    collect_parser.add_argument(
        "--username",
        help=f"Teacher mailbox address. If omitted, read from ${DEFAULT_USERNAME_ENV}.",
    )
    collect_parser.add_argument(
        "--username-env",
        default=DEFAULT_USERNAME_ENV,
        help=(
            "Environment variable used when --username is omitted. "
            f"Default: {DEFAULT_USERNAME_ENV}."
        ),
    )
    collect_parser.add_argument(
        "--password-env",
        default=DEFAULT_PASSWORD_ENV,
        help=(
            "Environment variable containing the mailbox auth code. "
            f"Default: {DEFAULT_PASSWORD_ENV}."
        ),
    )
    collect_parser.add_argument("--mailbox", default="INBOX", help="Mailbox to select read-only.")
    collect_parser.add_argument(
        "--output-dir",
        default="submissions/stage1",
        help="Root directory for accepted downloads and default reports.",
    )
    collect_parser.add_argument("--report-json", help="Optional JSON report path.")
    collect_parser.add_argument("--report-csv", help="Optional CSV report path.")
    collect_parser.add_argument(
        "--student-id-core-regex",
        default=r"\d{11}",
        help=(
            "Core regex for the student ID inside the subject. The only accepted "
            "attachment filename is bigdata_p1_{student_id}.tar.gz, where "
            "{student_id} matches this regex and equals the subject student ID."
        ),
    )
    collect_parser.add_argument("--since", help="ISO date YYYY-MM-DD used for IMAP SINCE.")
    collect_parser.add_argument(
        "--timeout", type=int, default=30, help="IMAP connection timeout seconds."
    )
    collect_parser.add_argument(
        "--dry-run",
        dest="download",
        action="store_false",
        help="Scan and write reports without downloading attachments. This is the default mode.",
    )
    collect_parser.add_argument(
        "--download",
        dest="download",
        action="store_true",
        help="Download only the single validated Stage 1 attachment part for each accepted email.",
    )
    collect_parser.set_defaults(download=False)
    collect_parser.add_argument(
        "--duplicate-policy",
        choices=["manual", "skip", "overwrite", "keep-newest", "preserve-review"],
        default="preserve-review",
    )
    collect_parser.set_defaults(func=handle_collect_email)

    return parser


def _resolve_username(args: argparse.Namespace) -> str:
    if args.username:
        return args.username
    return os.environ.get(args.username_env, "")


def _command_args(args: argparse.Namespace) -> dict[str, object]:
    return {
        "provider": args.provider,
        "imap_host": args.imap_host,
        "imap_port": args.imap_port,
        "username_env": args.username_env,
        "password_env": args.password_env,
        "mailbox": args.mailbox,
        "output_dir": args.output_dir,
        "report_json": args.report_json,
        "report_csv": args.report_csv,
        "student_id_core_regex": args.student_id_core_regex,
        "since": args.since,
        "timeout": args.timeout,
        "dry_run": not args.download,
        "duplicate_policy": args.duplicate_policy,
    }


def _write_cli_error_report(args: argparse.Namespace, reason: str) -> None:
    output_dir = Path(getattr(args, "output_dir", "submissions/stage1"))
    report_json = getattr(args, "report_json", None)
    report_csv = getattr(args, "report_csv", None)
    write_reports_safely(
        [ReportRow(action="error", reason=reason)],
        output_dir,
        Path(report_json) if report_json else None,
        Path(report_csv) if report_csv else None,
        _command_args(args),
    )


def handle_collect_email(args: argparse.Namespace) -> int:
    try:
        validate_student_id_core_regex(args.student_id_core_regex)
    except Exception:  # noqa: BLE001 - argparse error should stay concise.
        _write_cli_error_report(args, "invalid_student_id_core_regex")
        print("error: invalid --student-id-core-regex", file=sys.stderr)
        return 2
    try:
        validate_since(args.since)
    except ValueError:
        _write_cli_error_report(args, "invalid_since")
        print("error: invalid --since; expected YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        _write_cli_error_report(args, "invalid_timeout")
        print("error: --timeout must be positive", file=sys.stderr)
        return 2

    username = _resolve_username(args)
    if not username:
        _write_cli_error_report(args, "env_username_missing")
        print(
            f"error: missing username; set ${args.username_env} or pass --username",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get(args.password_env):
        _write_cli_error_report(args, "env_auth_code_missing")
        print(f"error: missing password environment variable: {args.password_env}", file=sys.stderr)
        return 2

    config = CollectorConfig(
        provider=args.provider,
        imap_host=args.imap_host,
        imap_port=args.imap_port,
        username=username,
        password_env=args.password_env,
        mailbox=args.mailbox,
        output_dir=Path(args.output_dir),
        student_id_core_regex=args.student_id_core_regex,
        since=args.since,
        dry_run=not args.download,
        duplicate_policy=args.duplicate_policy,
        timeout=args.timeout,
        report_json=Path(args.report_json) if args.report_json else None,
        report_csv=Path(args.report_csv) if args.report_csv else None,
        command_args=_command_args(args),
    )
    rows = collect_stage1_email(config)
    print(f"wrote reports to {config.output_dir}")
    print(f"processed report rows: {len(rows)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
