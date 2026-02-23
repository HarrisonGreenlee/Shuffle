@echo off
setlocal enabledelayedexpansion

:: ---------------------------------------------------------------
:: 1) Set up MSVC environment
:: ---------------------------------------------------------------
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

:: ---------------------------------------------------------------
:: 2) Set paths for NCLS (Modify if needed)
:: ---------------------------------------------------------------
set INTERVALDB_INCLUDE=%CD%\..\external_lib\ncls\ncls\src
set INTERVALDB_LIB=%CD%\..\external_lib\ncls\ncls\src
echo [DEBUG] Using intervaldb include path: %INTERVALDB_INCLUDE%

:: ---------------------------------------------------------------
:: 3) Clean previous build outputs
:: ---------------------------------------------------------------
if exist build_contact_graph.exe del build_contact_graph.exe
if exist build_contact_graph.obj del build_contact_graph.obj
if exist intervaldb.obj del intervaldb.obj

:: ---------------------------------------------------------------
:: 4) Compile NCLS component
:: ---------------------------------------------------------------
echo [INFO] Compiling intervaldb.c...
cl /c /Zi /Od /DEBUG /openmp /DBUILD_C_LIBRARY ^
    "%INTERVALDB_INCLUDE%\intervaldb.c" ^
    /I"%INTERVALDB_INCLUDE%" ^
    /Fo:intervaldb.obj

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to compile intervaldb.c
    pause
    exit /b
)

:: ---------------------------------------------------------------
:: 5) Compile main C program
:: ---------------------------------------------------------------
echo [INFO] Compiling build_contact_graph.c...
cl /c /Zi /Od /DEBUG /openmp /DBUILD_C_LIBRARY ^
    build_contact_graph.c ^
    /I"%INTERVALDB_INCLUDE%" ^
    /Fo:build_contact_graph.obj

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to compile build_contact_graph.c
    pause
    exit /b
)

:: ---------------------------------------------------------------
:: 6) Link objects into executable
:: ---------------------------------------------------------------
echo [INFO] Linking executable...
link /DEBUG /OUT:build_contact_graph.exe ^
    build_contact_graph.obj ^
    intervaldb.obj ^
    kernel32.lib user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib comdlg32.lib advapi32.lib ^
    /NODEFAULTLIB:libcmtd.lib

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Linking failed!
    pause
    exit /b
)

:: ---------------------------------------------------------------
:: 7) Run
:: ---------------------------------------------------------------
echo [INFO] Build successful. Running...
build_contact_graph.exe patient_paths.txt output.txt

pause
