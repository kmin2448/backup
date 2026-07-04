@echo off
chcp 65001 >nul
REM ============================================================
REM  sync_app.py 를 단일 .exe 파일로 빌드합니다.
REM  결과물: dist\폴더동기화.exe  (더블클릭으로 실행 가능)
REM ============================================================
cd /d "%~dp0"

REM Python 실행기 결정 (py 런처 우선)
set PYEXE=
where py >nul 2>nul && set PYEXE=py
if "%PYEXE%"=="" ( where python >nul 2>nul && set PYEXE=python )
if "%PYEXE%"=="" (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
    pause
    exit /b 1
)

echo [1/2] PyInstaller / customtkinter / pyzipper 설치 확인 / 설치 중...
%PYEXE% -m pip install --upgrade pyinstaller customtkinter pyzipper
if errorlevel 1 (
    echo [오류] 라이브러리 설치에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo [2/2] exe 빌드 중... (시간이 조금 걸립니다)
REM  --onefile     : 하나의 exe로
REM  --windowed    : 콘솔 창 없이 GUI만 표시
REM  --name        : 결과 파일 이름
REM  assets 폴더(글꼴 등)가 있으면 exe 안에 함께 포함 (Windows 구분자는 ; )
set ADDDATA=
if exist "assets\*" set ADDDATA=--add-data "assets;assets"
%PYEXE% -m PyInstaller --onefile --windowed --name "폴더동기화" --collect-all customtkinter %ADDDATA% sync_app.py
if errorlevel 1 (
    echo [오류] 빌드에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  빌드 완료!  dist\폴더동기화.exe 를 실행하세요.
echo ============================================================
pause
