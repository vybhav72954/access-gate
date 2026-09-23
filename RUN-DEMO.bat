@echo off
rem ============================================================================
rem  The Access Gate - one-click demo
rem
rem  Double-click this file. It serves the built evidence viewer on localhost,
rem  opens it in the default browser, and shuts the server down when you close
rem  it. Nothing is installed, no key is needed and nothing reaches the network.
rem
rem  Why it serves rather than just opening the HTML: browsers refuse to load ES
rem  modules over file://, so opening frontend\build\index.html directly renders
rem  every page in full but leaves the filters, the map selection, the threshold
rem  slider and the theme toggle dead on the screen. A demo that gets clicked
rem  has to be served.
rem
rem  RUN-DEMO.bat /smoke   starts it, checks it answers, shuts down, reports.
rem                        Use that to verify the demo before you rely on it.
rem ============================================================================

setlocal EnableExtensions
title The Access Gate - demo
cd /d "%~dp0"

set "SMOKE="
if /i "%~1"=="/smoke" set "SMOKE=1"
rem What this file exits with. /smoke is only worth running if it can fail.
set "RC=0"

echo.
echo   The Access Gate
echo   PM-JAY hospital-fraud decisions - evidence viewer
echo   ------------------------------------------------
echo.

rem --- 1. a build to serve ----------------------------------------------------
rem frontend\build\ is generated, so it is not in the repository. On a fresh
rem clone this is the one step that needs Node and the network.
if exist "frontend\build\index.html" goto have_build

echo   No build found - building it now.
echo   ^(Needs Node.js. First run also downloads dependencies: a few minutes.^)
echo.
where npm >nul 2>&1
if errorlevel 1 goto no_npm

pushd "frontend"
if not exist "node_modules" (
    echo   Installing dependencies...
    call npm install
    if errorlevel 1 ( popd & goto build_failed )
)
echo   Building...
call npm run build
if errorlevel 1 ( popd & goto build_failed )
popd

if not exist "frontend\build\index.html" goto build_failed
echo   Built.
echo.

:have_build

rem --- 2. something to serve it with ------------------------------------------
rem Python first: it is already a dependency of this project, http.server needs
rem no install, and it serves the folder exactly as it sits on disk. Any Python
rem on PATH will do - the viewer is static files, nothing is imported from here.
set "PY="
for %%P in (py python python3) do (
    if not defined PY (
        %%P -c "import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)" >nul 2>&1 && set "PY=%%P"
    )
)

if not defined PY goto try_node

rem --- 3. a free port ---------------------------------------------------------
rem 4173 is Vite's preview port; walking upwards keeps a second copy of the demo,
rem or a dev server someone left running, from colliding with this one.
set "PORT="
for /L %%N in (4173,1,4188) do (
    if not defined PORT (
        netstat -ano | findstr /r /c:":%%N  *[0-9.]*:.*LISTENING" >nul 2>&1 || set "PORT=%%N"
    )
)
if not defined PORT (
    echo   Could not find a free port between 4173 and 4188.
    echo   Close whatever is using them and try again.
    set "RC=1"
    goto finish
)

rem --- 4. serve, wait for it, open it -----------------------------------------
echo   Starting the viewer on port %PORT% ...
start "access-gate-demo" /min "%PY%" -m http.server %PORT% --bind 127.0.0.1 --directory "frontend\build"

set "URL=http://127.0.0.1:%PORT%/"
set "READY="
where curl >nul 2>&1
if errorlevel 1 (
    rem No curl (pre-1803 Windows): no way to poll, so just give it a moment.
    timeout /t 3 /nobreak >nul
    set "READY=1"
) else (
    for /L %%I in (1,1,40) do (
        if not defined READY (
            curl -s -o nul --max-time 2 "%URL%" && set "READY=1"
            if not defined READY timeout /t 1 /nobreak >nul
        )
    )
)

if not defined READY (
    echo.
    echo   The server did not answer on %URL%
    echo   Something blocked it - a firewall prompt, or the port was taken
    echo   between the check and the start. Try running this file again.
    call :stop_server
    set "RC=1"
    goto finish
)

if defined SMOKE (
    echo   OK - the viewer answered on %URL%
    call :check_page "/"            "Decided cases"
    call :check_page "/evaluation/" "Where the load falls"
    call :check_page "/benchmark/"  "Rules versus the crew"
    call :check_page "/case/CLM-S02/" "Protected"
    call :stop_server
    echo.
    if defined SMOKE_FAIL (
        echo   SMOKE TEST FAILED - a page did not carry what it should.
        echo   The build is stale or broken. Rebuild it:
        echo     cd frontend ^&^& npm run verify
        set "RC=1"
    ) else (
        echo   Smoke test done. The demo is good to click.
    )
    goto finish
)

echo   Opening %URL%
start "" "%URL%"

echo.
echo   ========================================================================
echo     The demo is running.        %URL%
echo.
echo     Cases       the ten decided cases, and where they are on the map
echo     Evaluation  the 5,000-case run, the map, and the threshold slider
echo     Benchmark   the rules against the crew, 40 hand-written cases
echo.
echo     Leave this window open. Closing it, or pressing a key, stops the
echo     server - the browser tab will then show nothing.
echo   ========================================================================
echo.
pause >nul

call :stop_server
echo   Stopped.

:finish
echo.
if not defined SMOKE (
    echo   Press any key to close.
    pause >nul
)
rem %RC% is expanded as this line is read, which is before endlocal discards it.
endlocal & exit /b %RC%

rem ---------------------------------------------------------------------------
:stop_server
rem Kill by port, not by name. Other Python processes on this machine are
rem somebody else's work and must not be touched.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b 0

rem ---------------------------------------------------------------------------
:check_page
rem %~1 path, %~2 a phrase that page must contain. Proves the page was really
rem prerendered, not that the server merely returned something.
curl -s --max-time 5 "http://127.0.0.1:%PORT%%~1" | findstr /c:"%~2" >nul 2>&1
if errorlevel 1 (
    echo   FAIL %~1 did not contain "%~2"
    rem Remembered, not just printed: a check that reports success after printing
    rem FAIL is worse than no check, because it is run precisely when the demo is
    rem about to be trusted.
    set "SMOKE_FAIL=1"
) else (
    echo   OK   %~1
)
exit /b 0

rem ---------------------------------------------------------------------------
:try_node
rem No Python. Vite can serve its own build, and Node must already be here
rem because the build exists. Ctrl+C stops this one; there is no PID to chase.
where npm >nul 2>&1
if errorlevel 1 goto no_runtime
echo   No Python found - falling back to "npm run preview".
echo   Press Ctrl+C in this window to stop it.
echo.
pushd "frontend"
call npm run preview -- --open
popd
goto finish

rem ---------------------------------------------------------------------------
:no_npm
echo   This is a fresh checkout with no build in it, and Node.js is not
echo   installed, so there is nothing to serve and no way to make it.
echo.
echo   Either install Node.js ^(nodejs.org^) and run this file again, or ask
echo   for a copy of the frontend\build folder and drop it in place.
set "RC=1"
goto finish

:no_runtime
echo   Neither Python nor Node.js is on PATH, so there is nothing to serve
echo   the viewer with. Install either one and run this file again.
echo.
echo   Last resort, no install needed: open
echo     "%~dp0frontend\build\index.html"
echo   Every page renders in full, but the filters, the map selection and the
echo   threshold slider will not respond - browsers will not load modules from
echo   a file:// page.
set "RC=1"
goto finish

:build_failed
echo.
echo   The build failed. Run it by hand to see why:
echo     cd "%~dp0frontend"
echo     npm install
echo     npm run build
set "RC=1"
goto finish
