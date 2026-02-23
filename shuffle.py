"""
shuffle.py

Generate synthetic patient sequence files by shuffling arrival timestamps while preserving
within-sequence timing, with optional hash-based constraints (built-in or plugin-defined).

Also provides utilities for:
- listing/validating shuffle rules
- deterministic parallel shuffle generation
- optional EXIT-gap sequence splitting
- computing and summarizing a windowed mixing-capacity index series
"""

import os
import pathlib
import random
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor
from functools import reduce
from collections import Counter, defaultdict
from math import lgamma

from rules_registry import register_rule, all_rules
from plugin_loader import load_plugin_file

_LOADED_PLUGIN_PATHS = set()

def ensure_plugins_loaded(plugin_paths):
    for p in plugin_paths or []:
        p = str(pathlib.Path(p).expanduser().resolve())
        if p in _LOADED_PLUGIN_PATHS:
            continue
        try:
            load_plugin_file(p)
        except Exception as e:
            raise SystemExit(f"Failed to load plugin '{p}': {e}")
        _LOADED_PLUGIN_PATHS.add(p)

# ---------------------------
# I/O helpers
# ---------------------------

def load_data(file_path):
    with open(file_path, 'r') as file:
        data = file.readlines()
    return [line.strip() for line in data]

def save_shuffled_sequence(sequence, file_path):
    with open(file_path, 'w') as file:
        for row in sequence:
            file.write(row + '\n')

# ---------------------------
# Hash functions
# ---------------------------

def full_shuffle(dt):
    # put all in same pool
    return 1

def same_year(dt):
    return dt.year

def same_month(dt):
    return (dt.year, dt.month)

def same_day_of_week(dt):
    return dt.weekday()

def same_time_of_day(dt):
    hour = dt.hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"

# Register built-in rules (plugins will add more at runtime)
register_rule("Full Shuffle", full_shuffle, "All arrivals in one pool (no constraints).")
register_rule("Shuffle by Year", same_year, "Only shuffle among arrivals in the same year.")
register_rule("Shuffle by Month", same_month, "Only shuffle among arrivals in the same month.")
register_rule("Shuffle by Day of the Week", same_day_of_week, "Only shuffle among same weekday arrivals.")
register_rule("Shuffle by Time of Day", same_time_of_day, "Only shuffle among morning/afternoon/evening/night.")

def list_hash_names() -> list[str]:
    # returns current rule names including plugins already loaded in this process
    return list(all_rules().keys())

def _rule_fn(name: str):
    return all_rules()[name].fn

# ---------------------------
# Core logic (shuffle)
# ---------------------------

def _parse_row_events(row: str) -> list[tuple[str, datetime]]:
    items = row.split(',')
    if len(items) % 2 != 0:
        raise ValueError(f"Bad row (odd number of tokens): {row}")
    events = []
    for i in range(0, len(items), 2):
        room = items[i]
        dt = datetime.strptime(items[i + 1], "%Y-%m-%d %H:%M:%S")
        events.append((room, dt))
    return events

def _split_events_on_exit_gap(
    events: list[tuple[str, datetime]],
    threshold: timedelta | None,
    exit_token: str = "EXIT",
) -> list[list[tuple[str, datetime]]]:
    """
    Returns list of segments (each a list of (room, dt)).
    Split happens when current room == EXIT and gap to next event > threshold.
    EXIT stays with the preceding segment; next event starts the new segment.
    """
    if not events:
        return []
    if threshold is None:
        return [events]

    segments = []
    current = [events[0]]

    for idx in range(1, len(events)):
        prev_room, prev_dt = events[idx - 1]
        room, dt = events[idx]

        # if the previous event was EXIT, check the gap to the next event
        if prev_room == exit_token and (dt - prev_dt) > threshold:
            # close current segment at the EXIT (already included)
            segments.append(current)
            # start a new segment with the current event
            current = [(room, dt)]
        else:
            current.append((room, dt))

    segments.append(current)
    return segments


def calculate_time_deltas(input_data, exit_gap_seconds: float | None = None, exit_token: str = "EXIT"):
    """
    Optionally split a row into multiple sequences when the time between an EXIT
    and the next token exceeds exit_gap_seconds.
    """
    threshold = None if exit_gap_seconds is None else timedelta(seconds=float(exit_gap_seconds))

    sequences = []
    initial_datetimes = []

    for row in input_data:
        events = _parse_row_events(row)
        for segment in _split_events_on_exit_gap(events, threshold, exit_token=exit_token):
            first_datetime = segment[0][1]
            sequence = []

            for j, (room, dt) in enumerate(segment):
                if j == 0:
                    sequence.append((room, dt))
                else:
                    sequence.append((room, dt - first_datetime))

            sequences.append(sequence)
            initial_datetimes.append(first_datetime)

    return sequences, initial_datetimes

def shuffle_sequences(sequences, initial_datetimes, selected_hash_functions):
    """
    Uses global 'random' module state. Determinism is achieved by seeding before calling this function.
    """

    # 1) Build grouping keys
    pool_fns = [fn for fn in selected_hash_functions]

    hash_groups = defaultdict(list)
    for i, dt in enumerate(initial_datetimes):
        key = tuple(fn(dt) for fn in pool_fns) if pool_fns else None  # one bucket if no pool_fns
        hash_groups[key].append(i)

    # 2) Shuffle within each hash group (map dest -> src)
    reshuffled_initial_datetimes = initial_datetimes[:]
    for group_indices in hash_groups.values():
        if len(group_indices) > 1:
            perm = group_indices[:]           # copy as source
            random.shuffle(perm)              # permute source indices
            for dest_idx, src_idx in zip(group_indices, perm):
                reshuffled_initial_datetimes[dest_idx] = initial_datetimes[src_idx]


    reshuffled_sequences = []

    for i, sequence in enumerate(sequences):
        new_initial_datetime = reshuffled_initial_datetimes[i]
        reshuffled_sequence = []

        for j, (room, time_info) in enumerate(sequence):
            if j == 0:
                reshuffled_sequence.append((room, new_initial_datetime))
            else:
                new_datetime = new_initial_datetime + time_info
                reshuffled_sequence.append((room, new_datetime))

        output_row = []
        for room, dt in reshuffled_sequence:
            output_row.append(room)
            output_row.append(dt.strftime("%Y-%m-%d %H:%M:%S"))

        reshuffled_sequences.append(','.join(output_row))

    return reshuffled_sequences

def generate_one_shuffle(job_index, sequences, initial_datetimes, selected_hash_names, base_seed, plugin_paths=None):
    """
    Each job gets a deterministic seed derived from base_seed and job_index.
    This controls random.shuffle calls.
    """
    if base_seed is not None:
        # Use a simple, reproducible derivation to avoid collisions
        job_seed = (int(base_seed) + int(job_index)) & 0xFFFFFFFF
        random.seed(job_seed)

    # Ensure plugins loaded in worker as well (needed for spawn)
    ensure_plugins_loaded(plugin_paths)
    
    # Reconstruct function objects from names (safer for multiprocessing)
    selected_fns = [_rule_fn(name) for name in selected_hash_names]
    return shuffle_sequences(sequences, initial_datetimes, selected_fns)

# ---------------------------
# Mixing capacity metric (pure functions; orchestrator handles CSV)
# ---------------------------

def _logfact(n: int) -> float:
    """Stable log(n!) via lgamma."""
    return lgamma(n + 1)

def _load_arrivals_only_from_lines(lines, exit_gap_seconds: float | None = None, exit_token: str = "EXIT"):
    """
    Extract (pseudo_arrival_id, first_datetime) from input rows.

    If exit_gap_seconds is set, a single input line may produce multiple arrivals:
    one per segment created by splitting on (EXIT -> next) gap > threshold.
    """
    threshold = None if exit_gap_seconds is None else timedelta(seconds=float(exit_gap_seconds))

    arrivals = []
    arrival_id = 0

    for line_no, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        events = _parse_row_events(line)
        segments = _split_events_on_exit_gap(events, threshold, exit_token=exit_token)

        for seg_idx, segment in enumerate(segments):
            if not segment:
                continue
            first_dt = segment[0][1]
            # unique id per segment; keep line_no in there if you ever want traceability
            arrivals.append(((line_no, seg_idx, arrival_id), first_dt))
            arrival_id += 1

    return arrivals

def _compute_mixing_index_series_from_arrivals(
    arrivals,
    selected_hash_names,
    t_start: int, step: int, num_steps: int, window_size: int
):
    """
    Compute per-window mixing capacity time series.

    Returns list of dict rows:
      {t_start, t_end, window_size, N, logM, logMmax, I, rules}
    With:
      I in [0,1], rules is "name + name + ...", may be empty for Unshuffled.

    Notes:
    - Reuses existing hash functions from registry to build pool keys (never calls full_shuffle).
    - Handles identical timestamps via multiplicities.
    - Windows with N<=1: define I=1.0 (constraints don't matter).
    """
    # pre-sort arrivals by timestamp
    arrivals = sorted(arrivals, key=lambda x: x[1])
    stamps = [int(dt.timestamp()) for _, dt in arrivals]

    # Unconstrained only when no pool functions were selected
    rules = all_rules()
    pool_fns = [rules[n].fn for n in selected_hash_names if n in rules]
    unconstrained = not pool_fns


    rows = []
    left = 0
    right = 0
    for i in range(num_steps):
        w_start = t_start + i * step
        w_end   = w_start + window_size

        # advance pointers so that arrivals[left:right] ∈ [w_start, w_end)
        while left < len(stamps) and stamps[left] < w_start:
            left += 1
        while right < len(stamps) and stamps[right] < w_end:
            right += 1

        window = arrivals[left:right]
        N = len(window)

        if N <= 1:
            rows.append({
                "t_start": w_start, "t_end": w_end, "window_size": window_size,
                "N": N, "logM": 0.0, "logMmax": 0.0, "I": 1.0,
                "rules": " + ".join(selected_hash_names)
            })
            continue

        # multiplicities of exact timestamps across the whole window
        ts_all = [dt.strftime("%Y-%m-%d %H:%M:%S") for _, dt in window]
        mult_all = Counter(ts_all)
        logMmax = _logfact(N) - sum(_logfact(c) for c in mult_all.values())

        if logMmax <= 0:
            rows.append({
                "t_start": w_start, "t_end": w_end, "window_size": window_size,
                "N": N, "logM": 0.0, "logMmax": 0.0, "I": 1.0,
                "rules": " + ".join(selected_hash_names)
            })
            continue

        # If unconstrained or no pool functions were selected, logM equals the unconstrained baseline.
        if unconstrained:
            logM = logMmax
        else:
            # group by combined pool key
            pools = defaultdict(list)
            for _, dt in window:
                key = tuple(fn(dt) for fn in pool_fns)
                pools[key].append(dt)

            logM = 0.0
            for dts in pools.values():
                n_p = len(dts)
                ts_p = [dt.strftime("%Y-%m-%d %H:%M:%S") for dt in dts]
                mult_p = Counter(ts_p)
                logM += _logfact(n_p) - sum(_logfact(c) for c in mult_p.values())

        I = max(0.0, min(1.0, logM / logMmax))
        rows.append({
            "t_start": w_start, "t_end": w_end, "window_size": window_size,
            "N": N, "logM": logM, "logMmax": logMmax, "I": I,
            "rules": " + ".join(selected_hash_names)
        })
    return rows

def compute_mixing_index_series(
    input_path: str,
    selected_hash_names,
    t_start: int, step: int, num_steps: int, window_size: int, exit_gap_seconds: float | None = None,
    exit_token: str = "EXIT"
):
    """
    Public API for orchestrators:
      - Reads arrivals from input_path
      - Computes per-window mixing series using selected_hash_names and time sweep args
      - Returns list of rows with fields:
          t_start, t_end, window_size, N, logM, logMmax, I, rules
    """
    lines = load_data(input_path)
    arrivals = _load_arrivals_only_from_lines(lines, exit_gap_seconds=exit_gap_seconds, exit_token=exit_token)
    if not arrivals:
        return []
    return _compute_mixing_index_series_from_arrivals(
        arrivals, selected_hash_names, t_start, step, num_steps, window_size
    )

def summarize_mixing_rows(per_window_rows):
    """
    Aggregate an overall network statistic from per-window rows.

    Returns a dict:
      {
        "metric": "mixing_summary",
        "I_global": sum(logM)/sum(logMmax)  (0..1),
        "logM_sum": ...,
        "logMmax_sum": ...,
        "N_total": sum of N,
        "rules": " + ".join(names)
      }
    """
    if not per_window_rows:
        return {
            "metric": "mixing_summary",
            "I_global": "",
            "logM_sum": 0.0,
            "logMmax_sum": 0.0,
            "N_total": 0,
            "rules": ""
        }
    logM_sum = sum(r.get("logM", 0.0) for r in per_window_rows)
    logMmax_sum = sum(r.get("logMmax", 0.0) for r in per_window_rows)
    I_global = (logM_sum / logMmax_sum) if logMmax_sum > 0 else 1.0
    rules = per_window_rows[0].get("rules", "")
    N_total = sum(r.get("N", 0) for r in per_window_rows)
    return {
        "metric": "mixing_summary",
        "I_global": I_global,
        "logM_sum": logM_sum,
        "logMmax_sum": logMmax_sum,
        "N_total": N_total,
        "rules": rules
    }

# ---------------------------
# CLI
# ---------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Shuffle sequences with optional hash constraints. Supports deterministic seeding and CLI use."
    )
    parser.add_argument("-i", "--input", help="Path to input text file.")
    parser.add_argument("-o", "--output", help="Directory to write shuffled sequences.")
    parser.add_argument("-n", "--num-shuffles", type=int, help="Number of shuffled sequence files to create.", dest="num_shuffles")
    parser.add_argument(
        "-H", "--hashes",
        nargs="+",
        help=(
            "Hash function selections by name or index (1-based). "
            "Examples: --hashes 1 3  OR  --hashes 'Shuffle by Year' 'Shuffle by Month'"
        ),
    )
    parser.add_argument(
    "--exit-token",type=str,default="EXIT",help="Room token that marks an exit event for splitting (default: EXIT).")
    parser.add_argument("--exit-gap-seconds",type=float,default=None,help="If set, split sequences when time between EXIT and next token exceeds this many seconds.")

    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible shuffles (optional).")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker processes (default: CPU count).")
    parser.add_argument("--list-hashes", action="store_true", help="List available hash functions and exit.")
    parser.add_argument( "--plugin", action="append", default=[], help="Path to a Python plugin file that registers additional shuffle rules. Can be used multiple times.",)
    return parser.parse_args()

def list_hashes():
    print("Available hash functions:")
    for i, name in enumerate(list_hash_names(), start=1):
        print(f"{i}. {name}")

def resolve_hash_selections(hashes_arg):
    """
    Accepts a list of tokens that can be 1-based indices or exact names.
    Returns a list of canonical names.
    """
    if not hashes_arg:
        return []  # No constraints -> behaves like 'Full Shuffle' only if you include it explicitly
    selected_names = []
    names = list_hash_names()
    rules = all_rules()
    for token in hashes_arg:
        # Try index
        try:
            idx = int(token)
            if 1 <= idx <= len(names):
                selected_names.append(names[idx - 1])
                continue
            else:
                raise ValueError
        except ValueError:
            # Try exact name
            if token in rules:
                selected_names.append(token)
            else:
                # Try forgiving match (case-insensitive full name)
                matches = [name for name in names if name.lower() == token.lower()]
                if matches:
                    selected_names.append(matches[0])
                else:
                    raise SystemExit(f"Unknown hash selection: {token}")
    return selected_names

def generate_shuffles(input_path: str, output_dir: str, num_shuffles: int,
                      selected_hash_names: list[str], seed: int | None = None,
                      workers: int | None = None, plugin_paths: list[str] | None = None,
                      exit_gap_seconds: float | None = None,
                      exit_token: str = "EXIT"):
    input_data = load_data(input_path)
    sequences, initial_datetimes = calculate_time_deltas(input_data, exit_gap_seconds=exit_gap_seconds, exit_token=exit_token)

    os.makedirs(output_dir, exist_ok=True)

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                generate_one_shuffle,
                idx,
                sequences,
                initial_datetimes,
                selected_hash_names,
                seed,
                plugin_paths
            )
            for idx in range(num_shuffles)
        ]

        for idx, future in enumerate(futures):
            shuffled_sequences = future.result()
            file_path = os.path.join(output_dir, f"{idx}.txt")
            save_shuffled_sequence(shuffled_sequences, file_path)

# ---------------------------
# Main
# ---------------------------

if __name__ == "__main__":
    args = parse_args()

    # Load plugin files BEFORE list-hashes or resolving selections
    ensure_plugins_loaded(args.plugin)


    if args.list_hashes:
        list_hashes()
        raise SystemExit(0)

    # If any required args are missing, fall back to interactive prompts (backward-compatible)
    if not (args.input and args.output and args.num_shuffles is not None and args.hashes):
        if not args.input:
            args.input = input("Enter the path to the input text file: ").strip()
        if not args.output:
            args.output = input("Enter the directory where shuffled sequences should be saved: ").strip()
        if args.num_shuffles is None:
            args.num_shuffles = int(input("Enter the number of shuffled sequences to create: ").strip())
        if not args.hashes:
            list_hashes()
            raw = input("Select hash functions to use (by number or name, space-separated): ").split()
            args.hashes = raw

    selected_hash_names = resolve_hash_selections(args.hashes)

    # Load data
    input_data = load_data(args.input)

    # Precompute deltas
    sequences, initial_datetimes = calculate_time_deltas(input_data, exit_gap_seconds=args.exit_gap_seconds, exit_token=args.exit_token)

    # Ensure output dir exists
    os.makedirs(args.output, exist_ok=True)

    # Parallel generation
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                generate_one_shuffle,
                idx,
                sequences,
                initial_datetimes,
                selected_hash_names,
                args.seed,
                args.plugin
            )
            for idx in range(args.num_shuffles)
        ]

        for idx, future in enumerate(futures):
            shuffled_sequences = future.result()
            file_path = os.path.join(args.output, f"{idx}.txt")
            save_shuffled_sequence(shuffled_sequences, file_path)

    print(
        f"{args.num_shuffles} shuffled sequence files have been created in {args.output} "
        f"using: {', '.join(selected_hash_names)}"
        + (f" (seed={args.seed})" if args.seed is not None else "")
        + "."
    )

