@echo off
REM ============================================
REM  POS Middleware - Windows 단일 exe 빌드 스크립트
REM  사용법: build.bat 를 더블클릭하거나 cmd 에서 실행
REM ============================================
cd /d "%~dp0"

echo [1/4] 의존성 설치 중 (pyusb, pyinstaller)...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [2/4] PyInstaller 단일 exe 빌드 중...
python -m PyInstaller --noconfirm --clean --onefile ^
  --name POSMiddleware ^
  --hidden-import usb.backends.libusb1 ^
  --hidden-import usb.backends.libusb0 ^
  --hidden-import usb.backends.openusb ^
  main.py
if errorlevel 1 goto :error

echo [3/4] config.json 복사 중...
copy /Y config.json dist\config.json >nul
if errorlevel 1 goto :error

echo [4/4] 완료
echo.
echo 빌드 결과: dist\POSMiddleware.exe
echo.
echo 실행 전 확인 사항:
echo   1) dist\config.json 의 vid/pid 를 실제 프린터 값으로 수정
echo      (장치 관리자 - 해당 USB 장치 - 속성 - 자세히 - 하드웨어 ID 에서 확인)
echo   2) libusb 드라이버(Zadig 등)가 프린터 USB 에 설치되어 있는지 확인
echo   3) libusb-1.0.dll 이 exe 와 같은 폴더 또는 시스템 PATH 에 있는지 확인
echo.
pause
exit /b 0

:error
echo.
echo [오류] 빌드가 실패했습니다. 위 로그를 확인해주세요.
pause
exit /b 1
