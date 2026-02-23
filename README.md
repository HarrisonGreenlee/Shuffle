# Shuffle

Shuffle is a Python-based framework for generating **synthetic temporal hospital contact networks** by applying interpretable, rule-based permutations to patient arrival times and replaying observed room-transfer trajectories.

Built for research workflows in epidemiology and healthcare network analysis, Shuffle lets users generate multiple plausible temporal contact networks from empirical hospital movement data, then evaluate how different shuffling constraints affect network structure over time.

## Background

Hospital-acquired infection risk depends heavily on **who shares space with whom, and when**. Temporal contact networks are a natural way to represent this, but high-quality hospital contact data is difficult to collect and often not broadly generalizable across institutions.

Traditional network generation approaches can be difficult to interpret, computationally expensive, or poorly suited to preserving realistic temporal and operational constraints in hospital settings.

Shuffle addresses this by using a practical, transparent alternative: **shuffle patient arrival times within user-defined rule pools**, then replay each patient’s observed care trajectory at the reassigned start time.

## What Shuffle Does

Shuffle generates synthetic temporal contact networks by:

- Grouping patients into rule-defined shuffle pools (e.g., same year, month, weekday, or time-of-day)
- Permuting arrival times **within** those pools
- Replaying each patient’s original room-transfer sequence at the new start time
- Recomputing patient co-location contacts and downstream network statistics over time windows

This preserves observed pathway structure while introducing interpretable, controllable variation in timing and contact opportunities. Rules are modular and can be combined to progressively preserve more temporal structure.

## Available Shuffle Rules

Shuffle includes built-in rule types for constraining permutations, including:

- **Unconstrained Shuffle**
- **Shuffle by Year**
- **Shuffle by Month**
- **Shuffle by Day of the Week**
- **Shuffle by Time of Day**

Rules can be combined (for example: Year + Month + Weekday + Time of Day) to produce increasingly restrictive and more operationally realistic synthetic networks.

## Mixing Capacity (Entropy-Based Index)

Shuffle includes a **mixing capacity** metric to quantify how restrictive a selected rule set is relative to an unconstrained shuffle.

In practice, Shuffle can compute:

- a **per-window mixing index** time series, and
- a **global aggregate mixing summary** over the selected analysis period

This gives users a concrete way to measure how much permutation freedom remains under a chosen set of constraints, rather than relying only on visual inspection of generated networks.

## Network Statistics and Evaluation

Shuffle is designed to work with a companion analysis workflow that computes time-windowed graph statistics such as:

- degree (mean / max)
- strength (mean / max / min)
- connected components
- largest component size
- weighted / unweighted density
- eigenvector centrality (mean / max)

These metrics can be compared between the empirical baseline and generated synthetic networks to assess how rule choices affect global and patient-level contact structure.

## Use Cases

- Hospital-acquired infection research
- Temporal contact network analysis
- Sensitivity analysis for epidemic simulations
- Generating interpretable synthetic baselines under operational constraints
- Studying how seasonality / weekday effects / time-of-day patterns shape contact structure

---

## Usage

This section outlines the basic steps needed to begin using the tool.

### Setup Instructions

Shuffle includes GUI tools (Gooey-based) for generation and statistics workflows:

- `shuffle_gui.py` — generate synthetic shuffled patient sequence files and optionally calculate mixing capacity
- `stats_gui.py` — compare a baseline file against generated files and build a visualization/dashboard workflow

### Basic Workflow

1. **Prepare a baseline patient movement sequence file** (`.txt`)
2. **Generate synthetic shuffled sequence files** using `shuffle_gui.py`
3. *(Optional)* **Compute mixing capacity** during generation
4. **Run `stats_gui.py`** to compute time-windowed network metrics and generate comparison outputs/dashboard

### Data Format
ALL statistics are measured by generating static slices of a a temporal contact graph constructed from the provided data.
Each line corresponds to the mobility data of an individual patient, and each ROOM_ID DATETIME pair corresponds to the path that they take.
Records **MUST** be provided in the following format:
```
ROOM_ID, DATETIME, ROOM_ID, DATETIME, ROOM_ID, DATETIME, ...
ROOM_ID, DATETIME, ROOM_ID, DATETIME, ...
ROOM_ID, DATETIME, ROOM_ID, DATETIME, ROOM_ID, DATETIME, ROOM_ID, DATETIME, ROOM_ID, DATETIME, ...
...
```
Datetimes should be provided in `"%Y-%m-%d %H:%M:%S"` format.

For example, the following notation describes the paths taken by five (fictional) individuals at a (fictional) medical facility.

Note the usage of a special EXIT token. The name of this token can be provied to the network statistics tool to avoid generating contacts between people in this location. 

Patients may exit and re-enter the hospital multiple times. If configured, patient sequences can be automatically split after a certain duration outside of the hospital.
```
ENT-LOBBY, 2026-02-20 10:14:05, OR-PREOP-04, 2026-02-20 10:39:20, OR-02, 2026-02-20 11:05:00, PACU-03, 2026-02-20 13:11:25, EXIT, 2026-02-20 15:02:10
WARD-1C-22, 2026-02-20 06:45:00, PT-GYM-01, 2026-02-20 07:30:00, EXIT, 2026-02-20 08:25:30
ER-03, 2026-02-21 00:12:40, CT-01, 2026-02-21 00:41:05, EXIT, 2026-02-21 01:18:50
ENT-LOBBY, 2026-02-21 13:05:12, CLINIC-B-07, 2026-02-21 13:22:40, EXIT, 2026-02-21 13:48:05, CLINIC-B-07, 2026-02-21 14:16:30, EXIT, 2026-02-21 15:02:55
WARD-4D-11, 2026-02-21 08:00:00, XRAY-02, 2026-02-21 08:27:35, WARD-4D-11, 2026-02-21 09:10:20, CAF-01, 2026-02-21 12:03:00, EXIT, 2026-02-21 12:44:10
```
In an actual graph file, there might be thousands of nodes and millions of recorded contacts. The main bottleneck is the number of simultaneous interactions - as this increases, VRAM requirements also increase. 

### Running the Shuffle GUI

```bat
py shuffle_gui.py
```

The GUI supports:

- selecting the baseline input file
- selecting an output directory
- choosing how many synthetic files to generate
- selecting shuffle constraints (dynamic checkboxes loaded from `shuffle.py`)
- optional mixing-capacity calculation using selected constraints
- manifest generation (`shuffle_manifest.json`) in the output folder

### Running the Stats GUI

```bat
py stats_gui.py
```

The stats GUI supports:

- selecting a baseline patient file
- selecting a folder of generated `.txt` files for comparison
- selecting an output folder
- configuring UTC sweep windowing (start/end, size, stride)
- generating CSV statistics and dashboard outputs

## Extending Shuffle with Custom Rules

Shuffle supports user-defined rules through a plugin mechanism. A rule is a Python function that maps a `datetime` to a categorical key used for pooling arrivals before permutation.

The included `user_plugins.py` provides an example rule:

**Shuffle by Hour** (only shuffle among arrivals in the same hour-of-day)

Example plugin rule:

```python
from rules_registry import register_rule

def same_hour(dt):
    return dt.hour

register_rule(
    "Shuffle by Hour",
    same_hour,
    "Only shuffle among arrivals that occurred in the same hour-of-day (0..23)."
)
```

`shuffle_gui.py` can auto-load `user_plugins.py` (when present next to the GUI script/exe), and dynamically exposes registered rules as GUI checkboxes.

> After editing `user_plugins.py`, close and reopen the GUI to refresh the available shuffle methods.

---

## Project Components (High-Level)

- **`shuffle_gui.py`**  
  GUI for synthetic sequence generation and optional mixing-capacity outputs/manifest.

- **`stats_gui.py`**  
  GUI for running conversion/stats/dashboard workflows against baseline vs generated files.

- **`user_plugins.py`**  
  Optional external shuffle rule definitions.

- **`shuffle.py`**  
  Core shuffling logic, built-in rules, plugin loading, multiprocessing generation, and mixing-capacity computation.

- **`network_stats.py`**  
  Time-window graph metric computation (connected components, density, strength, eigenvector centrality, etc.) using native acceleration via `temporal_contact_matrix.dll`.

- **`build_contact_graph.c`** 
C source for `build_contact_graph.exe; builds a temporal contact edge list from visit/room interval records by finding overlapping co-presence intervals.
- **`temporal_contact_matrix.c`**  
C source for `temporal_contact_matrix.dll`; parses temporal contact edges and serves fast time-window adjacency queries.

- **`slice_temporal.py`**  
Python wrapper for `temporal_contact_matrix.dll` to slice temporal contact data into time windows and compute graph stats.

---


# Build Walkthrough

This section walks you through building the native components used by the Shuffle workflow on **Windows** using **Visual Studio 2022**, **CMake**, and **Ninja**.

### ✅ Prerequisites

Make sure the following are installed and available in your system `PATH`:

- [x] **Visual Studio 2022** (with C/C++ build tools installed)
- [x] **CMake** (3.18+ recommended)
- [x] **Ninja**
- [x] **Python 3.x**

> Use the **x64 Native Tools Command Prompt for VS 2022** so `cl.exe` and the MSVC toolchain are available.

### Step-by-Step Instructions

Shuffle relies on two native C components used by the analysis pipeline:

1. `build_contact_graph.exe`
2. `temporal_contact_matrix.dll`

---

## build_contact_graph

This portion of the project (`build_contact_graph`) is written in C to improve performance.

### Prerequisites

- **Visual Studio 2022** (with C/C++ build tools installed)
- **CMake** (3.18+ recommended)
- **Ninja** (available on PATH)

> Use the **x64 Native Tools Command Prompt for VS 2022** so `cl.exe` and the MSVC toolchain are available.


### Build steps (CMake + Ninja + MSVC)

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

---

## temporal_contact_matrix

This portion of the project (`temporal_contact_matrix.dll`) is also written in C for efficiency.

### Prerequisites

- **Visual Studio 2022** (with C/C++ build tools installed)
- **CMake** (3.18+ recommended)
- **Ninja** (available on PATH)

> Use the **x64 Native Tools Command Prompt for VS 2022** so `cl.exe` and the MSVC toolchain are available.

### Build steps (CMake + Ninja + MSVC)

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


### Release build (optional)

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

---

## For Maintainers

### Force a Clean Rebuild

Use the same pattern inside either `build_contact_graph\` or `temporal_contact_matrix\`:

```bat
rd /s /q build
mkdir build
cd build
cmake .. -G "Ninja" -DCMAKE_BUILD_TYPE=Debug
cmake --build .
```

(Then copy the built artifact back to the component root as shown above.)

---

# Acknowledgements

This project builds on several excellent open-source libraries and tools. I am grateful to the authors and maintainers of the following projects:

- **ncls** — https://github.com/pyranges/ncls  
  This project includes a modified version of code derived from `ncls`. The original authors did the foundational work; any bugs or issues introduced by my tweaks to their code are my responsibility.

- **uthash** — https://troydhanson.github.io/uthash/  
  Used for efficient hash table utilities in the C components.

- **eigen** — https://libeigen.gitlab.io/  
  Used for linear algebra operations.

I do not claim credit for code from these projects; all credit belongs to their respective authors and maintainers.