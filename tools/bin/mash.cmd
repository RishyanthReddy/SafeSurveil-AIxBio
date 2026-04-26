@echo off
setlocal
python "%~dp0wsl_tool_bridge.py" mash %*
exit /b %ERRORLEVEL%
