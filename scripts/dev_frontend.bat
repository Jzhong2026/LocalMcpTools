@echo off
REM Start the Angular dev server with a proxy to the local FastAPI.
REM Prereqs: ``npm install`` in ui/ and ``localmcptools start --http`` running.

setlocal

set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%\..\ui"

if not exist "node_modules" (
    echo [dev_frontend] ui/node_modules missing — running "npm install" first.
    call npm install
    if errorlevel 1 (
        echo [dev_frontend] npm install failed.
        popd
        exit /b 1
    )
)

REM The proxy.conf.json forwards /api and /mcp to http://127.0.0.1:7890.
call npx ng serve --proxy-config proxy.conf.json

popd
endlocal