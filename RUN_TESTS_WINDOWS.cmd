@echo off
setlocal
python -m pytest -q
exit /b %ERRORLEVEL%
