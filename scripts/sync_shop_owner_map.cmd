@echo off
setlocal
cd /d "%~dp0.."
py -3 scripts\sync_shop_owner_map.py %*
endlocal
