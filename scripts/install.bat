@echo off
rem BigData offline install (Windows): load image, start stack, doctor.
cd /d "%~dp0.."
set /p VERSION=<VERSION
echo [install] loading bigdata-student:%VERSION% image ...
docker load -i images\bigdata-student-%VERSION%.image.tar
echo [install] starting container ...
docker compose up -d
echo [install] done. Use bin\datac.bat --help
