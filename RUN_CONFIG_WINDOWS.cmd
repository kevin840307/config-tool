@echo off
setlocal
python "%~dp0config_tool.py" %*
exit /b %ERRORLEVEL%
