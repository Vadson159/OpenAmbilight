@echo off
echo ========================================
echo  Ambilight for OpenRGB - Setup
echo ========================================
echo.

echo [1/2] Checking Python...
python --version 2>NUL
if errorlevel 1 (
    echo ERROR: Python not found! Install from python.org
    pause
    exit /b 1
)
echo OK
echo.

echo [2/2] Installing dependencies...
python -m pip install mss numpy openrgb-python pystray pillow soundcard keyboard hidapi
if errorlevel 1 (
    echo.
    echo WARNING: Failed to install packages. Make sure pip is updated.
    pause
    exit /b 1
)
echo.

echo ========================================
echo  Setup Complete! Next steps:
echo ========================================
echo.
echo  1. Open OpenRGB
echo  2. Go to the "SDK Server" tab
echo  3. Click "Start Server"
echo.
echo  4. Run in terminal to test colors:
echo     python Ambilight.pyw --test
echo.
echo  5. Run Ambilight:
echo     double-click Ambilight.pyw
echo.
echo  (Don't forget to configure LED sizes in OpenRGB if 
echo  your fans are connected to the motherboard!)
echo.
pause
