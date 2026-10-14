@echo off
rem BigData Runtime Resource Limits — One-Click Teacher Demo (Windows)
cd /d "%~dp0.."
docker compose up -d >NUL 2>&1
bin\datac.bat demo runtime --all --workspace /workspace %*
