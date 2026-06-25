@echo off
chcp 65001 >nul
REM 폴더 동기화 프로그램 실행 (더블클릭으로 실행하세요)
cd /d "%~dp0"

REM Python 실행기 결정 (py 런처 우선, 없으면 python)
set PYEXE=
where py >nul 2>nul && set PYEXE=py
if "%PYEXE%"=="" ( where python >nul 2>nul && set PYEXE=python )
if "%PYEXE%"=="" (
    echo.
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python을 설치한 뒤 다시 실행하세요.
    echo 설치 시 "Add Python to PATH" 옵션을 꼭 체크하세요.
    echo.
    pause
    goto :end
)

REM customtkinter 가 없으면 설치
%PYEXE% -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo customtkinter 설치 중...
    %PYEXE% -m pip install customtkinter
)

%PYEXE% sync_app.py

:end
