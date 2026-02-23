"""
stats_gui.py

Gooey-based GUI for computing temporal network statistics for a baseline patient file
and a folder of comparison patient files, then building a visualization dashboard.

Features
--------
- Converts each patient `.txt` file to graph format using a hardcoded converter executable.
- Runs `network_stats.py` temporal sweep stats (always with `directed=False`).
- Appends all results into one run CSV and generates a dashboard via `visualize.py`.
- Always resets run outputs (stats CSV + dashboard folder) before a run.
- Always reconverts inputs (no resume) and deletes converted temp files immediately.
- Always runs visualization after stats and attempts to open the dashboard automatically.

Inputs
------
- Baseline patient sequence file (`.txt`)
- Folder of comparison patient sequence files (`*.txt`)
- Output folder
- UTC windowing parameters: start/end datetime, stride, and window duration
- Exit token and optional exit-gap splitting (blank disables gap handling)

Outputs (in output folder)
--------------------------
- `<run_name>_all_stats.csv`
- `<run_name>_dashboard/dashboard.html` (plus assets)
- optional existing `mixing_index_series.csv` may be detected and offered to visualization

Notes
-----
- `run_name` is sanitized; if blank, a timestamped name is generated automatically.
- Number of sweep steps is computed from start/end/window/stride:
  `((t_end - t_start - window) // stride) + 1` (when the duration can fit a full window).
- Assumes local tools/scripts exist at the hardcoded paths in this file.
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import subprocess
import importlib.util
import re
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
from gooey import Gooey, GooeyParser

try:
    from datetime import UTC  # Py 3.11+
except ImportError:  # Py < 3.11
    from datetime import timezone as _tz

    UTC = _tz.utc

# ---------------------------
# Hardcoded tool/script paths
# ---------------------------
CONVERTER_EXE = "./build_contact_graph.exe"
NETWORK_STATS_PY = "./network_stats.py"
VISUALIZE_PY = "./visualize.py"

# ---------------------------
# Fix blurry text on high-DPI displays (Windows only)
# ---------------------------
if sys.platform == "win32":
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

_slug_re = re.compile(r"[^a-z0-9]+")


def sanitize_run_name(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("/", "-").replace("\\", "-")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._+-]+", "_", s)
    return s.strip("_")


def default_run_name(prefix: str = "stats") -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    prefix = sanitize_run_name(prefix) or "stats"
    return f"{prefix}_{ts}"


def load_module_from_path(module_name: str, path: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def append_df_to_csv(df: pd.DataFrame, csv_path: Path):
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        df.to_csv(f, header=is_new, index=False)
        f.flush()
        os.fsync(f.fileno())


def safe_unlink(p: Path, retries: int = 3, delay: float = 0.5) -> bool:
    for attempt in range(1, retries + 1):
        try:
            if p.exists():
                p.unlink()
            return True
        except Exception as e:
            if attempt == retries:
                print(f"[CLEANUP][WARN] Could not delete {p}: {e}")
                return False
            time.sleep(delay)
    return False


def _parse_epoch_utc(date_str: str, time_str: str, *, label: str) -> int:
    """
    Requires BOTH date+time, accepts HH:MM or HH:MM:SS, and returns epoch seconds (UTC).
    """
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()

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

def parse_exit_gap_seconds(s: str) -> int:
    """
    GUI flag parsing:
      - "" or whitespace => disabled => -1
      - otherwise must be a positive integer (>0)
    Returns:
      -1 if disabled, else positive int
    Raises ValueError on invalid non-empty input.
    """
    s = (s or "").strip()
    if s == "":
        return -1
    try:
        v = int(s, 10)
    except ValueError:
        raise ValueError(f"--exit-gap-seconds must be a positive integer or blank (got {s!r}).")
    if v <= 0:
        raise ValueError(f"--exit-gap-seconds must be > 0 or blank (got {v}).")
    return v



def run_converter(converter_exe: str, in_file: Path, out_file: Path, *, exit_token: str, exit_gap_seconds: int):
    out_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [converter_exe, str(in_file), str(out_file)]

    # Always pass exit token (non-empty by validation)
    cmd += ["--exit-token", exit_token]

    # Only pass gap if enabled (>0). Blank in GUI becomes -1 here.
    if exit_gap_seconds > 0:
        cmd += ["--exit-gap-seconds", str(exit_gap_seconds)]

    print(f"[CONVERT] {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print("[CONVERT][ERROR] Converter failed.")
        print("[CONVERT][ERROR] stdout:\n", e.stdout or "")
        print("[CONVERT][ERROR] stderr:\n", e.stderr or "")
        raise


def list_patient_files(compare_dir: str) -> list[Path]:
    p = Path(compare_dir)
    if not p.exists():
        return []
    return sorted(p.glob("*.txt"))


def make_artifact_paths(output_dir: str, run_name: str):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    safe = sanitize_run_name(run_name) or "run"
    stats_csv = out / f"{safe}_all_stats.csv"
    dashboard_dir = out / f"{safe}_dashboard"

    # Optional mixing series file that may already exist (from shuffle_gui)
    mixing_csv = out / "mixing_index_series.csv"

    return stats_csv, dashboard_dir, mixing_csv



def reset_run_outputs(output_dir: str, run_name: str):
    stats_csv, dashboard_dir, mixing_csv = make_artifact_paths(output_dir, run_name)

    # Only reset stats + dashboard; do NOT delete mixing CSV (it may come from shuffle_gui)
    if stats_csv.exists():
        print(f"[RESET] Removing old all stats CSV: {stats_csv}")
        stats_csv.unlink()

    if dashboard_dir.exists():
        print(f"[RESET] Removing old dashboard dir: {dashboard_dir}")
        shutil.rmtree(dashboard_dir, ignore_errors=True)

    return stats_csv, dashboard_dir, mixing_csv



def open_dashboard_if_present(dashboard_dir: Path):
    dash = dashboard_dir / "dashboard.html"
    if not dash.exists():
        print(f"[VIS][WARN] dashboard.html not found at: {dash}")
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(dash))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(dash)], check=False)
        else:
            subprocess.run(["xdg-open", str(dash)], check=False)
    except Exception:
        pass

def ask_include_mixing_if_present(mixing_csv: Path) -> bool:
    """
    If mixing_csv exists and is non-empty, ask user whether to include it.
    Returns True if user clicks Yes, else False.
    """
    try:
        if not mixing_csv.exists() or mixing_csv.stat().st_size == 0:
            return False
    except Exception:
        return False

    try:
        import wx  # Gooey uses wxPython

        msg = (
            f"Detected mixing data file:\n\n{mixing_csv}\n\n"
            "Would you like to include it in the visualization?"
        )
        dlg = wx.MessageDialog(
            None,
            msg,
            "Include Mixing Data?",
            style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        try:
            return dlg.ShowModal() == wx.ID_YES
        finally:
            dlg.Destroy()
    except Exception as e:
        # If dialog fails for any reason, default to NOT including.
        print(f"[VIS][WARN] Could not show mixing prompt ({e}); skipping mixing file.")
        return False


def run_visualize(csv_all: Path, dashboard_dir: Path, mixing_csv: Path):
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        VISUALIZE_PY,
        "--csv",
        str(csv_all),
        "--output-dir",
        str(dashboard_dir),
        "--source-method",
        "Baseline",
    ]

    # Ask user if we should include detected mixing CSV
    if ask_include_mixing_if_present(mixing_csv):
        cmd += ["--csv-mixing", str(mixing_csv)]
        print("[VIS] Including mixing CSV.")
    else:
        print("[VIS] Mixing CSV not included.")

    print(f"[VIS] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    open_dashboard_if_present(dashboard_dir)


def compute_stats(
    *,
    baseline_input: str,
    compare_dir: str,
    output_dir: str,
    exit_token: str,
    exit_gap_seconds: int,
    run_name: str,
    # sweep params
    t_start: int,
    step: int,
    steps: int,
    window: int,
):
    # Always reset run outputs first
    csv_all, dashboard_dir, mixing_csv = reset_run_outputs(output_dir, run_name)

    # Load stats module by path
    stats_mod = load_module_from_path(f"network_stats_mod_{int(time.time()*1e6)}", NETWORK_STATS_PY)

    baseline_path = Path(baseline_input).resolve()
    compare_files = list_patient_files(compare_dir)

    # Build work items: baseline + all files in compare_dir (excluding baseline if present)
    work_items: list[tuple[str, Path]] = [("Baseline", baseline_path)]
    for f in compare_files:
        if f.resolve() == baseline_path:
            continue
        work_items.append(("Compared", f))

    if len(work_items) == 1:
        print(f"[STATS][WARN] No comparison .txt files found in: {compare_dir}")
        print("[STATS] Will process baseline only.")

    # Use a temp conversion folder under output_dir for this run
    safe_run = sanitize_run_name(run_name) or "run"
    converted_dir = Path(output_dir) / f"{safe_run}_converted_tmp"
    if converted_dir.exists():
        shutil.rmtree(converted_dir, ignore_errors=True)
    converted_dir.mkdir(parents=True, exist_ok=True)

    print("\n[STATS] Run-scoped outputs:")
    print(f"  stats_csv        = {csv_all}")
    print(f"  dashboard_out_dir= {dashboard_dir}")
    print(f"  files_to_process = {len(work_items)}")
    print(f"  converted_tmp    = {converted_dir}")

    for idx, (m_label, patient_txt) in enumerate(work_items, start=1):
        print(f"\n[STATS {idx}/{len(work_items)}] {m_label} :: {patient_txt.name}")

        conv_name = patient_txt.with_suffix("").name + ".graph.txt"
        conv_file = converted_dir / conv_name

        print("[STATS] Converting to graph format...")
        run_converter(
            CONVERTER_EXE,
            patient_txt,
            conv_file,
            exit_token=exit_token,
            exit_gap_seconds=exit_gap_seconds,
        )


        print("[STATS] Computing stats...")
        df = None
        gen = stats_mod.AdjacencyMatrixGenerator(str(conv_file), exit_token=exit_token)
        try:
            df = stats_mod.temporal_sweep(
                gen,
                t_start=t_start,
                step=step,
                num_steps=steps,
                window_size=window,
                directed=False,  # FIXED: never keep directed edges
            )
        except Exception as e:
            print(f"[STATS][ERROR] Stats computation failed for {conv_file}: {e}")
        finally:
            try:
                gen.shutdown()
            except Exception:
                pass

        if df is not None and not df.empty:
            df["method"] = m_label
            df["shuffle_file"] = patient_txt.name
            df["converted_file"] = conv_file.name
            df["run_timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            df["window_duration_seconds"] = window
            print(f"[STATS] Appending {len(df)} rows -> {csv_all}")
            append_df_to_csv(df, csv_all)
        else:
            print("[STATS][WARN] No rows produced; skipping CSV append.")

        # Always cleanup converted file immediately
        print("[CLEANUP] Deleting converted file...")
        if safe_unlink(conv_file):
            print(f"[CLEANUP] Deleted: {conv_file}")
        else:
            print(f"[CLEANUP][WARN] Could not delete: {conv_file}")

    # Cleanup temp conversion dir
    shutil.rmtree(converted_dir, ignore_errors=True)

    print("\n[STATS] Done. Running visualization...")
    run_visualize(csv_all, dashboard_dir, mixing_csv)


@Gooey(
    program_name="Shuffle - Network Statistics Tool",
    default_size=(1100, 1500),
    advanced=True,
    tabbed_groups=False,
    show_success_modal=False,
    show_stop_warning=True,
    clear_before_run=True,
    terminal_font_color="black",
    terminal_background_color="white",
    requires_shell=False,
)
def main():
    parser = GooeyParser(description="Compute stats to compare against baseline network.")

    inputs = parser.add_argument_group("Inputs", gooey_options={"columns": 2, "show_border": True})
    inputs.add_argument(
        "--baseline",
        widget="FileChooser",
        help="Baseline patient sequence file (.txt).",
        gooey_options={"full_width": True},
        required=True,
    )

    inputs.add_argument(
        "--compare-dir",
        widget="DirChooser",
        help="Folder containing patient sequence files (*.txt) to compare against baseline.",
        gooey_options={"full_width": True},
        required=True,
    )

    inputs.add_argument( "--exit-token", type=str, default="EXIT", help="Room token that marks an exit event.", gooey_options={}, )
    inputs.add_argument( "--exit-gap-seconds", default="", help="Split sequences when time between exit token and next token exceeds duration.", gooey_options={}, )

    outputs = parser.add_argument_group("Output", gooey_options={"columns": 1, "show_border": True})
    outputs.add_argument(
        "--output-dir",
        widget="DirChooser",
        default="./results_runs",
        help="Folder to store the stats CSV + dashboard output.",
        gooey_options={"full_width": True},
        required=True,
    )
    outputs.add_argument(
        "--run-name",
        default="",
        help="Base name for this run. If empty, an automatic name is generated.",
        gooey_options={"full_width": True},
    )

    sweep = parser.add_argument_group("Windowing Parameters", gooey_options={"columns": 2, "show_border": True})
    sweep.add_argument("--start-date", widget="DateChooser", default="", help="Windowing start date (UTC).")
    sweep.add_argument("--start-time", widget="TimeChooser", default="00:00:00", help="Windowing start time (HH:MM:SS).")
    sweep.add_argument("--end-date", widget="DateChooser", default="", help="Windowing end date (UTC).")
    sweep.add_argument("--end-time", widget="TimeChooser", default="00:00:00", help="Windowing end time (HH:MM:SS).")

    sweep.add_argument("--step", type=int, default=86400, help="Window stride (seconds).")
    sweep.add_argument("--window", type=int, default=86400, help="Window duration (seconds).")

    args = parser.parse_args()

    # ---------------------------
    # Validation + compute steps
    # ---------------------------

    exit_token = (args.exit_token or "").strip()
    if not exit_token:
        print("[ERROR] --exit-token cannot be empty.")
        return

    try:
        exit_gap_seconds = parse_exit_gap_seconds(args.exit_gap_seconds)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return

    try:
        t_start = _parse_epoch_utc(args.start_date, args.start_time, label="start")
        t_end = _parse_epoch_utc(args.end_date, args.end_time, label="end")

        if args.step <= 0:
            print("[ERROR] Window stride must be positive.")
            return
        if args.window <= 0:
            print("[ERROR] Window duration must be positive.")
            return
        if t_end <= t_start:
            print("[ERROR] End datetime must be after start datetime.")
            return

        duration = t_end - t_start
        if duration < args.window:
            num_steps = 0
        else:
            num_steps = ((duration - args.window) // args.step) + 1

        if num_steps <= 0:
            print("[ERROR] Duration is too short for the given window stride (num_steps computed as 0).")
            return

    except ValueError as e:
        print(f"[ERROR] {e}")
        return

    run_name = sanitize_run_name(args.run_name) or default_run_name("stats")

    print("\n[STATS] Running...")
    print(f"[STATS] baseline    = {args.baseline}")
    print(f"[STATS] compare_dir = {args.compare_dir}")
    print(f"[STATS] output_dir  = {args.output_dir}")
    print(f"[STATS] exit_token={exit_token!r} exit_gap_seconds={exit_gap_seconds} (-1 means disabled)")
    print(f"[STATS] run_name    = {run_name}")
    print(f"[STATS] start_epoch_utc={t_start} end_epoch_utc={t_end}")
    print(f"[STATS] stride={args.step} duration={args.window} steps={num_steps} (computed)")

    compute_stats(
        baseline_input=args.baseline,
        compare_dir=args.compare_dir,
        output_dir=args.output_dir,
        exit_token=exit_token,
        exit_gap_seconds=exit_gap_seconds,
        run_name=run_name,
        t_start=t_start,
        step=args.step,
        steps=int(num_steps),
        window=args.window,
    )


if __name__ == "__main__":
    main()
