@echo off
setlocal
python "%~dp0wsl_tool_bridge.py" amrfinder %*
exit /b %ERRORLEVEL%
