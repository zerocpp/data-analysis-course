@echo off
rem BigData v10.1.13 student wrapper (Windows): course CLI inside the local container.
cd /d "%~dp0.."
docker compose up -d >NUL
docker compose exec -T codesetarena datac %*
