@echo off
cd /d "%~dp0.."
call bin\datac.bat doctor --workspace /workspace %*
