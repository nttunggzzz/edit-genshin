@echo off
setlocal

echo ====================================================
echo   KHOI DONG TRINH DUYET CDP CHO GHEP ANH GENSHIN
echo ====================================================
echo.

:: 1. Kiem tra cong 9222 co bi chiem boi tien trinh khac khong
netstat -ano | findstr /R /C:":9222 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [LOI] Cong 9222 dang bi chiem boi mot tien trinh hoac trinh duyet khac!
    echo He thong tuyet doi khong tat trinh duyet ca nhan cua ban de gianh cong.
    echo Vui long dong cua so trinh duyet cu hoac chuong trinh dang dung cong 9222 roi chay lai.
    echo.
    pause
    exit /b 1
)

:: 2. Xac dinh thu muc profile tam rieng biet theo ma acc
set "ACC_CODE=%~1"
if "%ACC_CODE%"=="" (
    if exist "data\acc-card\web-out\current_acc_code.txt" (
        for /f "usebackq delims=" %%A in ("data\acc-card\web-out\current_acc_code.txt") do set "ACC_CODE=%%A"
    ) else if exist "web-out\current_acc_code.txt" (
        for /f "usebackq delims=" %%A in ("web-out\current_acc_code.txt") do set "ACC_CODE=%%A"
    )
)
if "%ACC_CODE%"=="" set "ACC_CODE=temp"
set "CDP_PROFILE=%TEMP%\gacard_cdp_%ACC_CODE%"

:: 3. Tim duong dan Google Chrome hoac Playwright Chromium
set BROWSER_EXE=
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE (
    for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\ms-playwright\chrome.exe" 2^>nul') do (
        if not defined BROWSER_EXE set "BROWSER_EXE=%%F"
    )
)

if not defined BROWSER_EXE (
    echo [LOI] Khong tim thay Google Chrome hoac Playwright Chromium tren may!
    echo Vui long cai Google Chrome hoac chay lenh: playwright install chromium
    echo.
    pause
    exit /b 1
)

echo [OK] Trinh duyet tim thay: %BROWSER_EXE%
echo [OK] Cong CDP: 9222
echo [OK] Profile tam rieng biet: %CDP_PROFILE%
echo.
echo ====================================================
echo LUU Y QUAN TRONG:
echo 1. KHONG tu go tai khoan, mat khau vao trinh duyet nay!
echo    Bot se tu dong ket noi va dien form cho ban.
echo 2. Neu HoYoLAB hien Captcha keo truot, ban chi can keo truc
echo    tiep thanh truot tren cua so trinh duyet nay.
echo ====================================================
echo.
echo Dang mo trinh duyet...

start "" "%BROWSER_EXE%" --remote-debugging-port=9222 --user-data-dir="%CDP_PROFILE%" --window-size=1280,800 --no-first-run --no-default-browser-check https://www.hoyolab.com

exit /b 0
