@echo off
chcp 65001 >nul
REM 폴더 동기화 프로그램 실행 (더블클릭으로 실행하세요)
cd /d "%~dp0"

REM Python 실행 파일 찾기 (py 런처 우선, 없으면 python)
where py >nul 2>nul
if %errorlevel%==0 (
    py sync_app.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python sync_app.py
    goto :end
)

echo.
echo [오류] Python이 설치되어 있지 않습니다.
echo https://www.python.org/downloads/ 에서 Python을 설치한 뒤 다시 실행하세요.
echo 설치 시 "Add Python to PATH" 옵션을 꼭 체크하세요.
echo.
pause

:end
