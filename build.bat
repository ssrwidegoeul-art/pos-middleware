@echo off
REM ============================================
REM  POS Middleware - Windows 단일 exe 빌드 스크립트
REM  사용법: build.bat 를 더블클릭하거나 cmd 에서 실행
REM ============================================
cd /d "%~dp0"

echo [1/3] 의존성 설치 중 (pyinstaller)...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [2/3] PyInstaller 단일 exe 빌드 중...
python -m PyInstaller --noconfirm --clean --onefile --name pos-middleware main.py
if errorlevel 1 goto :error

echo [3/3] 실행 폴더 구성 중...
copy /Y config.json dist\config.json >nul
copy /Y "설치방법.txt" dist\설치방법.txt >nul
if errorlevel 1 goto :error

echo.
echo 빌드 완료: dist\pos-middleware.exe
echo 함께 생성된 파일: dist\config.json, dist\설치방법.txt
echo.
echo 사용 전 확인:
echo   1) dist\config.json 의 printer.ip 를 실제 프린터 IP 로 수정
echo   2) 포스 프로그램(또는 Windows 프린터)의 프린터 주소를 127.0.0.1:9100 으로 변경
echo      자세한 내용은 설치방법.txt 참고
echo.
pause
exit /b 0

:error
echo.
echo [오류] 빌드가 실패했습니다. 위 로그를 확인해주세요.
pause
exit /b 1
