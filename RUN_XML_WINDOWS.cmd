@echo off
setlocal
python "%~dp0xml_config_tool.py" %*
exit /b %errorlevel%
