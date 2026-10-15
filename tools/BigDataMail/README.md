# MailGain

MailGain is a teacher-side CLI for collecting CodeSetArena Stage 1 submission packages
from email.

The project is intentionally created as a fresh `src`-layout Python package. Use the
project-local virtual environment and run commands through `uv`.

```bash
uv run python -m mailgain.cli --help
uv run pytest
uv run ruff check .
```

The Stage 1 collector must keep the security boundary strict: student IDs are only
extracted from email subjects, matching attachments must use the exact
`bigdata_p1_{student_id}.tar.gz` filename, and downloads are only allowed
for validated MIME parts.
