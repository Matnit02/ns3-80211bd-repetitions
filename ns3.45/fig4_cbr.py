#!/usr/bin/env python3


import os
import re
import argparse
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import matplotlib.colors as mcolors
from scipy.stats import t
import re as _re


plt.style.use("default")

PRR_REGEX = re.compile(
    r"prr_vs_distance_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
    re.IGNORECASE,
)
PRR_REGEX_LEGACY = re.compile(r"prr_vs_distance_(?P<rng>\d+)_(?P<rep>\d+)\.csv$", re.IGNORECASE)
CBR_REGEX = re.compile(
    r"cbr_n\d+_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
    re.IGNORECASE,
)


MCS_ORDER = {
    "bpsk_1_2":   1,
    "bpsk_3_4":   2,
    "qpsk_1_2":   3,
    "qpsk_3_4":   4,
    "16qam_1_2":  5,
    "16qam_3_4":  6,
    "64qam_2_3":  7,
    "64qam_3_4":  8,
    "64qam_5_6":  9,
    "256qam_3_4": 10,
}


def pretty_mcs(mcs: str) -> str:


    parts = mcs.split("_")
    if len(parts) < 3:
        return mcs

    mod_raw = parts[0]
    num = parts[1]
    den = parts[2]

    m = _re.match(r"(\d+)(qam)", mod_raw, flags=_re.IGNORECASE)
    if m:
        order, mod = m.groups()
        mod_label = f"{order}-QAM"
    else:
        mod_label = mod_raw.upper()

    return f"{mod_label} {num}/{den}"


def extract_prr_meta(filename: str, folder_density: int):
    m = PRR_REGEX.search(filename)
    if m:
        return int(m.group("density")), int(m.group("rng")), int(m.group("rep"))

    m = PRR_REGEX_LEGACY.search(filename)
    if m:
        return folder_density, int(m.group("rng")), int(m.group("rep"))

    return None, None, None


def extract_cbr_meta(filename: str):
    m = CBR_REGEX.search(filename)
    if m:
        return int(m.group("density")), int(m.group("rng")), int(m.group("rep"))
    return None, None, None


def load_csv(path: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(
            path,
            sep=";",
            header=None,
            names=["distance", "prr", "s", "o"],
            dtype=str,
            engine="python",
        )
    except Exception:
        return pd.DataFrame()


    mask = (
            df["distance"].str.lower().eq("distance")
            | df["prr"].str.lower().eq("prr")
    )
    df = df.loc[~mask].copy()


    for col in ["distance", "prr"]:
        df[col] = (
            df[col]
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["distance", "prr"])
    df["distance"] = df["distance"].round(1)

    return df[["distance", "prr"]]


def load_cbr_mean(path: str, rep: int) -> float | None:

    try:
        df = pd.read_csv(path, sep=";", dtype=str, engine="python")
    except Exception:
        return None

    if df.empty:
        return None

    df.columns = [c.strip().lower() for c in df.columns]
    if "cbr" not in df.columns:
        return None

    cbr = (
        df["cbr"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.replace(" ", "", regex=False)
    )
    cbr = pd.to_numeric(cbr, errors="coerce").dropna()
    if cbr.empty:
        return None

    mean_cbr = float(cbr.mean())
    return mean_cbr / (1 + max(0, int(rep)))


def _parse_scan_task(task):
    kind, full_path, filename, density, mcs = task

    if kind == "prr":
        d_prr, rng, rep = extract_prr_meta(filename, density)
        if rng is None:
            return None

        df = load_csv(full_path)
        if df.empty:
            return None

        df["rng"] = rng
        df["rep"] = rep
        df["density"] = d_prr
        df["mcs"] = mcs
        return ("prr", df)

    d_cbr, rng, rep = extract_cbr_meta(filename)
    if rng is None:
        return None

    net_cbr = load_cbr_mean(full_path, rep)
    if net_cbr is None:
        return None

    return (
        "cbr",
        {
            "mcs": mcs,
            "density": d_cbr,
            "rng": rng,
            "rep": rep,
            "net_cbr": net_cbr,
        },
    )


def collect_data(root_dir: str, scan_workers: int = 1) -> pd.DataFrame:
    print(f"Scanning {root_dir} with {scan_workers} worker(s)...")
    prr_frames = []
    cbr_rows = []
    scan_tasks = []

    for root, dirs, files in os.walk(root_dir):
        folder_name = os.path.basename(root)
        if "density" in folder_name and "csv" in folder_name:
            try:
                parts = (
                    folder_name.replace("density_", "")
                    .replace("_csv", "")
                )
                density = int(parts)
            except ValueError:
                continue


            mcs = os.path.basename(os.path.dirname(root))

            for f in files:
                full_path = os.path.join(root, f)
                if f.startswith("prr_vs_distance"):
                    scan_tasks.append(("prr", full_path, f, density, mcs))
                    continue

                if not f.startswith("cbr_"):
                    continue

                scan_tasks.append(("cbr", full_path, f, density, mcs))

    if scan_workers <= 1:
        results = (_parse_scan_task(task) for task in scan_tasks)
    else:
        with ThreadPoolExecutor(max_workers=scan_workers) as pool:
            results = pool.map(_parse_scan_task, scan_tasks)

    for item in results:
        if item is None:
            continue
        kind, payload = item
        if kind == "prr":
            prr_frames.append(payload)
        else:
            cbr_rows.append(payload)

    if not prr_frames:
        return pd.DataFrame()

    if not cbr_rows:
        print("No CBR files found/matched. Expected names like cbr_nX_dY_repZ_rngW.csv")
        return pd.DataFrame()

    prr_df = pd.concat(prr_frames, ignore_index=True)
    cbr_df = pd.DataFrame(cbr_rows).drop_duplicates(subset=["mcs", "density", "rng", "rep"])

    merged = prr_df.merge(cbr_df, on=["mcs", "density", "rng", "rep"], how="inner")
    if merged.empty:
        print("No overlapping PRR/CBR runs found after merge by (mcs, density, rep, rng).")
        return pd.DataFrame()

    return merged


def ci99(std, n):

    n = np.asarray(n)
    std = np.asarray(std)
    crit = t.ppf(0.995, df=np.maximum(n - 1, 1))
    return crit * std / np.sqrt(np.maximum(n, 1))


def plot_improvement_line(df: pd.DataFrame, target_densities, out_path: str):

    if target_densities:
        print(f"Filtering for specific densities: {target_densities}")
        df = df[df["density"].isin(target_densities)].copy()
    else:
        print(f"Using all found densities: {sorted(df['density'].unique())}")


    df = df[(df["distance"] >= 50) & (df["distance"] <= 1000)]

    if df.empty:
        print("No data found after distance filtering.")
        return

    print("Calculating gain between rep 1 and rep 3...")


    per_run = (
        df.groupby(["mcs", "density", "rep", "rng"])
        .agg(mean_prr=("prr", "mean"), net_cbr=("net_cbr", "mean"))
        .reset_index()
    )


    prr_pivot = per_run.pivot_table(
        index=["mcs", "density", "rng"],
        columns="rep",
        values="mean_prr",
    )
    cbr_pivot = per_run.pivot_table(
        index=["mcs", "density", "rng"],
        columns="rep",
        values="net_cbr",
    )

    missing_reps = [r for r in (1, 3) if r not in prr_pivot.columns]
    if missing_reps:
        print(f"Error: missing repetitions {missing_reps} (need reps 1 and 3 to compute gain).")
        return

    missing_cbr_reps = [r for r in (1, 3) if r not in cbr_pivot.columns]
    if missing_cbr_reps:
        print(f"Error: missing CBR repetitions {missing_cbr_reps} (need reps 1 and 3 to place points on x-axis).")
        return


    gain_df = pd.concat(
        [
            (prr_pivot[3] - prr_pivot[1]).rename("gain"),
            cbr_pivot[3].rename("net_cbr"),
        ],
        axis=1,
    ).reset_index()
    gain_df = gain_df.dropna(subset=["gain", "net_cbr"])

    gain_df["density"] = gain_df["density"].astype(int)
    gain_df["mcs"] = gain_df["mcs"].astype(str)


    stats = (
        gain_df.groupby(["mcs", "density"])
        .agg(
            mean=("gain", "mean"),
            count=("gain", "count"),
            std=("gain", "std"),
            net_cbr=("net_cbr", "mean"),
        )
        .reset_index()
    )

    stats["std"] = stats["std"].fillna(0.0)
    stats = stats.sort_values(["mcs", "net_cbr"])

    if stats.empty:
        print("No gain data computed.")
        return


    plt.figure(figsize=(9, 6))
    ax = plt.gca()

    base_colors = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])

    color_cycle = []
    for c in base_colors:
        hex_c = mcolors.to_hex(c).lower()
        if hex_c in ("#bcbd22", "#ffff00"):
            continue
        color_cycle.append(c)

    if not color_cycle:
        color_cycle = base_colors


    unique_mcs = sorted(
        stats["mcs"].unique(),
        key=lambda m: (MCS_ORDER.get(m, 999), m)
    )

    mcs_to_color = {
        mcs: color_cycle[i % len(color_cycle)] for i, mcs in enumerate(unique_mcs)
    }


    if "256qam_3_4" in mcs_to_color:
        mcs_to_color["256qam_3_4"] = "black"

    for mcs in unique_mcs:
        sub = stats[stats["mcs"] == mcs].sort_values("net_cbr")
        if sub.empty:
            continue

        color = mcs_to_color[mcs]
        x = sub["net_cbr"].values
        mean = sub["mean"].values
        ci = ci99(sub["std"].values, sub["count"].values)
        lower = mean - ci
        upper = mean + ci

        label = pretty_mcs(mcs)

        ax.plot(
            x,
            mean,
            "-o",
            color=color,
            label=label,
        )

        ax.fill_between(
            x,
            lower,
            upper,
            color=color,
            alpha=0.2,
        )


    ax = plt.gca()
    xmin = float(stats["net_cbr"].min())
    xmax = float(stats["net_cbr"].max())
    spread = max(xmax - xmin, 1e-6)
    pad = 0.05 * spread
    ax.set_xlim(max(0.0, xmin - pad), min(1.0, xmax + pad))
    plt.xlabel("Net CBR")
    plt.ylabel("Mean PRR Gain (Rep 3 − Rep 1)")
    ax.legend(
        title="MCS",
        loc="lower left",
        bbox_to_anchor=(0.0, 0.0),
        borderaxespad=0.0,
    )
    plt.grid(True, which="both", axis="both", linestyle="--", alpha=0.7)
    plt.axhline(0, color="black", linewidth=1)

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    print(f"Plot saved to {out_path}")
    plt.show()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root-dir",
        required=True,
        help="Folder containing MCS subfolders with density_X_csv subfolders",
    )
    ap.add_argument(
        "--densities",
        nargs="+",
        type=int,
        default=None,
        help="Specific densities to show (default: ALL)",
    )
    ap.add_argument(
        "--out-plot",
        default="line_improvement_vs_netcbr.pdf",
        help="Output filename (use .pdf, .svg, or .eps for vector graphics)",
    )
    ap.add_argument(
        "--scan-workers",
        type=int,
        default=1,
        help="Number of workers for PRR/CBR file scanning (0 = auto, 1 = single-threaded).",
    )

    args = ap.parse_args()

    if not os.path.exists(args.root_dir):
        print("Root directory not found.")
        return

    scan_workers = args.scan_workers
    if scan_workers <= 0:
        scan_workers = min(32, (os.cpu_count() or 1) + 4)

    data = collect_data(args.root_dir, scan_workers=scan_workers)
    if data.empty:
        print("No data found.")
        return

    plot_improvement_line(data, args.densities, args.out_plot)


if __name__ == "__main__":
    main()
