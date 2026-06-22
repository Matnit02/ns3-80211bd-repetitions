#!/usr/bin/env python3


import os
import re
import argparse
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import t
import re as _re


plt.style.use("default")

PRR_REGEX = re.compile(
    r"prr_vs_distance_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
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


def extract_prr_meta(filename: str):
    m = PRR_REGEX.search(filename)
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


def _parse_scan_task(task):
    full_path, filename, folder_density, mcs = task
    d_prr, rng, rep = extract_prr_meta(filename)
    if rng is None:
        return None


    if d_prr != folder_density:
        d_prr = folder_density

    df = load_csv(full_path)
    if df.empty:
        return None

    df["rng"] = rng
    df["rep"] = rep
    df["density"] = d_prr
    df["mcs"] = mcs
    return df


def collect_data(root_dir: str, scan_workers: int = 1) -> pd.DataFrame:
    print(f"Scanning {root_dir} with {scan_workers} worker(s)...")
    prr_frames = []
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
                    scan_tasks.append((full_path, f, density, mcs))

    if scan_workers <= 1:
        results = (_parse_scan_task(task) for task in scan_tasks)
    else:
        with ThreadPoolExecutor(max_workers=scan_workers) as pool:
            results = pool.map(_parse_scan_task, scan_tasks)

    for item in results:
        if item is None:
            continue
        prr_frames.append(item)

    if not prr_frames:
        return pd.DataFrame()

    return pd.concat(prr_frames, ignore_index=True)


def ci99(std, n):

    n = np.asarray(n)
    std = np.asarray(std)
    crit = t.ppf(0.995, df=np.maximum(n - 1, 1))
    return crit * std / np.sqrt(np.maximum(n, 1))


def plot_prr_vs_distance(df: pd.DataFrame, target_density: int, target_rep: int, out_path: str):
    df = df[(df["density"] == target_density) & (df["rep"] == target_rep)].copy()
    if df.empty:
        print(f"No data found for density={target_density}, rep={target_rep}.")
        return


    df = df[df["distance"] <= 1000].copy()

    if df.empty:
        print("No data found after distance filtering (<= 1000 m).")
        return

    print(f"Calculating PRR vs distance for density={target_density}, rep={target_rep}...")


    stats = (
        df.groupby(["mcs", "distance"])
        .agg(
            mean=("prr", "mean"),
            count=("prr", "count"),
            std=("prr", "std"),
        )
        .reset_index()
        .sort_values(["mcs", "distance"])
    )

    stats["std"] = stats["std"].fillna(0.0)

    if stats.empty:
        print("No PRR data computed.")
        return


    plt.figure(figsize=(9, 6))
    ax = plt.gca()

    unique_mcs = sorted(
        stats["mcs"].unique(),
        key=lambda m: (MCS_ORDER.get(m, 999), m)
    )

    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.1, 0.9, max(len(unique_mcs), 1)))
    markers = [
        "o",
        "s",
        "^",
        "D",
        ">",
        "*",
        "v",
        "P",
        "X",
        "1",
        "h",
        "8",
        "p",
    ]
    mcs_to_style = {
        mcs: (colors[i], markers[i % len(markers)])
        for i, mcs in enumerate(unique_mcs)
    }

    for mcs in unique_mcs:
        sub = stats[stats["mcs"] == mcs].sort_values("distance")
        if sub.empty:
            continue

        color, marker = mcs_to_style[mcs]
        x = sub["distance"].values
        mean = sub["mean"].values
        ci = ci99(sub["std"].values, sub["count"].values)
        lower = mean - ci
        upper = mean + ci

        ax.plot(x, mean, "-", marker=marker, color=color, label=pretty_mcs(mcs))

        ax.fill_between(
            x,
            lower,
            upper,
            color=color,
            alpha=0.2,
        )

    plt.xlabel("Distance Between Vehicles [m]")
    plt.ylabel("Packet Reception Ratio ± 99% Confidence Band")
    plt.legend(title="MCS", loc="best")
    plt.grid(True, which="both", axis="both", linestyle="--", alpha=0.7)


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
        "--rep",
        type=int,
        default=0,
        help="Repetition number to plot (default: 0)",
    )
    ap.add_argument(
        "--density",
        type=int,
        default=6,
        help="Vehicle density to plot (default: 6)",
    )
    ap.add_argument(
        "--out-plot",
        default="prr_vs_distance_all_mcs_rep0_density6.svg",
        help="Output filename (use .pdf, .svg, or .eps for vector graphics)",
    )
    ap.add_argument(
        "--scan-workers",
        type=int,
        default=1,
        help="Number of workers used to scan/parse CSV files (0 = auto, 1 = single-threaded).",
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

    plot_prr_vs_distance(data, args.density, args.rep, args.out_plot)


if __name__ == "__main__":
    main()
