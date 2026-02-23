@echo off
setlocal enabledelayedexpansion

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"

set INTERVALDB_INCLUDE=%CD%\ncls\ncls\src
echo [DEBUG] Using intervaldb include path: %INTERVALDB_INCLUDE%

del /q temporal_contact_matrix.dll temporal_contact_matrix.lib temporal_contact_matrix.exp temporal_contact_matrix.obj intervaldb.obj 2>nul

:: -------- ASan-friendly common flags --------
set CFLAGS=/c /Zi /Od /MDd /fsanitize=address /Zi /Z7 /nologo /W4 /GS- /Gy- /FC
:: Tips:
:: /GS- because ASan already guards the stack, keeps reports cleaner.
:: /Z7 puts type info in OBJ (fine for single-DLL case), keep /Zi for PDB too.

echo [INFO] Compiling intervaldb.c (ASan)...
cl %CFLAGS% ^
   /DBUILD_C_LIBRARY ^
   "%INTERVALDB_INCLUDE%\intervaldb.c" ^
   /I"%INTERVALDB_INCLUDE%" ^
   /Fo:intervaldb.obj
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] intervaldb.c failed & exit /b )

echo [INFO] Compiling temporal_contact_matrix.c (ASan)...
cl %CFLAGS% ^
   /DBUILD_C_LIBRARY ^
   temporal_contact_matrix.c ^
   /I"%INTERVALDB_INCLUDE%" ^
   /Fo:temporal_contact_matrix.obj
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] temporal_contact_matrix.c failed & exit /b )

echo [INFO] Linking temporal_contact_matrix.dll (ASan)...
link /DLL /DEBUG /INCREMENTAL:NO /NOLOGO ^
     /OUT:temporal_contact_matrix.dll ^
     temporal_contact_matrix.obj intervaldb.obj ^
     /fsanitize=address ^
     kernel32.lib user32.lib gdi32.lib shell32.lib ole32.lib oleaut32.lib uuid.lib comdlg32.lib advapi32.lib
if %ERRORLEVEL% NEQ 0 ( echo [ERROR] Linking failed! & exit /b )

echo [SUCCESS] temporal_contact_matrix.dll built with AddressSanitizer.

:: Optional: stricter ASan behavior/logging on Windows
set ASAN_OPTIONS=windows_hook_veh=1:handle_segv=1:halt_on_error=1:print_stats=1
:: Log to file next to the process (asan.*.log)
:: set ASAN_OPTIONS=%ASAN_OPTIONS%:log_path=asan
pause
