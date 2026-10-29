@echo off
rem Install, update or remove specloop in a project (Windows cmd / PowerShell).
rem Usage: install.cmd [TARGET] [options]   (see install.cmd --help)
setlocal
set "DIR=%~dp0"
if defined SPECLOOP_PYTHON (
  "%SPECLOOP_PYTHON%" "%DIR%install.py" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul && (
  py -3 "%DIR%install.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul && (
  python "%DIR%install.py" %*
  exit /b %ERRORLEVEL%
)
echo specloop: Python 3.8+ is required (or set SPECLOOP_PYTHON) 1>&2
exit /b 1
