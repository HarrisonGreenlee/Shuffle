#!/usr/bin/env python3
import argparse
import importlib.util
import os
import sys
import glob
import subprocess
from datetime import datetime
try:
    from datetime import UTC          # Py 3.11+
except ImportError:                    # Py < 3.11
    from datetime import timezone as _tz
    UTC = _tz.utc
from pathlib import Path
import pandas as pd
import time

# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def load_module_from_path(module_name: str, path: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def run_shuffler(shuffler_py: str, input_file: str, out_dir: Path, num: int,
                 hashes: list[str], seed: int | None, workers: int | None):
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, shuffler_py, "-i", str(input_file), "-o", str(out_dir), "-n", str(num)]
    if hashes:
        cmd += ["-H"] + hashes
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if workers is not None:
        cmd += ["--workers", str(workers)]
    print(f"[SHUFFLER] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def run_converter(converter_exe: str, in_file: Path, out_file: Path, skip_if_exists=True):
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if skip_if_exists and out_file.exists() and out_file.stat().st_size > 0:
        print(f"[CONVERT] Skip (exists): {out_file}")
        return
    cmd = [converter_exe, str(in_file), str(out_file)]
    print(f"[CONVERT] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def safe_unlink(p: Path, retries: int = 3, delay: float = 0.5):
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

def append_df_to_csv(df, csv_path: Path):
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        df.to_csv(f, header=is_new, index=False)
        f.flush()
        os.fsync(f.fileno())

# ------------------------------------------------------------
# Main logic
# ------------------------------------------------------------

def orchestrate(args):
    combos = [
        ["Unshuffled"],  # process the source file once, as a baseline
        ["Full Shuffle"],
        ["Shuffle by Year"],
        ["Shuffle by Year", "Shuffle by Month"],
        ["Shuffle by Year", "Shuffle by Month", "Shuffle by Day of the Week"],
        ["Shuffle by Year", "Shuffle by Month", "Shuffle by Day of the Week", "Shuffle by Time of Day"],
    ]

    def combo_slug(names: list[str]) -> str:
        return " + ".join(names).replace(" ", "_").replace("/", "-")

    stats_mod = load_module_from_path("stats_mod", args.stats)
    shuff_mod = load_module_from_path("shuff_mod", args.shuffler)
    out_root = Path(args.out_root)
    csv_path = Path(args.csv)
    out_root.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    csv_mixing_path = Path(args.csv_mixing)
    csv_mixing_summary_path = Path(args.csv_mixing_summary)
    csv_mixing_path.parent.mkdir(parents=True, exist_ok=True)
    csv_mixing_summary_path.parent.mkdir(parents=True, exist_ok=True)


    if csv_path.exists():
        print(f"[RESET] Removing old CSV: {csv_path}")
        csv_path.unlink()
    if csv_mixing_path.exists():
        print(f"[RESET] Removing old mixing CSV: {csv_mixing_path}")
        csv_mixing_path.unlink()
    if csv_mixing_summary_path.exists():
        print(f"[RESET] Removing old mixing summary CSV: {csv_mixing_summary_path}")
        csv_mixing_summary_path.unlink()


    total_combos = len(combos)
    print(f"[START] Combos: {total_combos}, per-type: {args.per_type}, CSV: {csv_path}")
    print(f"[PARAMS] start={args.start}, step={args.step}, steps={args.steps}, window={args.window}, directed={args.directed}")

    for c_idx, combo in enumerate(combos, start=1):
        slug = combo_slug(combo)
        combo_dir = out_root / slug
        # For Unshuffled, keep outputs separate
        shuffled_dir = combo_dir / ("source" if combo == ["Unshuffled"] else "shuffled")
        converted_dir = combo_dir / "converted"

        print(f"\n[COMBO {c_idx}/{total_combos}] {', '.join(combo)}")
        print(f"[PATHS] shuffled={shuffled_dir} | converted={converted_dir}")

        # (0) Mixing capacity metrics (per-window series + one summary) - written to separate CSVs
        print("[STEP] Computing mixing capacity time series...")
        selected_hash_names = ["__IDENTITY__"] if combo == ["Unshuffled"] else combo

        mix_rows = shuff_mod.compute_mixing_index_series(
            input_path=args.input,
            selected_hash_names=selected_hash_names,
            t_start=args.start,
            step=args.step,
            num_steps=args.steps,
            window_size=args.window
        )

        mix_df = pd.DataFrame(mix_rows)
        if not mix_df.empty:
            mix_df["method"] = " + ".join(combo)
            mix_df["combo_dir"] = str(combo_dir)
            mix_df["seed_base"] = args.seed
            mix_df["run_timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            mix_df["window_size_seconds"] = args.window
            print(f"[STEP] Appending {len(mix_df)} mixing rows to CSV (series)...")
            append_df_to_csv(mix_df, csv_mixing_path)
            print(f"[OK] Mixing series CSV updated: {csv_mixing_path}")
        else:
            print("[WARN] No mixing rows produced for this combo.")

        # Single summary row per combo to a separate CSV
        mix_summary = shuff_mod.summarize_mixing_rows(mix_rows)
        mix_summary.update({
            "method": " + ".join(combo),
            "combo_dir": str(combo_dir),
            "seed_base": args.seed,
            "run_timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "window_size_seconds": args.window,
        })
        mix_summary_df = pd.DataFrame([mix_summary])
        print("[STEP] Appending mixing summary row to CSV (summary)...")
        append_df_to_csv(mix_summary_df, csv_mixing_summary_path)
        print(f"[OK] Mixing summary CSV updated: {csv_mixing_summary_path}")


        # (1) Generate shuffled networks OR skip for Unshuffled
        if combo == ["Unshuffled"]:
            print("[STEP] Unshuffled mode — skipping shuffler.")
            shuffled_dir.mkdir(parents=True, exist_ok=True)
            # Treat the original input as the only 'file to process'
            shuffled_files = [Path(args.input)]
        else:
            need_generate = True
            if args.resume and shuffled_dir.exists() and list(shuffled_dir.glob("*.txt")):
                print("[RESUME] Shuffled artifacts found; skipping generation.")
                need_generate = False

            if need_generate:
                print("[STEP] Generating shuffled networks...")
                run_shuffler(
                    shuffler_py=args.shuffler,
                    input_file=args.input,
                    out_dir=shuffled_dir,
                    num=args.per_type,
                    hashes=combo,
                    seed=args.seed,
                    workers=args.workers,
                )
            else:
                print("[STEP] Generation skipped (resume).")

            shuffled_files = sorted(shuffled_dir.glob("*.txt"))

        if not shuffled_files:
            print(f"[WARN] No .txt files to process in {shuffled_dir}; moving to next combo.")
            continue

        print(f"[INFO] {len(shuffled_files)} file(s) to process.")

        for f_idx, f in enumerate(shuffled_files, start=1):
            print(f"\n[FILE {f_idx}/{len(shuffled_files)}] {f.name}")
            # For Unshuffled, make a clear output name
            base_name = f.with_suffix("").name
            out_name = (base_name + ".graph.txt")
            conv_file = converted_dir / out_name

            print("[STEP] Converting to stats format...")
            run_converter(args.converter, f, conv_file, skip_if_exists=args.resume)

            print("[STEP] Computing stats...")
            df = None
            gen = stats_mod.AdjacencyMatrixGenerator(str(conv_file))
            try:
                df = stats_mod.temporal_sweep(
                    gen,
                    t_start=args.start,
                    step=args.step,
                    num_steps=args.steps,
                    window_size=args.window,
                    directed=args.directed
                )
            except Exception as e:
                print(f"[ERROR] Stats computation failed for {conv_file}: {e}")
            finally:
                try:
                    gen.shutdown()
                except Exception:
                    pass

            if df is not None and not df.empty:
                df["method"] = " + ".join(combo)  # will be "Unshuffled" for the source run
                df["shuffle_file"] = f.name       # original filename for Unshuffled
                df["converted_file"] = conv_file.name
                df["combo_dir"] = str(combo_dir)
                df["seed_base"] = args.seed
                df["run_timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                df["window_size_seconds"] = args.window

                print(f"[STEP] Appending {len(df)} rows to CSV...")
                append_df_to_csv(df, csv_path)
                print(f"[OK] CSV updated: {csv_path}")
            else:
                print("[WARN] No rows produced; CSV not updated for this file.")

            print("[CLEANUP] Deleting converted file to save space...")
            if safe_unlink(conv_file):
                print(f"[CLEANUP] Deleted: {conv_file}")
            else:
                print(f"[CLEANUP][WARN] Could not delete: {conv_file}")

    print("\n[DONE] All combos processed. Aggregated CSV:", csv_path)

# ------------------------------------------------------------
# Entry point with defaults
# ------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate shuffle -> convert -> stats -> CSV.")
    parser.add_argument("--shuffler", help="Path to shuffle_sequences.py")
    parser.add_argument("--converter", help="Path to build_contact_graph.exe")
    parser.add_argument("--stats", help="Path to stats script")
    parser.add_argument("-i", "--input", help="Input text file for shuffler")
    parser.add_argument("-o", "--out-root", help="Root output directory")
    parser.add_argument("--csv", help="Path to aggregated CSV")
    parser.add_argument("--csv-mixing", help="Path to per-window mixing CSV")
    parser.add_argument("--csv-mixing-summary", help="Path to mixing summary CSV")
    parser.add_argument("-n", "--per-type", type=int, help="Networks per hash combo")
    parser.add_argument("--seed", type=int, help="Base seed")
    parser.add_argument("--workers", type=int, help="Shuffler workers")
    parser.add_argument("--resume", action="store_true", help="Skip generation if artifacts exist")

    parser.add_argument("--start", type=int, help="Sweep start time (epoch)")
    parser.add_argument("--step", type=int, help="Sweep step size (seconds)")
    parser.add_argument("--steps", type=int, help="Number of sweep steps")
    parser.add_argument("--window", type=int, help="Sweep window size (seconds)")
    parser.add_argument("--directed", action="store_true", help="Keep directed edges")

    # Defaults for "daily stats" run
    # defaults = {
    #     "shuffler": "./shuffle.py",
    #     "converter": "./build_contact_graph.exe",
    #     "stats": "./network_stats.py",
    #     "input": "./patient_paths_filtered_2017_2021.txt",
    #     "out_root": "./out",
    #     "csv": "./results/all_stats.csv",
    #     "csv_mixing": "./results/mixing_series.csv",
    #     "csv_mixing_summary": "./results/mixing_summary.csv",
    #     "per_type": 5,
    #     "seed": 42,
    #     "start": 1483232400,
    #     "step": 86400,
    #     "steps": 1460,
    #     "window": 86400,
    # }

    defaults = {
        "shuffler": "./shuffle.py",
        "converter": "./build_contact_graph.exe",
        "stats": "./network_stats.py",
        "input": "./patient_paths_filtered_2016_2022.txt",
        "out_root": "./out",
        "csv": "./results/all_stats.csv",
        "csv_mixing": "./results/mixing_series.csv",
        "csv_mixing_summary": "./results/mixing_summary.csv",
        "per_type": 5,
        "seed": 42,
        "start": 1451653261,
        "step": 604800, # a week
        "steps": 312, # six years ish
        "window": 604800,
    }

        #     defaults = {
    #     "shuffler": "./shuffle.py",
    #     "converter": "./build_contact_graph.exe",
    #     "stats": "./network_stats.py",
    #     "input": "./patient_paths_filtered_2017_2021.txt",
    #     "out_root": "./out",
    #     "csv": "./results/all_stats.csv",
    #     "per_type": 10,
    #     "seed": 42,
    #     "start": 1483232400,
    #     "step": 604800, # a week
    #     "steps": 208, # four years ish
    #     "window": 604800,
    # }


    if len(sys.argv) == 1:
        # No args provided: run defaults
        args = argparse.Namespace(**defaults, workers=None, resume=False, directed=False)
        print("[INFO] No args provided — running default daily stats mode.")
    else:
        parser.set_defaults(**defaults)
        args = parser.parse_args()

    orchestrate(args)
