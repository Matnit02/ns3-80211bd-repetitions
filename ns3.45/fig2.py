#!/usr/bin/env python3


import os
import re
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import matplotlib.colors as mcolors
from itertools import cycle
from scipy.stats import t
import re as _re


plt.style.use("default")

FILE_REGEX = re.compile(
    r"prr_vs_distance_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
    re.IGNORECASE,
)

CBR_REGEX = re.compile(
    r"cbr_n\d+_d(?P<density>\d+)_rep(?P<rep>\d+)_rng(?P<rng>\d+)\.csv$",
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


def extract_rng_rep(filename: str):
    m = FILE_REGEX.search(filename)
    if m:
        return int(m.group("rng")), int(m.group("rep"))
    return None, None


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


def load_cbr_mean(path: str, rep: int, cbr_mode: str) -> float | None:

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
    if cbr_mode == "normalized":
        return mean_cbr / (1 + max(0, int(rep)))
    return mean_cbr


def collect_data(root_dir: str, cbr_mode: str) -> pd.DataFrame:
    print(f"Scanning {root_dir}...")
    prr_frames = []
    cbr_rows = []

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

            for f in files:
                full_path = os.path.join(root, f)

                if f.startswith("prr_vs_distance"):
                    rng, rep = extract_rng_rep(f)
                    if rng is None:
                        continue

                    df = load_csv(full_path)
                    if df.empty:
                        continue

                    df["rng"] = rng
                    df["rep"] = rep
                    df["density"] = density
                    prr_frames.append(df)
                    continue

                if not f.startswith("cbr_"):
                    continue

                d_cbr, rng, rep = extract_cbr_meta(f)
                if rng is None:
                    continue

                net_cbr = load_cbr_mean(full_path, rep, cbr_mode)
                if net_cbr is None:
                    continue

                cbr_rows.append(
                    {
                        "density": d_cbr,
                        "rng": rng,
                        "rep": rep,
                        "net_cbr": net_cbr,
                    }
                )

    if not prr_frames:
        return pd.DataFrame()

    if not cbr_rows:
        print("No CBR files found/matched. Expected names like cbr_nX_dY_repZ_rngW.csv")
        return pd.DataFrame()

    prr_df = pd.concat(prr_frames, ignore_index=True)
    cbr_df = pd.DataFrame(cbr_rows).drop_duplicates(subset=["density", "rng", "rep"])

    merged = prr_df.merge(cbr_df, on=["density", "rng", "rep"], how="inner")
    if merged.empty:
        print("No overlapping PRR/CBR runs found after merge by (density, rep, rng).")
        return pd.DataFrame()

    return merged


def ci99(std, n):

    n = np.asarray(n)
    std = np.asarray(std)

    crit = t.ppf(0.995, df=np.maximum(n - 1, 1))
    return crit * std / np.sqrt(np.maximum(n, 1))


def plot_improvement_line(
    df: pd.DataFrame,
    target_densities,
    min_density,
    out_path: str,
    mcs_label: str = None,
    cbr_mode: str = "raw",
):

    if target_densities:
        print(f"Filtering for specific densities: {target_densities}")
        df = df[df["density"].isin(target_densities)].copy()
    else:
        print(f"Using all found densities: {sorted(df['density'].unique())}")

    if min_density is not None:
        print(f"Applying min-density filter: density >= {min_density}")
        df = df[df["density"] >= min_density].copy()


    df = df[(df["distance"] >= 50) & (df["distance"] <= 1000)]

    if df.empty:
        print("No data found after distance filtering.")
        return

    print("Calculating improvement over baseline (rep 0)...")


    per_run = (
        df.groupby(["density", "rep", "rng"])
        .agg(mean_prr=("prr", "mean"), net_cbr=("net_cbr", "mean"))
        .reset_index()
    )


    prr_pivot = per_run.pivot_table(
        index=["density", "rng"],
        columns="rep",
        values="mean_prr",
    )
    cbr_pivot = per_run.pivot_table(
        index=["density", "rng"],
        columns="rep",
        values="net_cbr",
    )

    if 0 not in prr_pivot.columns:
        print("Error: Baseline (Repetition 0) data is missing. Cannot calculate improvement.")
        return


    improvement_data = []
    for r in prr_pivot.columns:
        if r == 0:
            continue

        if r not in cbr_pivot.columns:
            continue

        temp_df = pd.concat(
            [
                (prr_pivot[r] - prr_pivot[0]).rename("improvement"),
                cbr_pivot[r].rename("net_cbr"),
            ],
            axis=1,
        ).reset_index()
        temp_df = temp_df.dropna(subset=["improvement", "net_cbr"])
        temp_df["rep"] = r
        improvement_data.append(temp_df)

    if not improvement_data:
        print("No repetitions > 0 found (no improvement data).")
        return

    final_df = pd.concat(improvement_data, ignore_index=True)

    final_df["density"] = final_df["density"].astype(int)
    final_df["rep"] = final_df["rep"].astype(int)


    stats = (
        final_df.groupby(["density", "rep"])
        .agg(
            mean=("improvement", "mean"),
            count=("improvement", "count"),
            std=("improvement", "std"),
            net_cbr=("net_cbr", "mean"),
        )
        .reset_index()
    )


    stats["std"] = stats["std"].fillna(0.0)


    stats = stats.sort_values(["rep", "net_cbr"])


    plt.figure(figsize=(9, 6))
    ax = plt.gca()

    reps = sorted(stats["rep"].unique())
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
    start_offset = 1
    colors = [
        "#482475",
        "#443983",
        "#3d4d8a",
        "#355f8d",
        "#2d708e",
        "#27808e",
        "#21918c",
        "#1fa188",
        "#2ab07f",
        "#44bf70",
        "#67cc5c",
        "#90d743",
        "#bddf26",
    ]
    rep_color_map = {
        1: "#21918c",
        2: "#44bf70",
        3: "#bddf26",
    }
    markers = cycle(markers[start_offset:] + markers[:start_offset])

    for idx, rep in enumerate(reps):
        sub = stats[stats["rep"] == rep].sort_values("net_cbr")
        color = rep_color_map.get(rep)
        if color is None:
            color = colors[min(idx + start_offset, len(colors) - 1)]

        x = sub["net_cbr"].values
        mean = sub["mean"].values
        ci = ci99(sub["std"].values, sub["count"].values)
        lower = mean - ci
        upper = mean + ci


        line, = ax.plot(
            x,
            mean,
            "-",
            marker=next(markers),
            color=color,
            label=f"{rep}",
        )


        ax.fill_between(
            x,
            lower,
            upper,
            color=color,
            alpha=0.2,
        )

    x_label = "Net CBR" if cbr_mode == "normalized" else "CBR"


    if mcs_label:
        title = (
            f"Mean PRR Improvement over Baseline (Rep 0) vs {x_label}\n"
            f"({mcs_label}, Distance Between Vehicles: 50m - 1000m)"
        )
    else:
        title = (
            f"Mean PRR Improvement over Baseline (Rep 0) vs {x_label} \n"
            "(99% Confidence Band, Distance Between Veh.: 50m - 1000m)"
        )


    ax = plt.gca()
    xmin = stats["net_cbr"].min()
    xmax = stats["net_cbr"].max()
    pad = 0.03 * max(xmax - xmin, 1e-6)
    ax.set_xlim(max(0.0, xmin - pad), min(1.0, xmax + pad))
    ax.set_ylim(bottom=0)
    plt.xlabel(x_label)
    plt.ylabel("Mean PRR Improvement ± 99% Confidence Band")
    plt.legend(title="Repetitions", loc="lower left")
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
        help="Folder containing density_X_csv subfolders (typically inside an MCS folder, e.g. .../qpsk_1_2/)",
    )
    ap.add_argument(
        "--densities",
        nargs="+",
        type=int,
        default=None,
        help="Specific densities to show (default: ALL)",
    )
    ap.add_argument(
        "--min-density",
        type=int,
        default=None,
        help="Include only densities >= this value (default: no minimum)",
    )
    ap.add_argument(
        "--out-plot",
        default="line_improvement_vs_netcbr.svg",
        help="Output filename (use .pdf, .svg, or .eps for vector graphics)",
    )
    ap.add_argument(
        "--cbr-mode",
        choices=["raw", "normalized"],
        default="normalized",
        help="Use raw CBR mean or normalized Net CBR (mean/(rep+1)) on x-axis.",
    )

    args = ap.parse_args()

    if not os.path.exists(args.root_dir):
        print("Root directory not found.")
        return


    mcs_raw = os.path.basename(os.path.normpath(args.root_dir))
    mcs_label = pretty_mcs(mcs_raw)
    print(f"Detected MCS from path: {mcs_raw} -> {mcs_label}")

    data = collect_data(args.root_dir, args.cbr_mode)
    if data.empty:
        print("No data found.")
        return

    plot_improvement_line(
        data,
        args.densities,
        args.min_density,
        args.out_plot,
        mcs_label=mcs_label,
        cbr_mode=args.cbr_mode,
    )


if __name__ == "__main__":
    main()
