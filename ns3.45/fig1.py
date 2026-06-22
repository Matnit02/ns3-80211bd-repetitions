#!/usr/bin/env python3


import os
import re
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import t
import re as _re
from itertools import cycle

FILE_REGEX = re.compile(
    r"prr_vs_distance_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
    re.IGNORECASE,
)


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


def extract_rng_rep(path: str):
    base = os.path.basename(path)
    m = FILE_REGEX.search(base)
    if m:
        return int(m.group("rng")), int(m.group("rep"))
    return None, None


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["distance", "prr", "successes", "opportunities"],
        dtype=str,
        engine="python",
    )


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
    return df[["distance", "prr"]]


def find_files(input_dir: str):
    return sorted(glob.glob(os.path.join(input_dir, "prr_vs_distance_d*_rep*_rng*.csv")))


def aggregate(files):
    frames = []
    for f in files:
        rng, rep = extract_rng_rep(f)
        if rng is None:
            continue
        df = load_csv(f)
        df["rngrun"] = rng
        df["rep"] = rep
        frames.append(df)
    if not frames:
        raise RuntimeError("No valid CSV files parsed.")
    data = pd.concat(frames, ignore_index=True)

    agg = (
        data.groupby(["rep", "distance"], as_index=False)
        .agg(
            mean_prr=("prr", "mean"),
            std_prr=("prr", "std"),
            n=("prr", "count"),
        )
        .sort_values(["rep", "distance"])
    )
    agg["std_prr"] = agg["std_prr"].fillna(0.0)
    return agg


def ci99(std, n):

    n = np.asarray(n)
    std = np.asarray(std)

    crit = t.ppf(0.995, df=np.maximum(n - 1, 1))
    return crit * std / np.sqrt(np.maximum(n, 1))


def plot_agg(agg: pd.DataFrame, out_path: str, mcs_label: str = None, density_val: int | None = None):
    plt.figure(figsize=(9, 6))


    agg_plot = agg[agg["distance"] <= 1000].copy()
    if agg_plot.empty:
        print("No data with distance <= 1000 m to plot.")
        return

    reps = sorted(agg_plot["rep"].unique())
    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.1, 0.9, max(len(reps), 1)))
    markers = [
        "o",
        "s",
        "^",
        "D",
        "v",
        ">",
        "<",
        "P",
        "X",
        "*",
        "h",
        "8",
        "p",
    ]
    markers = cycle(markers)

    for idx, rep_val in enumerate(reps):
        df_rep = agg_plot[agg_plot["rep"] == rep_val].sort_values("distance")

        mean = df_rep["mean_prr"].values
        ci = ci99(df_rep["std_prr"].values, df_rep["n"].values)
        lower = mean - ci
        upper = mean + ci

        color = colors[idx % len(colors)]
        marker = next(markers)
        line, = plt.plot(
            df_rep["distance"].values,
            mean,
            "-",
            marker=marker,
            color=color,
            label=f"{rep_val}",
        )

        plt.fill_between(
            df_rep["distance"].values,
            lower,
            upper,
            color=color,
            alpha=0.2,
            label=None,
        )


    if mcs_label and density_val is not None:
        title = (
            f"PRR vs Distance Between Vehicles\n"
            f"({mcs_label}, Vehicle Density {density_val} veh./km)"
        )
    elif mcs_label:
        title = (
            f"PRR vs Distance (99% Confidence Band)\n"
            f"{mcs_label}, distance ≤ 1000 m"
        )
    else:
        title = "PRR vs Distance (99% Confidence Band, distance ≤ 1000 m)"


    plt.xlabel("Distance Between Vehicles [m]")
    plt.ylabel("Packet Reception Ratio ± 99% Confidence Band")
    plt.grid(True, which="both", linestyle="--", alpha=0.5)
    plt.legend(title="Repetitions")
    plt.tight_layout()
    if out_path:

        plt.savefig(out_path)
    plt.show()


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input-dir",
        default="results_csv",
        help=(
            "Directory with CSV files; typically something like "
            ".../<MCS>/density_<X>_csv/"
        ),
    )
    ap.add_argument(
        "--out-plot",
        default="prr_vs_distance_plot.svg",
        help="Output plot filename (use .svg, .pdf, or .eps for vector graphics)",
    )
    ap.add_argument(
        "--out-csv",
        default="prr_vs_distance_aggregated.csv",
        help="Save aggregated results here",
    )
    args = ap.parse_args()


    input_dir_norm = os.path.normpath(args.input_dir)
    density_folder = os.path.basename(input_dir_norm)
    mcs_folder = os.path.basename(os.path.dirname(input_dir_norm))


    density_val = None
    if "density" in density_folder:
        try:
            density_val = int(
                density_folder.replace("density_", "").replace("_csv", "")
            )
        except ValueError:
            density_val = None


    mcs_label = pretty_mcs(mcs_folder)
    print(f"Detected MCS from path: {mcs_folder} -> {mcs_label}")
    if density_val is not None:
        print(f"Detected density from path: {density_folder} -> {density_val} veh./km")

    files = find_files(args.input_dir)
    if not files:
        raise SystemExit(f"No CSV files found in '{args.input_dir}'.")

    agg = aggregate(files)
    agg.to_csv(args.out_csv, index=False)
    print(f"Aggregated data saved to {args.out_csv}")

    plot_agg(agg, args.out_plot, mcs_label=mcs_label, density_val=density_val)
    print(f"Plot saved to {args.out_plot}")


if __name__ == "__main__":
    main()
