@echo off
REM Build the Angular SPA and copy the bundle into src/localmcptools/ui_assets/.
REM
REM Prereqs: ``npm install`` in the ui/ folder (one-time). The script
REM is idempotent — running it twice produces the same output dir.

setlocal

set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%\..\ui"

if not exist "node_modules" (
    echo [build_frontend] ui/node_modules missing — running "npm install" first.
    call npm install
    if errorlevel 1 (
        echo [build_frontend] npm install failed.
        popd
        exit /b 1
    )
)

call npx ng build --configuration production
if errorlevel 1 (
    echo [build_frontend] ng build failed.
    popd
    exit /b 1
)

REM angular.json declares outputPath = "../src/localmcptools/ui_assets"
REM so the bundle lands in the right place automatically.
echo [build_frontend] build complete. Bundle at src\localmcptools\ui_assets\

popd
endlocal