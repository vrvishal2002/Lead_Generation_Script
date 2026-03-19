@echo off
:: =============================================
:: Automated Windows setup for Selenium & Chrome
:: =============================================

:: 1. Update pip
python -m pip install --upgrade pip

:: 2. Install Python packages
pip install selenium webdriver-manager wordfreq names-dataset email-validator tldextract dnspython beautifulsoup4

:: 3. Download & install Google Chrome silently
set TEMP_CHROME=%TEMP%\chrome_installer.exe
powershell -Command "Invoke-WebRequest -Uri 'https://dl.google.com/chrome/install/latest/chrome_installer.exe' -OutFile '%TEMP_CHROME%'"
start /wait %TEMP_CHROME% /silent /install

:: 4. Cleanup installer
del %TEMP_CHROME%

:: 5. Done message
echo ===================================================
echo Setup complete! Use Python with Selenium & ChromeDriver.
echo ===================================================
pause
