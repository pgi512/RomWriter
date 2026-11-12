@echo off
rem ROM Writer 를 설치가 필요 없는 .exe 한 파일로 만듭니다. (Python 3 와 인터넷 연결이 필요합니다)
rem 결과: dist\ROMWriter.exe   (중간 파일은 %TEMP%\romwriter_build 에 만들고, 끝나면 지워도 됩니다)
cd /d "%~dp0"

python -m pip install --quiet --upgrade pyinstaller pyserial
if errorlevel 1 goto fail

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ROMWriter ^
  --icon "%~dp0assets\romwriter.ico" ^
  --add-data "%~dp0assets;assets" ^
  --distpath "%~dp0dist" ^
  --workpath "%TEMP%\romwriter_build" ^
  --specpath "%TEMP%\romwriter_build" ^
  "%~dp0romwriter_gui.py"
if errorlevel 1 goto fail

echo.
echo 완료: %~dp0dist\ROMWriter.exe
pause
exit /b 0

:fail
echo.
echo 빌드에 실패했습니다. 위의 오류 메시지를 확인하세요.
pause
exit /b 1
