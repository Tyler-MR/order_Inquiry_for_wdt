@echo off
setlocal
cd /d "%~dp0.."
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" scripts\sync_shop_owner_map.py %*
) else (
  py -3 scripts\sync_shop_owner_map.py %*
)
endlocal
