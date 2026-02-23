#!/usr/bin/env python3
"""
filter_rooms.py

Just a preprocessing script! If you want to filter your files to some time range it might be useful.

Reads a text file where each line looks like:
  Room A, 2016-01-07 05:45:00,Room A, 2016-01-07 05:58:00,Room A,2016-01-08 16:01:00

- X (the room name) can be any characters except a comma.
- Each *odd* field (0-based indexing) is a room name; each *even* field is a timestamp.

We keep lines whose sequence *overlaps* the requested time range:
keep if last_ts >= range_start_epoch AND first_ts <= range_end_epoch.
(Equivalently: not (last_ts < range_start OR first_ts > range_end).)


Outputs:
  - x.txt:           lines that PASS the span check (kept)
  - filtered_x.txt:  lines that FAIL the span check (discarded), with a short reason in a comment

Notes:
- The script streams the file (no full-file load).
- You can supply either an explicit end epoch or a duration in seconds to derive it.
- Timestamps in the file are parsed with --timestamp-format; optionally provide --timezone.

Example:
  python filter_rooms.py \
    --input big_input.txt \
    --start-epoch 1452124800 \
    --end-epoch 1452211200

Or using a duration (12 hours here):
  python filter_rooms.py \
    --input big_input.txt \
    --start-epoch 1452124800 \
    --range-seconds 43200
"""

import argparse
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

def parse_args():
    ap = argparse.ArgumentParser(
        description="Filter lines that overlap a given epoch range using the FIRST and LAST timestamps per line."
    )
    ap.add_argument("--input", required=True, help="Path to the input text file.")
    ap.add_argument("--out-pass", default="x.txt", help="Output file for kept lines (default: x.txt).")
    ap.add_argument("--out-fail", default="filtered_x.txt", help="Output file for filtered-out lines (default: filtered_x.txt).")
    ap.add_argument("--timestamp-format", default="%Y-%m-%d %H:%M:%S",
                    help="Datetime strptime format for timestamps in the file (default: %%Y-%%m-%%d %%H:%%M:%%S).")
    ap.add_argument("--timezone", default=None,
                    help="IANA timezone for interpreting input timestamps (e.g., 'UTC', 'America/Los_Angeles'). "
                         "If omitted, timestamps are assumed to already be UTC.")
    ap.add_argument("--start-epoch", type=float, required=True,
                    help="Start of the required span in UNIX epoch seconds.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--end-epoch", type=float, help="End of the required span in UNIX epoch seconds.")
    group.add_argument("--range-seconds", type=float, help="Duration in seconds added to --start-epoch to form end-epoch.")
    return ap.parse_args()

def to_epoch(ts_str: str, fmt: str, tz: ZoneInfo | None) -> float:
    """
    Parse ts_str using fmt. If tz is provided, localize to that timezone then convert to UTC.
    If tz is None, interpret the parsed datetime as UTC directly.
    Return UNIX epoch seconds as float.
    """
    ts_str = ts_str.strip()
    dt = datetime.strptime(ts_str, fmt)
    if tz is not None:
        # Treat parsed wall time as occurring in the given timezone, then convert to UTC
        dt = dt.replace(tzinfo=tz).astimezone(timezone.utc)
    else:
        # Assume input timestamps are already UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()

def first_and_last_timestamps(line: str, ts_fmt: str, tz: ZoneInfo | None):
    """
    Extract first and last timestamps (as epoch seconds) from the comma-separated line.
    The line format is: room, timestamp, room, timestamp, ...
    So timestamps are expected at indices 1, 3, 5, ... (0-based).
    Returns (first_epoch, last_epoch) or raises ValueError if not parseable.
    """
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        raise ValueError("Line has fewer than 2 comma-separated fields.")
    # Timestamps are at odd indices: 1, 3, 5, ...
    ts_indices = [i for i in range(1, len(parts), 2)]
    if not ts_indices:
        raise ValueError("No timestamp fields found.")
    try:
        first_epoch = to_epoch(parts[ts_indices[0]], ts_fmt, tz)
        last_epoch  = to_epoch(parts[ts_indices[-1]], ts_fmt, tz)
    except Exception as e:
        raise ValueError(f"Timestamp parse failed: {e}") from e
    return first_epoch, last_epoch

def main():
    args = parse_args()

    # Compute end of range from either --end-epoch or --range-seconds
    if args.end_epoch is not None:
        range_start = float(args.start_epoch)
        range_end = float(args.end_epoch)
    else:
        range_start = float(args.start_epoch)
        range_end = range_start + float(args.range_seconds)

    if range_end < range_start:
        print("Error: end of range is before start of range.", file=sys.stderr)
        sys.exit(2)

    tz = None
    if args.timezone:
        try:
            tz = ZoneInfo(args.timezone)
        except Exception as e:
            print(f"Error: invalid timezone '{args.timezone}': {e}", file=sys.stderr)
            sys.exit(2)

    kept = 0
    dropped = 0
    bad = 0

    # Open all files once; stream the input for huge files.
    with open(args.input, "r", encoding="utf-8", errors="replace") as fin, \
         open(args.out_pass, "w", encoding="utf-8") as fout_keep, \
         open(args.out_fail, "w", encoding="utf-8") as fout_drop:

        # Make the intent crystal-clear in the output header comments
        # fout_keep.write("# Lines that OVERLAP the requested epoch range, i.e., last_ts >= range_start AND first_ts <= range_end\n")
        # fout_keep.write(f"# range_start={range_start}  range_end={range_end}\n")
        # fout_drop.write("# Lines that do NOT overlap the requested epoch range, or lines with parse errors\n")
        # fout_drop.write(f"# range_start={range_start}  range_end={range_end}\n")

        for lineno, raw in enumerate(fin, start=1):
            line = raw.rstrip("\n")
            if not line.strip():
                # Empty/whitespace-only lines go to dropped with a reason
                fout_drop.write(f"{line}  # dropped: empty line (line {lineno})\n")
                dropped += 1
                continue
            try:
                first_epoch, last_epoch = first_and_last_timestamps(line, args.timestamp_format, tz)
            except ValueError as e:
                # If we can't parse, record in filtered file with reason
                fout_drop.write(f"{line}  # dropped: parse error ({e}); line {lineno}\n")
                bad += 1
                continue

            # *** CORE FILTERING LOGIC ***
            # *** CORE FILTERING LOGIC (OVERLAP) ***
            # Keep if the sequence overlaps the window [range_start, range_end].
            # Overlap occurs when the last timestamp is on/after the window start
            # AND the first timestamp is on/before the window end:
            #     last_ts >= range_start  AND  first_ts <= range_end
            if (last_epoch >= range_start) and (first_epoch <= range_end):
                fout_keep.write(f"{line}\n")
                kept += 1
            else:
                # No overlap with the requested window
                fout_drop.write(
                    f"{line}  # dropped: no overlap (first={first_epoch}, last={last_epoch})\n"
                )
                dropped += 1


    # Simple summary to stderr (so it doesn't pollute output files)
    print(f"Done. Kept: {kept}, Dropped: {dropped}, Parse errors: {bad}", file=sys.stderr)

if __name__ == "__main__":
    main()
