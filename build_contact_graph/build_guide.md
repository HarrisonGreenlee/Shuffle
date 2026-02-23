# Build Guide: build_contact_graph

This portion of the project (`build_contact_graph`) is written in C to improve performance.

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
cd build_contact_graph
```

3. Create and enter a build directory:

```bat
mkdir build
cd build
```

4. Configure with CMake using Ninja:

```bat
cmake .. -G "Ninja" -DCMAKE_BUILD_TYPE=Debug
```

5. Build the executable:

```bat
cmake --build .
```

6. Copy the executable to the base directory:
```bat
copy /Y build_contact_graph.exe ..\build_contact_graph.exe
``` 