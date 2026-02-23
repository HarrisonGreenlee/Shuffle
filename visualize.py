import os, io
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from datetime import datetime, UTC
import argparse

# Visualize all of the data and build a nice dashboard.

# ---------------- CONFIG ----------------
CSV_PATH = "results/all_stats.csv"

CSV_MIXING_PATH = "mixing_index_series.csv"  # single mixing file (optional)

OUTPUT_DIR = Path("paper_exports")
DASHBOARD_HTML = OUTPUT_DIR / "dashboard.html"

SOURCE_METHOD_NAME = "Unshuffled"

DISTR_COLOR = (1.0, 0.0, 0.0, 0.18)  # light red with alpha
SOURCE_COLOR = (0.0, 0.0, 0.0, 1.0)  # black
MIX_COLOR = (0.05, 0.3, 0.95, 0.95)  # blue-ish for mixing line
LW_MIX = 2.2
MS_MIX = 2.8

LW_DISTR = 1.0
LW_SOURCE = 2.8
MS_DISTR = 2.2
MS_SOURCE = 2.8
FIGSIZE = (10, 6)
DPI = 300

FONT_SIZE = 20
MINIMAL_FIG_TEXT = True
HIDE_X_TICKS = True


def apply_rcparams():
    plt.rcParams["svg.fonttype"] = "none"  # keep text as text in SVG
    plt.rcParams.update({
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "font.size": FONT_SIZE,
        "axes.labelsize": FONT_SIZE,
        "axes.titlesize": FONT_SIZE + 2,
        "legend.fontsize": FONT_SIZE - 1,
        "xtick.labelsize": FONT_SIZE - 1,
        "ytick.labelsize": FONT_SIZE - 1,
    })


def parse_args():
    p = argparse.ArgumentParser(description="Generate plots + HTML dashboard from stats CSVs.")
    p.add_argument("--csv", default=CSV_PATH, help=f"Stats CSV path (default: {CSV_PATH})")
    p.add_argument(
    "--csv-mixing",
    default=CSV_MIXING_PATH,
    help=f"Mixing series CSV path (default: {CSV_MIXING_PATH}). If missing/empty, mixing plots are skipped.",
    )
    p.add_argument("--output-dir", default=str(OUTPUT_DIR), help=f"Output directory for figures + dashboard (default: {OUTPUT_DIR})")
    p.add_argument("--source-method", default=SOURCE_METHOD_NAME, help=f"Baseline method label (default: {SOURCE_METHOD_NAME})")
    p.add_argument("--font-size", type=int, default=FONT_SIZE, help=f"Matplotlib base font size (default: {FONT_SIZE})")

    # toggles
    p.add_argument("--minimal", dest="minimal", action="store_true", default=MINIMAL_FIG_TEXT,
                   help="Remove titles/labels/legends (tick numbers remain).")
    p.add_argument("--no-minimal", dest="minimal", action="store_false",
                   help="Show titles/labels/legends.")

    p.add_argument("--hide-x-ticks", dest="hide_x_ticks", action="store_true", default=HIDE_X_TICKS,
                   help="Hide x-axis tick labels.")
    p.add_argument("--show-x-ticks", dest="hide_x_ticks", action="store_false",
                   help="Show x-axis tick labels.")

    return p.parse_args()


def apply_runtime_config(args):
    """
    Apply CLI overrides to module-level config that plotting functions reference.
    """
    global CSV_PATH, CSV_MIXING_PATH
    global OUTPUT_DIR, DASHBOARD_HTML, SOURCE_METHOD_NAME
    global MINIMAL_FIG_TEXT, HIDE_X_TICKS, FONT_SIZE

    CSV_PATH = args.csv
    CSV_MIXING_PATH = args.csv_mixing

    OUTPUT_DIR = Path(args.output_dir)
    DASHBOARD_HTML = OUTPUT_DIR / "dashboard.html"
    SOURCE_METHOD_NAME = args.source_method

    FONT_SIZE = int(args.font_size)
    MINIMAL_FIG_TEXT = bool(args.minimal)
    HIDE_X_TICKS = bool(args.hide_x_ticks)

def maybe_read_csv(path: str) -> pd.DataFrame | None:
    if not path:
        return None
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    return pd.read_csv(path)

def require_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise FileNotFoundError(f"Expected CSV at {path} — file not found or empty.")
    return pd.read_csv(path)

def detect_time_col(df: pd.DataFrame) -> str:
    # Prefer explicit epoch-like columns (mixing uses t_start)
    candidates = ["t_start", "window_start", "time", "timestamp", "t", "step_time",
                  "start_time", "window", "epoch"]
    for c in candidates:
        if c in df.columns:
            return c
    # fallback: first integer-like column
    for c in df.columns:
        if c.lower() not in {"seed_base"} and pd.api.types.is_integer_dtype(df[c]):
            return c
    # last fallback: synthesize step index; group only by columns that exist
    group_keys = [c for c in ["method", "shuffle_file"] if c in df.columns]
    if group_keys:
        df["__time_index__"] = df.groupby(group_keys).cumcount()
    else:
        df["__time_index__"] = np.arange(len(df))
    return "__time_index__"

def parse_time_if_needed(df: pd.DataFrame, time_col: str):
    s = df[time_col]
    if pd.api.types.is_string_dtype(s):
        parsed = pd.to_datetime(s, errors="coerce", utc=True)
        if parsed.notna().mean() > 0.5:
            df[time_col] = parsed
    return df

def get_window_duration_seconds(df: pd.DataFrame):
    if "window_duration_seconds" in df.columns:
        vals = pd.to_numeric(df["window_duration_seconds"], errors="coerce").dropna()
        if not vals.empty:
            v = float(vals.mode().iloc[0])  # most common
            return int(v) if v.is_integer() else v
    return None

def build_step_index(df: pd.DataFrame, time_col: str, step_sec: float) -> pd.Series:
    s = df[time_col]
    if pd.api.types.is_datetime64_any_dtype(s):
        t0 = s.min()
        elapsed = (s - t0).dt.total_seconds()
    else:
        s_num = pd.to_numeric(s, errors="coerce")
        t0 = s_num.min()
        elapsed = s_num - t0
    steps = np.floor(elapsed / float(step_sec))
    return np.maximum(steps, 0).astype(int)

def metric_columns(df: pd.DataFrame, time_col: str) -> list[str]:
    meta = {"method","shuffle_file","converted_file","combo_dir",
            "seed_base","run_timestamp", time_col,
            "window_duration_seconds","step_index"}
    metrics = []
    for c in df.columns:
        if c in meta: 
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            metrics.append(c)
        else:
            coerced = pd.to_numeric(df[c], errors="coerce")
            if coerced.notna().any():
                df[c] = coerced
                metrics.append(c)
    if not metrics:
        raise ValueError("No numeric metric columns found to plot.")
    return metrics

def slugify(s: str) -> str:
    return s.replace(" ", "_").replace("/", "-").replace("+", "").strip("_")

def plot_method_metric(df: pd.DataFrame, method: str, metric: str, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    png_path = outdir / f"{slugify(metric)}.png"
    svg_path = outdir / f"{slugify(metric)}.svg"

    fig = plt.figure(figsize=FIGSIZE)
    ax = plt.gca()

    # distribution lines + dots
    df_m = df[df["method"] == method]
    for _, grp in df_m.groupby("shuffle_file", dropna=False):
        x = grp["step_index"].values
        y = grp[metric].values
        if np.all(pd.isna(y)):
            continue
        ax.plot(
            x, y,
            linewidth=LW_DISTR, color=DISTR_COLOR,
            marker="o", markersize=MS_DISTR, markerfacecolor=DISTR_COLOR, markeredgewidth=0
        )

    # overlay source
    if (df["method"] == SOURCE_METHOD_NAME).any():
        df_src = df[df["method"] == SOURCE_METHOD_NAME].sort_values("step_index")
        x = df_src["step_index"].values
        y = df_src[metric].values
        if not np.all(pd.isna(y)):
            ax.plot(
                x, y,
                linewidth=LW_SOURCE, color=SOURCE_COLOR, label="Source network",
                marker="o", markersize=MS_SOURCE, markerfacecolor=SOURCE_COLOR, markeredgewidth=0
            )

    # Title / axis labels (gate them)
    if not MINIMAL_FIG_TEXT:
        ax.set_title(f"{method} — {metric}")
        ax.set_ylabel(metric)
        ax.set_xlabel(r"Window index $w$")
    else:
        ax.set_title("")
        ax.set_ylabel("")
        ax.set_xlabel("")

    ax.xaxis.set_major_locator(MaxNLocator(integer=True))

    ax.grid(True, alpha=0.25)

    # Legend (gate it)
    if (df["method"] == SOURCE_METHOD_NAME).any() and not MINIMAL_FIG_TEXT:
        ax.legend(frameon=False, loc="best")

    # If a legend was created elsewhere, force-remove it when minimal mode is on
    if MINIMAL_FIG_TEXT:
        leg = ax.get_legend()
        if leg is not None:
            leg.remove()

    if HIDE_X_TICKS:
        ax.tick_params(axis="x", which="both", bottom=False, top=False, labelbottom=False)

    fig.tight_layout()

    meta_png = {
        "Title": f"{method} — {metric}",
        "Description": f"Distribution of generated networks (light red) with source network overlay (black). Metric: {metric}.",
        "CreationTime": datetime.now(UTC).isoformat().replace("+00:00","Z"),
    }
    fig.savefig(png_path, bbox_inches="tight", metadata=meta_png)

    meta_svg = {"Date": datetime.now(UTC).isoformat().replace("+00:00","Z")}
    fig.savefig(svg_path, bbox_inches="tight", metadata=meta_svg)

    plt.close(fig)
    return png_path, svg_path

def plot_mixing_method(df_mix: pd.DataFrame, method: str, outdir: Path):
    """
    Plot per-window mixing index I over step_index for a single method.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    png_path = outdir / f"mixing_index_I.png"
    svg_path = outdir / f"mixing_index_I.svg"

    fig = plt.figure(figsize=FIGSIZE)
    ax = plt.gca()

    grp = df_mix[df_mix["method"] == method].sort_values("step_index")
    if grp.empty:
        return None, None

    x = grp["step_index"].values
    y = grp["I"].values

    ax.plot(
        x, y,
        linewidth=LW_MIX, color=MIX_COLOR, marker="o",
        markersize=MS_MIX, markerfacecolor=MIX_COLOR, markeredgewidth=0,
        label="Mixing index I"
    )

    # Title / axis labels (gate them)
    if not MINIMAL_FIG_TEXT:
        ax.set_title(f"{method} — Mixing Capacity (I)")
        ax.set_ylabel(r"$I_w = \log M_w \,/\, \log M_w^{\max}$")
        ax.set_xlabel(r"Window index $w$")
    else:
        ax.set_title("")
        ax.set_ylabel("")
        ax.set_xlabel("")

    ax.set_ylim(-0.02, 1.02)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, alpha=0.25)

    # Legend (gate it)
    if not MINIMAL_FIG_TEXT:
        ax.legend(frameon=False, loc="best")
    else:
        leg = ax.get_legend()
        if leg is not None:
            leg.remove()

    if HIDE_X_TICKS:
        ax.tick_params(axis="x", which="both", bottom=False, top=False, labelbottom=False)

    fig.tight_layout()
    meta_png = {
        "Title": f"{method} — Mixing Capacity (I)",
        "Description": "Per-window mixing index I (normalized log count) over time.",
        "CreationTime": datetime.now(UTC).isoformat().replace("+00:00","Z"),
    }
    fig.savefig(png_path, bbox_inches="tight", metadata=meta_png)

    meta_svg = {"Date": datetime.now(UTC).isoformat().replace("+00:00","Z")}
    fig.savefig(svg_path, bbox_inches="tight", metadata=meta_svg)

    plt.close(fig)
    return png_path, svg_path

def main():
    args = parse_args()
    apply_runtime_config(args)
    apply_rcparams()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = require_csv(CSV_PATH)

    # Load mixing series (optional; separate schema from stats)
    df_mix = maybe_read_csv(CSV_MIXING_PATH)
    if df_mix is not None:
        time_col_mix = "t_start" if "t_start" in df_mix.columns else detect_time_col(df_mix)
        df_mix = parse_time_if_needed(df_mix, time_col_mix)


    time_col = detect_time_col(df)
    df = parse_time_if_needed(df, time_col)

    win_size = get_window_duration_seconds(df)
    if win_size is None:
        raise ValueError("window_duration_seconds not found in CSV. Make sure orchestrator writes it.")

    # step index (integer)
    df["step_index"] = build_step_index(df, time_col, win_size)

    win_size_mix = None
    if df_mix is not None:
        win_size_mix = get_window_duration_seconds(df_mix)
        if win_size_mix is None:
            s = df_mix[time_col_mix].dropna().sort_values()
            if len(s) >= 2:
                if pd.api.types.is_datetime64_any_dtype(s):
                    win_size_mix = int((s.iloc[1] - s.iloc[0]).total_seconds())
                else:
                    s_num = pd.to_numeric(s, errors="coerce").dropna()
                    win_size_mix = int(s_num.iloc[1] - s_num.iloc[0]) if len(s_num) >= 2 else 1
            else:
                win_size_mix = 1


        df_mix["step_index"] = build_step_index(df_mix, time_col_mix, win_size_mix)


    # Sort
    df = df.sort_values(["step_index", "method", "shuffle_file"], kind="mergesort").reset_index(drop=True)

    metrics = metric_columns(df, time_col)
    methods = [m for m in df["method"].unique() if m != SOURCE_METHOD_NAME]
    if not methods:
        print("[WARN] Only one mixing method present; proceeding without method-comparison plots.")
        methods = []

    win_str = f"Window duration: {win_size} seconds"

    generated = []
    for m in methods:
        mslug = slugify(m)
        mdir = OUTPUT_DIR / mslug
        for metric in metrics:
            png, svg = plot_method_metric(df, m, metric, mdir)
            generated.append((m, metric, png.relative_to(OUTPUT_DIR)))

    # ----- Mixing plot -----
    mix_generated = []
    mix_methods = []
    if df_mix is not None and "method" in df_mix.columns and "I" in df_mix.columns:
        mix_methods = sorted([m for m in df_mix["method"].dropna().unique() if m != SOURCE_METHOD_NAME])

        for m in mix_methods:
            mslug = slugify(m)
            mdir = OUTPUT_DIR / mslug / "mixing"
            png, svg = plot_mixing_method(df_mix, m, mdir)
            if png is not None:
                mix_generated.append((m, "Mixing index I", png.relative_to(OUTPUT_DIR)))


    # HTML dashboard
    html = io.StringIO()
    html.write("""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shuffle Methods — Network Statistic Distributions</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:20px;line-height:1.45}
h1{font-size:1.8rem;margin:0 0 0.6rem}
h2{margin:2rem 0 0.3rem}
.sub{color:#444;margin:0 0 0.6rem}
.method{margin-bottom:2rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px;align-items:start}
.card{border:1px solid #ddd;border-radius:12px;padding:10px}
.card img{width:100%;height:auto;border-radius:8px}
legend{font-size:0.95rem;color:#444;margin:0.5rem 0 1rem;display:block}
footer{margin-top: 2rem; font-size: 0.9rem; color: #555;}
</style></head><body>
<h1>Shuffle Methods — Network Statistic Distributions</h1>
<legend>Light red: generated realizations; black: source network. X-axis is <strong>Step</strong> (0-indexed).</legend>
""")

    from collections import defaultdict
    by_method = defaultdict(list)
    for m, metric, rel in generated:
        by_method[m].append((metric, str(rel)))

    for m in sorted(by_method.keys()):
        html.write(f'<div class="method"><h2>{m}</h2>')
        html.write(f'<div class="sub">{win_str}</div>')
        html.write('<div class="grid">')
        for metric, rel in sorted(by_method[m], key=lambda x: x[0].lower()):
            svg_rel = rel[:-4] + ".svg"
            html.write(
                f'<div class="card"><div style="font-weight:600;margin-bottom:6px">{metric}</div>'
                f'<img src="{rel}" alt="{m} — {metric}">'
                f'<div style="margin-top:6px;font-size:0.85rem">'
                f'<a href="{rel}" download>PNG</a> • <a href="{svg_rel}" download>SVG</a>'
                f'</div></div>'
            )
        html.write('</div></div>')

    # ----- Mixing section in HTML -----
    if mix_generated:
        html.write('<h1 style="margin-top:2.2rem">Mixing Capacity — Per-window I_w</h1>')
        html.write('<legend>Per-window mixing capacity (I_w) over window index (w).</legend>')


    from collections import defaultdict as _dd
    by_method_mix = _dd(list)
    for m, metric, rel in mix_generated:
        by_method_mix[m].append((metric, str(rel)))

    for m in sorted(by_method_mix.keys()):
        html.write(f'<div class="method"><h2>{m}</h2>')
        html.write(f'<div class="sub">Mixing stride: {win_size_mix} seconds</div>')

        html.write('<div class="grid">')
        for metric, rel in sorted(by_method_mix[m], key=lambda x: x[0].lower()):
            svg_rel = rel[:-4] + ".svg"
            html.write(
                f'<div class="card"><div style="font-weight:600;margin-bottom:6px">{metric}</div>'
                f'<img src="{rel}" alt="{m} — {metric}">'
                f'<div style="margin-top:6px;font-size:0.85rem">'
                f'<a href="{rel}" download>PNG</a> • <a href="{svg_rel}" download>SVG</a>'
                f'</div></div>'
            )
        html.write('</div></div>')

    html.write(
    f'<footer>'
    f'<p>Output: <code>{OUTPUT_DIR}</code></p>'
    f'<p>Stats CSV: <code>{CSV_PATH}</code></p>'
    f'<p>Mixing Series CSV: <code>{CSV_MIXING_PATH}</code></p>'
    f'</footer></body></html>'
    )

    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(html.getvalue())

if __name__ == "__main__":
    main()
