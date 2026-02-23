# Build Guide: temporal_contact_matrix

This portion of the project (`temporal_contact_matrix.dll`) is also written in C to improve performance

## Prerequisites

- **Visual Studio 2022** (with C/C++ build tools installed)
- **CMake** (3.18+ recommended)
- **Ninja** (available on PATH)

> Use the **x64 Native Tools Command Prompt for VS 2022** so `cl.exe` and the MSVC toolchain are available.

---

## Build steps (CMake + Ninja + MSVC)

1. Open **x64 Native Tools Command Prompt for VS 2022**

2. From your larger project root, change into the component folder:

```bat
cd temporal_contact_matrix
```

3. Create and enter a build directory:

```bat
mkdir build
cd build
```

4. Configure with CMake using Ninja (Debug build):

```bat
cmake .. -G "Ninja" -DCMAKE_BUILD_TYPE=Debug
```

5. Build the DLL:

```bat
cmake --build .
```

6. Copy the DLL to the base directory:

```bat
copy /Y temporal_contact_matrix.dll ..\temporal_contact_matrix.dll
```

---

## Release build (optional)

If you want an optimized Release build (and to disable ASan):

```bat
cd slicer_component
mkdir build-release
cd build-release
cmake .. -G "Ninja" -DCMAKE_BUILD_TYPE=Release -DENABLE_ASAN=OFF
cmake --build .
copy /Y temporal_contact_matrix.dll ..\temporal_contact_matrix.dll
copy /Y temporal_contact_matrix.lib ..\temporal_contact_matrix.lib
```
