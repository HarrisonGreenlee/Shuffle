@echo off
setlocal enabledelayedexpansion

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

set INTERVALDB_INCLUDE=%CD%\ncls\ncls\src
echo [DEBUG] Using intervaldb include path: %INTERVALDB_INCLUDE%

del /q temporal_contact_matrix.dll temporal_contact_matrix.lib temporal_contact_matrix.exp temporal_contact_matrix.obj intervaldb.obj 2>nul

:: -------- Release flags --------
:: /MD  = release CRT (no *D.dll deps)
:: /O2  = optimize
:: /DNDEBUG optional
:: /Zi  keep symbols (optional)
set CFLAGS=/c /O2 /MD /DNDEBUG /Zi /Z7 /nologo /W4 /GS /Gy /FC

echo [INFO] Compiling intervaldb.c (Release)...
cl %CFLAGS% ^
   /DBUILD_C_LIBRARY ^
   "%INTERVALDB_INCLUDE%\intervaldb.c" ^
   /I"%INTERVALDB_INCLUDE%" ^
   /Fo:intervaldb.obj
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] intervaldb.c failed & exit /b )

echo [INFO] Compiling temporal_contact_matrix.c (Release)...
cl %CFLAGS% ^
   /DBUILD_C_LIBRARY ^
   temporal_contact_matrix.c ^
   /I"%INTERVALDB_INCLUDE%" ^
   /Fo:temporal_contact_matrix.obj
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] temporal_contact_matrix.c failed & exit /b )

echo [INFO] Linking temporal_contact_matrix.dll (Release)...
link /DLL /INCREMENTAL:NO /NOLOGO ^
     /OUT:temporal_contact_matrix.dll ^
     /DEBUG ^
     temporal_contact_matrix.obj intervaldb.obj ^
     kernel32.lib user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib comdlg32.lib advapi32.lib
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] Linking failed! & exit /b )

echo [SUCCESS] temporal_contact_matrix.dll built (Release).

pause
