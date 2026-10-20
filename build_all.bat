@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 1. Installing Python packages ===
python -m pip install -r requirements.txt || goto :error
echo === 2. Downloading deals data (a few hundred MB, takes a while) ===
python pipeline\download_data.py || goto :error
python pipeline\fetch_cbs_index.py || goto :error
echo === 3. Training the model and exporting site data ===
python pipeline\build_model.py || goto :error
echo === 4. Checking the website matches the model ===
python tests\parity_test.py
echo.
echo Done. Accuracy report: data\report.txt
echo To view the site run: preview_site.bat
pause
exit /b 0
:error
echo.
echo Something failed. See the error above.
pause
exit /b 1
