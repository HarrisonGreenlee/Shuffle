"""
shuffle_gui.py

Gooey-based GUI for `shuffle.py` that generates synthetic patient sequence files and,
optionally, computes a mixing-capacity analysis.

Features
--------
- Loads shuffle constraints dynamically from `shuffle.list_hash_names()` as checkboxes.
- Generates synthetic outputs via `shuffle.generate_shuffles(...)`.
- Optionally computes mixing-index series + summary using `shuffle.py` helpers.
- Writes a single run manifest to the output directory:
    `shuffle_manifest.json` (overwritten each run)
- If mixing is enabled, also writes:
    - `mixing_index_series.csv`
    - `mixing_index_series.png`

GUI tabs
--------
- **Shuffle**: input/output, number of files, exit token/gap, constraint selection
- **Mixing Capacity**: optional UTC window/stride/duration settings
- **Advanced Config**: seed and worker count

Notes
-----
- Mixing inputs are validated only when "Calculate Mixing Capacity" is enabled.
- Mixing uses the currently selected shuffle constraints from the Shuffle tab.
- `user_plugins.py` is auto-loaded if present next to the script/executable.
- CSV/plot output requires optional dependencies (`pandas`, `matplotlib`).
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import hashlib
import platform
import threading
import subprocess
from argparse import SUPPRESS
from datetime import datetime, timezone
from pathlib import Path

from gooey import Gooey, GooeyParser

# Optional deps for mixing output
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # noqa: N816

try:
    import matplotlib
    import matplotlib.dates as mdates

    matplotlib.use("Agg")  # safe for GUI apps; we only save PNGs
    import matplotlib.pyplot as plt  # type: ignore
except Exception:
    plt = None  # noqa: N816


# ---------------------------
# Fix blurry text on high-DPI displays (Windows only)
# ---------------------------
if sys.platform == "win32":
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ---------------------------
# Import shuffle.py
# ---------------------------
try:
    import shuffle as shuff
except Exception as e:
    print("[ERROR] Could not import shuffle.py as module 'shuffle'.")
    print("        Put shuffle.py in the same folder as this GUI or add it to PYTHONPATH.")
    print(f"        Import error: {e}")
    raise

# Auto-load user plugins if present next to this GUI script/exe
base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # supports PyInstaller
plugin_file = base_dir / "user_plugins.py"
if plugin_file.exists():
    shuff.ensure_plugins_loaded([str(plugin_file)])

_slug_re = re.compile(r"[^a-z0-9]+")


def slug(s: str) -> str:
    return _slug_re.sub("_", s.lower()).strip("_")


# ---------------------------
# Manifest helpers
# ---------------------------
SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_text(path: Path, text: str):
    """
    Atomically write text to 'path' by writing a temp file then replacing.
    Works on Windows + POSIX. Overwrites if file exists.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------
# Mixing capacity parse helpers
# ---------------------------
def _parse_optional_int(field_name: str, raw: str) -> int | None:
    """
    Returns None if blank, else parses int or raises ValueError.
    """
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{field_name} must be an integer (got: {raw!r}).")


def _parse_optional_epoch_utc(date_str: str, time_str: str, *, label: str) -> int | None:
    """
    Returns None if BOTH date+time are blank.
    If either is provided, requires both, accepts HH:MM or HH:MM:SS, and returns epoch seconds (UTC).
    """
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()

    if date_str == "" and time_str == "":
        return None
    if date_str == "" or time_str == "":
        raise ValueError(f"Both {label} date and {label} time must be provided.")

    # accept HH:MM or HH:MM:SS (TimeChooser often yields HH:MM)
    try:
        datetime.strptime(time_str, "%H:%M:%S")
    except ValueError:
        try:
            datetime.strptime(time_str, "%H:%M")
            time_str = f"{time_str}:00"
        except ValueError:
            raise ValueError(f"{label} time must be HH:MM or HH:MM:SS (got: {time_str!r}).")

    dt = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def build_command_string(args, selected_hash_names: list[str]) -> str:
    """
    Reconstruct a readable CLI-like command for provenance.
    This is best-effort; Gooey itself invokes the script with args as usual.
    """
    parts: list[str] = [sys.executable, Path(__file__).name]
    parts += ["--input", str(args.input)]
    parts += ["--output-dir", str(args.output_dir)]
    parts += ["--num", str(args.num)]
    if args.seed is not None:
        parts += ["--seed", str(args.seed)]
    if args.workers is not None:
        parts += ["--workers", str(args.workers)]
    for name in selected_hash_names:
        parts += [f"--{slug(name).replace('_', '-')}"]

    if getattr(args, "exit_token", None):
        parts += ["--exit-token", str(args.exit_token)]
    if getattr(args, "exit_gap_seconds", None) is not None:
        parts += ["--exit-gap-seconds", str(args.exit_gap_seconds)]


    # mixing flags
    if getattr(args, "calc_mixing", False):
        parts += ["--calc-mixing"]
        if getattr(args, "mix_start_date", ""):
            parts += ["--mix-start-date", str(args.mix_start_date)]
        if getattr(args, "mix_start_time", ""):
            parts += ["--mix-start-time", str(args.mix_start_time)]
        if getattr(args, "mix_end_date", ""):
            parts += ["--mix-end-date", str(args.mix_end_date)]
        if getattr(args, "mix_end_time", ""):
            parts += ["--mix-end-time", str(args.mix_end_time)]

        # New terminology: stride + duration
        if getattr(args, "mix_stride", ""):
            parts += ["--mix-stride", str(args.mix_stride)]
        if getattr(args, "mix_duration", ""):
            parts += ["--mix-duration", str(args.mix_duration)]

    return " ".join(parts)


def collect_generated_files(out_dir: Path, num_expected: int) -> list[dict]:
    """
    Assumes generator writes 0.txt..N-1.txt (your current shuffle.py does).
    Records 'missing' if any expected file isn't present.
    """
    files: list[dict] = []
    for i in range(int(num_expected)):
        p = (out_dir / f"{i}.txt").resolve()
        entry = {
            "index": i,
            "path": str(p),
            "name": p.name,
            "size_bytes": None,
            "sha256": None,
            "status": None,
            "error": None,
        }
        try:
            if not p.exists():
                entry["status"] = "missing"
            else:
                entry["size_bytes"] = p.stat().st_size
                entry["sha256"] = sha256_file(str(p))
                entry["status"] = "complete"
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
        files.append(entry)
    return files


def _open_file_nonblocking(path: Path):
    """
    Best-effort open of a file using platform default app, without blocking.
    """
    try:
        p = str(path)
        if sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[PLOT][WARN] Could not open plot automatically: {e}")


def open_file_async(path: Path):
    """
    Fire-and-forget opener to avoid blocking generation.
    """
    threading.Thread(target=_open_file_nonblocking, args=(path,), daemon=True).start()


def write_mixing_csv(rows: list[dict], csv_path: Path) -> None:
    if pd is None:
        raise RuntimeError("pandas is required to write mixing CSV. Install with: pip install pandas")
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)


def write_mixing_plot(rows: list[dict], png_path: Path) -> None:
    if plt is None:
        raise RuntimeError("matplotlib is required to plot mixing series. Install with: pip install matplotlib")
    if not rows:
        raise RuntimeError("No mixing rows to plot (empty result).")

    xs = [datetime.fromtimestamp(int(r["t_start"]), tz=timezone.utc) for r in rows]
    ys = [float(r.get("I", 0.0)) for r in rows]

    plt.figure()

    plt.rcParams.update({
        "font.size": 10,
        "axes.labelsize": 15,
        "axes.titlesize": 15,
        "legend.fontsize": 10,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
    })


    fig, ax = plt.subplots()
    ax.plot(xs, ys, marker="o", markersize=3, linewidth=1)

    plt.ylim(0.0, 1.0)
    locator = mdates.AutoDateLocator(minticks=4, maxticks=20)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))

    fig.autofmt_xdate(rotation=30, ha="right") 
    plt.xlabel("Time")
    plt.ylabel("Mixing Index")
    plt.title("Mixing Index (per static window)")
    plt.tight_layout()
    plt.savefig(png_path)
    plt.close()


@Gooey(
    program_name="Shuffle",
    default_size=(1100, 1500),
    advanced=True,
    tabbed_groups=True,  # makes each argparse argument group a tab
    show_success_modal=False,
    show_stop_warning=True,
    clear_before_run=True,
    terminal_font_color="black",
    terminal_background_color="white",
    requires_shell=False,
)
def main():
    parser = GooeyParser(description="Generate synthetic patient sequence files using shuffle constraints.")

    # ===========================
    # TAB 1: Shuffle
    # ===========================
    shuffle_tab = parser.add_argument_group(
        "Shuffle",
        "Generate synthetic patient sequence files.\n\n"
        "Plugins placed in 'user_plugins.py' will be loaded at startup.\n"
        "Close and reopen the app after editing 'user_plugins.py' to refresh available shuffle methods.",
        gooey_options={"columns": 2, "show_border": True},
    )

    shuffle_tab.add_argument(
        "--input",
        widget="FileChooser",
        help="Baseline patient sequence file (.txt).",
        gooey_options={"full_width": True},
        required=True,
    )
    shuffle_tab.add_argument(
        "--output-dir",
        widget="DirChooser",
        help="Directory to write generated synthetic patient sequence files.",
        gooey_options={"full_width": True},
        required=True,
    )

    shuffle_tab.add_argument("--num", type=int, default=10, help="Number of synthetic files to generate.", gooey_options={"full_width": True})

    shuffle_tab.add_argument(
        "--exit-token",
        type=str,
        default="EXIT",
        help="Room token that marks an exit event.",
        gooey_options={},
    )

    shuffle_tab.add_argument(
        "--exit-gap-seconds",
        default="",
        help="Split sequences when time between exit token and next token exceeds duration.",
        gooey_options={},
    )


    # dynamic constraint checkboxes from shuffle.py
    hash_dest_by_name: dict[str, str] = {}
    for name in shuff.list_hash_names():
        dest = f"hash_{slug(name)}"
        hash_dest_by_name[name] = dest
        shuffle_tab.add_argument(
            f"--{slug(name).replace('_', '-')}",
            action="store_true",
            dest=dest,
            help=name,
        )

    # Optional: force the tab to render cleanly even if descriptions are long
    shuffle_tab.add_argument(
        "--_shuffle_anchor",
        default="",
        help=SUPPRESS,
        gooey_options={"visible": False},
    )

    # ===========================
    # TAB 2: Mixing Capacity
    # ===========================
    mix_tab = parser.add_argument_group(
        "Mixing Capacity",
        "Calculate mixing capacity using the selected shuffle constraints from the 'Shuffle' tab.",
        gooey_options={"columns": 2, "show_border": True},
    )

    mix_tab.add_argument(
        "--calc-mixing",
        widget="BlockCheckbox",
        gooey_options={"full_width": True, "checkbox_label": "Calculate Mixing Capacity"},
        action="store_true",
        help="If checked, all Mixing Capacity fields must be filled before running.",
    )

    mix_tab.add_argument("--mix-start-date", widget="DateChooser", default="", help="Windowing start date (UTC).")
    mix_tab.add_argument("--mix-start-time", widget="TimeChooser", default="00:00:00", help="Windowing start time (HH:MM:SS).")
    mix_tab.add_argument("--mix-end-date", widget="DateChooser", default="", help="Windowing end date (UTC).")
    mix_tab.add_argument("--mix-end-time", widget="TimeChooser", default="00:00:00", help="Windowing end time (HH:MM:SS).")

    # Keep these as strings so they can start empty
    mix_tab.add_argument("--mix-step", "--mix-stride", dest="mix_stride", default="", help="Window stride (seconds).")
    mix_tab.add_argument("--mix-window", "--mix-duration", dest="mix_duration", default="", help="Window duration (seconds).")

    # ===========================
    # TAB 3: Advanced Config
    # ===========================
    advanced_tab = parser.add_argument_group("Advanced Config", gooey_options={"columns": 2, "show_border": True})
    advanced_tab.add_argument("--seed", type=int, default=None, help="Random seed (optional).")
    advanced_tab.add_argument("--workers", type=int, default=None, help="Worker processes (default: CPU count).")

    args = parser.parse_args()

    # Resolve selected hash rule names from checkbox flags (from Shuffle tab)
    selected_hash_names = [n for n, d in hash_dest_by_name.items() if getattr(args, d)]

    # ---------------------------
    # Mixing gating / validation
    # ---------------------------

    raw_gap = (getattr(args, "exit_gap_seconds", "") or "").strip()
    if raw_gap == "":
        args.exit_gap_seconds = None
    else:
        try:
            args.exit_gap_seconds = float(raw_gap)
        except ValueError:
            print(f"[ERROR] exit-gap-seconds must be a number (got {raw_gap!r}). Leave blank for no splitting.")
            return
        
    if args.exit_gap_seconds is not None and args.exit_gap_seconds <= 0:
        print("[ERROR] exit-gap-seconds must be > 0, or leave blank for no splitting.")
        return



    mix_t_start: int | None = None
    mix_t_end: int | None = None
    stride: int | None = None
    win_dur: int | None = None
    num_steps: int | None = None

    if args.calc_mixing:
        try:
            mix_t_start = _parse_optional_epoch_utc(args.mix_start_date, args.mix_start_time, label="mix start")
            mix_t_end = _parse_optional_epoch_utc(args.mix_end_date, args.mix_end_time, label="mix end")

            # NOTE: argparse dest names are mix_stride / mix_duration (not step_size/window_size)
            stride = _parse_optional_int("Window stride", args.mix_stride)
            win_dur = _parse_optional_int("Window duration", args.mix_duration)

            missing = []
            if mix_t_start is None:
                missing.append("mix start date/time")
            if mix_t_end is None:
                missing.append("mix end date/time")
            if stride is None:
                missing.append("window stride")
            if win_dur is None:
                missing.append("window duration")


            if missing:
                print(
                    "[ERROR] 'Calculate Mixing Capacity' is checked, but the following fields are missing: "
                    + ", ".join(missing)
                )
                return
            
            if stride <= 0:
                print("[ERROR] Window stride must be positive.")
                return
            if win_dur <= 0:
                print("[ERROR] Window duration must be positive.")
                return
            if mix_t_end <= mix_t_start:
                print("[ERROR] Mix end datetime must be after mix start datetime.")
                return

            duration = mix_t_end - mix_t_start
            if duration < win_dur:
                print("[ERROR] Duration is too short for the given window duration (no full windows fit).")
                return

            num_steps = ((duration - win_dur) // stride) + 1
            if num_steps <= 0:
                print("[ERROR] No full windows fit for the given stride/duration.")
                return

        except ValueError as e:
            print(f"[ERROR] {e}")
            return

    print("\n[SHUFFLE] Generating synthetic patient files...")
    print(f"[SHUFFLE] baseline={args.input}")
    print(f"[SHUFFLE] out_dir={args.output_dir}")
    print(f"[SHUFFLE] num={args.num} seed={args.seed} workers={args.workers}")
    print(f"[SHUFFLE] hashes={selected_hash_names if selected_hash_names else '(none -> Full Shuffle)'}")

    if args.calc_mixing:
        print("[MIXING] Mixing Capacity enabled.")
        print(f"[MIXING] start_epoch_utc={mix_t_start} end_epoch_utc={mix_t_end}")
        print(f"[MIXING] stride={stride} duration={win_dur} steps={num_steps} (computed; full windows only)")
        print(f"[MIXING] uses_hashes={selected_hash_names if selected_hash_names else '(none -> Full Shuffle)'}")

    # ---------------------------
    # Manifest (single batch file, overwritten each run)
    # ---------------------------
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "shuffle_manifest.json"

    started_utc = utc_now_iso()
    t0 = time.time()

    source_path = Path(args.input).resolve()
    source_hash = None
    try:
        source_hash = sha256_file(str(source_path))
    except Exception as e:
        print(f"[MANIFEST][WARN] Could not hash source file: {e}")

    shuffle_mod_path = Path(getattr(shuff, "__file__", "")).resolve() if getattr(shuff, "__file__", None) else None
    shuffle_mod_hash = None
    if shuffle_mod_path:
        try:
            shuffle_mod_hash = sha256_file(str(shuffle_mod_path))
        except Exception as e:
            print(f"[MANIFEST][WARN] Could not hash shuffle module: {e}")

    cmd_str = build_command_string(args, selected_hash_names)

    # Will be populated if calc-mixing is enabled
    mixing_rows: list[dict] | None = None
    mixing_summary: dict | None = None
    mixing_csv_path: Path | None = None
    mixing_plot_path: Path | None = None

    manifest: dict = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": started_utc,
        "completed_utc": None,
        "duration_seconds": None,
        "status": "started",
        "error": None,
        "source": {
            "path": str(source_path),
            "name": source_path.name,
            "sha256": source_hash,
        },
        "output_dir": str(out_dir),
        "shuffle": {
            "selected_hash_names": list(selected_hash_names),
            "is_full_shuffle": (len(selected_hash_names) == 0),
            "seed": args.seed,
            "workers": args.workers,
            "num_shuffles": int(args.num),
            "exit_token": getattr(args, "exit_token", "EXIT"),
            "exit_gap_seconds": getattr(args, "exit_gap_seconds", None),
            "command": cmd_str,
            "shuffle_module_file": str(shuffle_mod_path) if shuffle_mod_path else None,
            "shuffle_module_sha256": shuffle_mod_hash,
            "python_version": sys.version.replace("\n", " "),
            "platform": platform.platform(),
            "gui_script": str(Path(__file__).resolve()),
            "user_plugins_file": str(plugin_file.resolve()) if plugin_file.exists() else None,
        },
        "mixing_capacity": {
            "enabled": bool(args.calc_mixing),
            "start_epoch_utc": mix_t_start,
            "end_epoch_utc": mix_t_end,
            "window_stride_seconds": stride,
            "window_duration_seconds": win_dur,
            "num_steps": num_steps,  # computed
            "uses_selected_hash_names": list(selected_hash_names),
            # NEW (filled later when enabled):
            "summary": None,
            "artifacts": {
                "csv": None,
                "plot_png": None,
            },
        },
        "files": [],
    }

    # Write "started" manifest immediately (overwrite any prior manifest in this directory)
    try:
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
        print(f"[MANIFEST] Wrote: {manifest_path}")
    except Exception as e:
        print(f"[MANIFEST][WARN] Could not write started manifest: {e}")

    # ---------------------------
    # If mixing is enabled, compute + save artifacts NOW (independent of shuffle generation),
    # and open plot asynchronously so it doesn't block file generation.
    # ---------------------------
    if args.calc_mixing:
        try:
            # Compute series + summary using shuffle.py functions
            mixing_rows = shuff.compute_mixing_index_series(
                input_path=str(source_path),
                selected_hash_names=selected_hash_names,
                t_start=int(mix_t_start),
                step=int(stride),          # stride between window starts
                num_steps=int(num_steps),
                window_size=int(win_dur),  # window duration
                exit_gap_seconds=args.exit_gap_seconds,
                exit_token=args.exit_token,

            )
            mixing_summary = shuff.summarize_mixing_rows(mixing_rows)

            # Save CSV
            mixing_csv_path = out_dir / "mixing_index_series.csv"
            write_mixing_csv(mixing_rows, mixing_csv_path)

            # Save plot PNG
            mixing_plot_path = out_dir / "mixing_index_series.png"
            write_mixing_plot(mixing_rows, mixing_plot_path)

            # Open plot asynchronously (best effort)
            open_file_async(mixing_plot_path)

            # Update manifest with summary + artifact metadata
            manifest["mixing_capacity"]["summary"] = mixing_summary
            manifest["mixing_capacity"]["artifacts"]["csv"] = {
                "path": str(mixing_csv_path.resolve()),
                "name": mixing_csv_path.name,
                "sha256": sha256_file(str(mixing_csv_path)),
            }
            manifest["mixing_capacity"]["artifacts"]["plot_png"] = {
                "path": str(mixing_plot_path.resolve()),
                "name": mixing_plot_path.name,
                "sha256": sha256_file(str(mixing_plot_path)),
            }

            # Persist intermediate manifest update (still "started")
            try:
                atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
                print(f"[MANIFEST] Updated (mixing artifacts): {manifest_path}")
            except Exception as e:
                print(f"[MANIFEST][WARN] Could not update manifest with mixing artifacts: {e}")

            print(f"[MIXING] Wrote CSV:  {mixing_csv_path}")
            print(f"[MIXING] Wrote plot: {mixing_plot_path}")
            print("[MIXING] Plot open requested (non-blocking).")

        except Exception as e:
            # Treat mixing failure as run failure when enabled
            manifest["status"] = "failed"
            manifest["error"] = f"Mixing computation failed: {e}"
            try:
                atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
                print(f"[MANIFEST] Updated (mixing failed): {manifest_path}")
            except Exception as e2:
                print(f"[MANIFEST][WARN] Could not write failed manifest after mixing error: {e2}")
            print(f"[MIXING][ERROR] {e}")
            return

    # ---------------------------
    # Run generation
    # ---------------------------
    try:
        shuff.generate_shuffles(
            input_path=args.input,
            output_dir=args.output_dir,
            num_shuffles=args.num,
            selected_hash_names=selected_hash_names,
            seed=args.seed,
            workers=args.workers,
            exit_gap_seconds=args.exit_gap_seconds,
            exit_token=args.exit_token,
        )

        manifest["files"] = collect_generated_files(out_dir, int(args.num))
        manifest["status"] = "complete"

    except Exception as e:
        manifest["status"] = "failed"
        manifest["error"] = str(e)
        # Still record whatever was produced (if any)
        try:
            manifest["files"] = collect_generated_files(out_dir, int(args.num))
        except Exception as e2:
            manifest["files"] = []
            manifest["error"] = f"{manifest['error']} | (also failed collecting outputs: {e2})"
        raise

    finally:
        manifest["completed_utc"] = utc_now_iso()
        manifest["duration_seconds"] = round(time.time() - t0, 3)
        try:
            atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
            print(f"[MANIFEST] Updated: {manifest_path}")
        except Exception as e:
            print(f"[MANIFEST][WARN] Could not finalize manifest: {e}")

    # Print mixing summary at end of generation process
    if args.calc_mixing and mixing_summary is not None:
        print("\n[MIXING][SUMMARY]")
        print(json.dumps(mixing_summary, indent=2, sort_keys=True))

    print("[SHUFFLE] Done.")


if __name__ == "__main__":
    main()
