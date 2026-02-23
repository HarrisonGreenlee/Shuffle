"""
Download the Gowalla check-ins file from SNAP, then convert it into
per-session room/timestamp sequences.

Input rows (whitespace or tab separated):
[user] [check-in time] [latitude] [longitude] [location id]

Output (one line per session):
room1,YYYY-mm-dd HH:MM:SS,room2,YYYY-mm-dd HH:MM:SS,...

Notes:
- We start a new session when the inactivity gap between consecutive
  check-ins exceeds GAP_HOURS.
- We process one user at a time (the file is organized in user blocks),
  so memory use stays small.

Filter:
- Only include sessions whose *first check-in* ("arrival") is within Jan-Feb 2010.
  (i.e., 2010-01-01T00:00:00Z <= start < 2010-03-01T00:00:00Z)
- We also drop individual events outside this window so output is strictly within it.
"""

from datetime import datetime, timedelta
import gzip
import os
import shutil
import urllib.request

URL = "https://snap.stanford.edu/data/loc-gowalla_totalCheckins.txt.gz"
GZ_PATH = "loc-gowalla_totalCheckins.txt.gz"
OUTPUT_PATH = "gowalla_roomseq_sessions.txt"

GAP_HOURS = 7  # new session if inactive longer than this
PROGRESS_EVERY_LINES = 2_000_000  # print progress every N input lines

# --- Time window: Jan-Feb 2010 (UTC) ---
WINDOW_START = datetime.fromisoformat("2010-01-01T00:00:00+00:00")
WINDOW_END   = datetime.fromisoformat("2010-03-01T00:00:00+00:00")  # exclusive

def download_if_missing(url: str, out_path: str) -> None:
    """Download url to out_path if it doesn't already exist."""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"[skip] Found {out_path} (already downloaded).")
        return

    print(f"[download] Fetching {url}")
    with urllib.request.urlopen(url) as r, open(out_path, "wb") as f:
        shutil.copyfileobj(r, f)
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"[download] Saved {out_path} ({size_mb:.1f} MB)")

def parse_ts(ts: str) -> datetime:
    # Example: 2010-07-24T13:45:06Z
    # Convert trailing Z to +00:00 so datetime.fromisoformat can parse.
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)

def fmt_ts(dt: datetime) -> str:
    # Required: YYYY-mm-dd HH:MM:SS
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def write_session(out_f, session):
    """Write one session (list of (dt, location_id)) as a single CSV line."""
    if not session:
        return
    parts = []
    for dt, loc in session:
        parts.append(loc)
        parts.append(fmt_ts(dt))
    out_f.write(",".join(parts) + "\n")

def flush_user_as_sessions(out_f, events):
    """
    Convert one user's events into sessions and write them.
    events: list of (dt, location_id), unsorted.
    """
    if not events:
        return 0

    events.sort(key=lambda x: x[0])

    gap = timedelta(hours=GAP_HOURS)
    session = [events[0]]
    sessions_written = 0

    def maybe_write(sess):
        nonlocal sessions_written
        if not sess:
            return
        # "arrival" == first timestamp of the session
        start_dt = sess[0][0]
        if WINDOW_START <= start_dt < WINDOW_END:
            write_session(out_f, sess)
            sessions_written += 1

    for dt, loc in events[1:]:
        if dt - session[-1][0] > gap:
            maybe_write(session)
            session = [(dt, loc)]
        else:
            session.append((dt, loc))

    maybe_write(session)
    return sessions_written

def main():
    download_if_missing(URL, GZ_PATH)

    print(f"[convert] Reading {GZ_PATH} (streaming gzip) and writing {OUTPUT_PATH}")
    print(f"[convert] Session gap threshold: {GAP_HOURS} hours")
    print(f"[convert] Keeping sessions starting in: {WINDOW_START.isoformat()} .. {WINDOW_END.isoformat()} (end exclusive)")

    current_user = None
    events = []

    lines_read = 0
    users_seen = 0
    sessions_written = 0

    with gzip.open(GZ_PATH, "rt", encoding="utf-8", errors="replace") as f_in, \
         open(OUTPUT_PATH, "w", encoding="utf-8") as f_out:

        for raw in f_in:
            lines_read += 1

            line = raw.strip()
            if not line:
                continue

            cols = line.split()  # works for tab- or space-separated
            if len(cols) < 5:
                continue

            user = cols[0]
            ts = cols[1]
            loc = cols[4]  # location id == "room id"

            if current_user is None:
                current_user = user

            # When the user changes, finalize the previous user's sessions
            if user != current_user:
                sessions_written += flush_user_as_sessions(f_out, events)
                users_seen += 1
                current_user = user
                events = []

            try:
                dt = parse_ts(ts)
            except Exception:
                continue

            # Drop events outside Jan-Feb 2010 so output is strictly within the window.
            if dt < WINDOW_START or dt >= WINDOW_END:
                continue

            events.append((dt, loc))

            if lines_read % PROGRESS_EVERY_LINES == 0:
                print(f"[progress] lines={lines_read:,} users={users_seen:,} sessions={sessions_written:,}")

        sessions_written += flush_user_as_sessions(f_out, events)
        if current_user is not None:
            users_seen += 1

    print(f"[done] lines={lines_read:,} users={users_seen:,} sessions={sessions_written:,}")
    print(f"[done] Wrote: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()