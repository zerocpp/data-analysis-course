#!/usr/bin/env bash
#
# Collect CodeSetArena Stage 1 submission packages from a real 163 mailbox.
#
# Security boundaries enforced by this script:
#   * The 163 auth code is NEVER printed, logged, echoed, or passed as a CLI
#     argument. It is consumed only through the CODESETARENA_163_AUTH_CODE
#     environment variable, which the underlying CLI reads via --password-env.
#   * Default mode is dry-run. Attachments are only fetched when --download is
#     given explicitly AND a second interactive confirmation ("DOWNLOAD") is
#     typed. --download is refused in non-interactive environments.
#   * All arguments are built as a Bash array; eval is never used.
#   * Reports are written to a unique per-run directory protected by umask 077.

set -Eeuo pipefail
umask 077

readonly AUTH_CODE_ENV="CODESETARENA_163_AUTH_CODE"
readonly USERNAME_ENV="MAILGAIN_163_USERNAME"
readonly SINCE_ENV="MAILGAIN_SINCE"
readonly DEFAULT_MAILBOX="INBOX"
readonly DEFAULT_DUPLICATE_POLICY="preserve-review"
readonly IMAP_HOST="imap.163.com"
readonly IMAP_PORT="993"
readonly TIMEOUT="30"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly SCRIPT_DIR PROJECT_ROOT
readonly DEFAULT_OUTPUT_DIR="${PROJECT_ROOT}/submissions/stage1"

# Tracks whether this script read the auth code itself (so it can unset on exit).
AUTH_CODE_READ_INTERNALLY=0

usage() {
    cat <<'EOF'
Usage: collect-stage1-163.sh [OPTIONS]

Collect CodeSetArena Stage 1 submission packages from a 163 mailbox via IMAP.

Options:
  --username ADDR        Teacher/TA mailbox address (e.g. teacher@163.com).
                          Fallback: $MAILGAIN_163_USERNAME. Required.
  --since YYYY-MM-DD     Scan mails since this date (required for real runs).
                          Fallback: $MAILGAIN_SINCE.
  --mailbox NAME         Mailbox to select read-only. Default: INBOX.
  --output-dir DIR       Root directory for accepted attachment downloads.
                          Default: <project>/submissions/stage1.
  --duplicate-policy POLICY  manual | skip | preserve-review.
                          Default: preserve-review.
                          overwrite and keep-newest are not permitted in real runs.
  --download             Enable attachment download (second confirmation required).
                          Without this flag only a dry-run scan + report is done.
  -h, --help             Show this help and exit.

Auth code:
  The auth code is read ONLY from $CODESETARENA_163_AUTH_CODE. If that variable
  is unset and stdin is a terminal, it is prompted (hidden) once. It is never
  printed, logged, or passed on the command line.
EOF
}

on_error() {
    local _exit_code=$?
    echo "error: failure at line ${1:-unknown} (exit ${_exit_code})" >&2
}

on_exit() {
    if [[ "${AUTH_CODE_READ_INTERNALLY}" -eq 1 && -n "${AUTH_CODE_ENV:-}" ]]; then
        unset "${AUTH_CODE_ENV}" 2>/dev/null || true
    fi
}

trap 'on_error $LINENO' ERR
trap on_exit EXIT

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
USERNAME=""
SINCE=""
MAILBOX="${DEFAULT_MAILBOX}"
OUTPUT_DIR="${DEFAULT_OUTPUT_DIR}"
DUPLICATE_POLICY="${DEFAULT_DUPLICATE_POLICY}"
DO_DOWNLOAD=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --username)
            [[ $# -ge 2 ]] || { echo "error: --username requires a value" >&2; exit 2; }
            USERNAME="$2"
            shift 2
            ;;
        --since)
            [[ $# -ge 2 ]] || { echo "error: --since requires a value" >&2; exit 2; }
            SINCE="$2"
            shift 2
            ;;
        --mailbox)
            [[ $# -ge 2 ]] || { echo "error: --mailbox requires a value" >&2; exit 2; }
            MAILBOX="$2"
            shift 2
            ;;
        --output-dir)
            [[ $# -ge 2 ]] || { echo "error: --output-dir requires a value" >&2; exit 2; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --duplicate-policy)
            [[ $# -ge 2 ]] || { echo "error: --duplicate-policy requires a value" >&2; exit 2; }
            DUPLICATE_POLICY="$2"
            shift 2
            ;;
        --download)
            DO_DOWNLOAD=1
            shift
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve username (required; may appear in summaries; auth code never may)
# ---------------------------------------------------------------------------
if [[ -z "${USERNAME}" ]]; then
    USERNAME="${!USERNAME_ENV:-}"
fi
if [[ -z "${USERNAME}" ]]; then
    echo "error: missing username; pass --username or set \$${USERNAME_ENV}" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Resolve since (required for real runs to avoid scanning the whole mailbox)
# ---------------------------------------------------------------------------
if [[ -z "${SINCE}" ]]; then
    SINCE="${!SINCE_ENV:-}"
fi
if [[ -z "${SINCE}" ]]; then
    echo "error: missing --since (YYYY-MM-DD) or \$${SINCE_ENV}; required for real runs" >&2
    exit 2
fi
if [[ ! "${SINCE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
    || ! date -d "${SINCE}" +%Y-%m-%d >/dev/null 2>&1; then
    echo "error: invalid --since '${SINCE}'; expected YYYY-MM-DD" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Validate duplicate policy
# ---------------------------------------------------------------------------
case "${DUPLICATE_POLICY}" in
    manual | skip | preserve-review) ;;
    overwrite | keep-newest)
        echo "error: --duplicate-policy '${DUPLICATE_POLICY}' is not permitted in real runs" >&2
        echo "       use preserve-review (default), manual, or skip" >&2
        exit 2
        ;;
    *)
        echo "error: invalid --duplicate-policy '${DUPLICATE_POLICY}'" >&2
        echo "       expected: manual | skip | preserve-review" >&2
        exit 2
        ;;
esac

# ---------------------------------------------------------------------------
# Resolve auth code (never printed, never on the command line)
# ---------------------------------------------------------------------------
if [[ -z "${!AUTH_CODE_ENV:-}" ]]; then
    if [[ -t 0 ]]; then
        printf 'Enter 163 auth code (input hidden): '
        IFS= read -r -s _entered_code
        echo
        if [[ -z "${_entered_code}" ]]; then
            echo "error: empty auth code" >&2
            unset _entered_code 2>/dev/null || true
            exit 2
        fi
        export "${AUTH_CODE_ENV}=${_entered_code}"
        unset _entered_code 2>/dev/null || true
        AUTH_CODE_READ_INTERNALLY=1
    else
        echo "error: \$${AUTH_CODE_ENV} is not set and stdin is not a terminal" >&2
        exit 2
    fi
fi

# ---------------------------------------------------------------------------
# Download gate: explicit flag + interactive second confirmation
# ---------------------------------------------------------------------------
if [[ "${DO_DOWNLOAD}" -eq 1 ]]; then
    if [[ ! -t 0 ]]; then
        echo "error: --download requires an interactive terminal for manual confirmation" >&2
        exit 2
    fi
    echo "Download confirmation required. Review the parameters below:"
    echo "  username:         ${USERNAME}"
    echo "  host:port:        ${IMAP_HOST}:${IMAP_PORT}"
    echo "  mailbox:          ${MAILBOX}"
    echo "  since:            ${SINCE}"
    echo "  output-dir:       ${OUTPUT_DIR}"
    echo "  duplicate-policy: ${DUPLICATE_POLICY}"
    echo "  mode:             DOWNLOAD (validated attachment parts will be saved)"
    echo "重复版本将保存在 duplicates/，旧文件不会被覆盖。"
    echo "The auth code is never displayed."
    printf 'Type PRESERVE_DUPLICATES to confirm, anything else to cancel: '
    IFS= read -r _confirm
    if [[ "${_confirm}" != "PRESERVE_DUPLICATES" ]]; then
        unset _confirm 2>/dev/null || true
        echo "cancelled."
        exit 1
    fi
    unset _confirm 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Build a unique per-run directory (UTC timestamp + mode) for reports
# ---------------------------------------------------------------------------
utc_ts="$(date -u +%Y%m%d-%H%M%S)"
mode_tag="dryrun"
[[ "${DO_DOWNLOAD}" -eq 1 ]] && mode_tag="download"
RUN_DIR="${PROJECT_ROOT}/runs/real-${mode_tag}-${utc_ts}"
_suffix=0
while [[ -e "${RUN_DIR}" ]]; do
    _suffix=$((_suffix + 1))
    RUN_DIR="${PROJECT_ROOT}/runs/real-${mode_tag}-${utc_ts}-${_suffix}"
done
mkdir -p "${RUN_DIR}"
readonly RUN_DIR
report_json="${RUN_DIR}/collect-report.json"
report_csv="${RUN_DIR}/collect-report.csv"
console_log="${RUN_DIR}/console.log"
summary_path="${RUN_DIR}/run-summary.txt"

if [[ "${DO_DOWNLOAD}" -eq 1 ]]; then
    run_mode="download"
else
    run_mode="dry-run"
fi

# ---------------------------------------------------------------------------
# Build and run the underlying CLI (array; no eval)
# ---------------------------------------------------------------------------
cmd=(uv run code-set-arena teacher stage1 collect-email)
cmd+=(--provider 163)
cmd+=(--host "${IMAP_HOST}")
cmd+=(--port "${IMAP_PORT}")
cmd+=(--username "${USERNAME}")
cmd+=(--password-env "${AUTH_CODE_ENV}")
cmd+=(--mailbox "${MAILBOX}")
cmd+=(--output-dir "${OUTPUT_DIR}")
cmd+=(--report-json "${report_json}")
cmd+=(--report-csv "${report_csv}")
cmd+=(--since "${SINCE}")
cmd+=(--timeout "${TIMEOUT}")
cmd+=(--duplicate-policy "${DUPLICATE_POLICY}")
if [[ "${DO_DOWNLOAD}" -eq 1 ]]; then
    cmd+=(--download)
else
    cmd+=(--dry-run)
fi

# Capture CLI exit code without aborting on its non-zero status.
set +e
"${cmd[@]}" 2>&1 | tee "${console_log}"
cli_exit="${PIPESTATUS[0]}"
set -e

# ---------------------------------------------------------------------------
# Write run summary (no auth code, no environment dump)
# ---------------------------------------------------------------------------
{
    echo "run_mode=${run_mode}"
    echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "username=${USERNAME}"
    echo "imap_host=${IMAP_HOST}"
    echo "imap_port=${IMAP_PORT}"
    echo "mailbox=${MAILBOX}"
    echo "since=${SINCE}"
    echo "output_dir=${OUTPUT_DIR}"
    echo "duplicate_policy=${DUPLICATE_POLICY}"
    echo "report_json=${report_json}"
    echo "report_csv=${report_csv}"
    echo "console_log=${console_log}"
    echo "cli_exit_code=${cli_exit}"
} > "${summary_path}"

# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------
if [[ "${cli_exit}" -ne 0 ]]; then
    echo "CLI failed with exit code ${cli_exit} (mode=${run_mode})." >&2
    echo "console log: ${console_log}" >&2
    exit "${cli_exit}"
fi

echo "reports written to:"
echo "  ${report_json}"
echo "  ${report_csv}"
echo "run mode: ${run_mode}"
echo "summary:  ${summary_path}"
