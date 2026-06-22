#!/usr/bin/env python3


import os
import re
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import t
import re as _re

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


def _viridis_palette(count: int):
    if count <= 0:
        return []
    if count == 1:
        return [plt.get_cmap("viridis")(0.6)]
    cmap = plt.get_cmap("viridis")
    return [cmap(v) for v in np.linspace(0.1, 0.9, count)]


def _marker_cycle(count: int):
    markers = ["o", "s", "^", "*", "+", "x", "1", "2", "3", "4", "8", "h", "H", "d", "p", "|", "_"]
    return [markers[i % len(markers)] for i in range(count)]


def plot_agg(
    agg: pd.DataFrame,
    out_path: str,
    mcs_label: str = None,
    density_val: int | None = None,
):
    plt.figure(figsize=(9, 6))


    agg_plot = agg[agg["distance"] <= 1000].copy()
    if agg_plot.empty:
        print("No data with distance <= 1000 m to plot.")
        return

    reps = sorted(agg_plot["rep"].unique())
    rep_colors = {rep: color for rep, color in zip(reps, _viridis_palette(len(reps)))}
    rep_markers = {rep: marker for rep, marker in zip(reps, _marker_cycle(len(reps)))}

    for rep_val in reps:
        df_rep = agg_plot[agg_plot["rep"] == rep_val].sort_values("distance")

        mean = df_rep["mean_prr"].values
        ci = ci99(df_rep["std_prr"].values, df_rep["n"].values)
        lower = mean - ci
        upper = mean + ci
        color = rep_colors.get(rep_val)
        marker = rep_markers.get(rep_val)

        plt.plot(
            df_rep["distance"].values,
            mean,
            linestyle="-",
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
    plt.ylabel("Packet Reception Ratio ± 99% CB")
    plt.grid(True, which="both", linestyle="--", alpha=0.5)
    plt.legend(title="Repetitions")
    plt.tight_layout()
    if out_path:

        plt.savefig(out_path)
    plt.show()


def plot_agg_dual(
    no_ldpc: pd.DataFrame,
    ldpc: pd.DataFrame,
    out_path: str,
    mcs_label: str = None,
    density_val: int | None = None,
    label_no_ldpc: str = "802.11p",
    label_ldpc: str = "802.11bd",
):
    plt.figure(figsize=(9, 6))


    no_ldpc_plot = no_ldpc[no_ldpc["distance"] <= 1000].copy()
    ldpc_plot = ldpc[ldpc["distance"] <= 1000].copy()
    if no_ldpc_plot.empty and ldpc_plot.empty:
        print("No data with distance <= 1000 m to plot.")
        return

    reps = sorted(set(no_ldpc_plot["rep"].unique()).union(ldpc_plot["rep"].unique()))
    rep_colors = {rep: color for rep, color in zip(reps, _viridis_palette(len(reps)))}
    rep_markers = {rep: marker for rep, marker in zip(reps, _marker_cycle(len(reps)))}

    for rep_val in reps:
        color = rep_colors[rep_val]
        marker = rep_markers[rep_val]

        df_rep_no = no_ldpc_plot[no_ldpc_plot["rep"] == rep_val].sort_values("distance")
        if not df_rep_no.empty:
            mean = df_rep_no["mean_prr"].values
            ci = ci99(df_rep_no["std_prr"].values, df_rep_no["n"].values)
            plt.plot(
                df_rep_no["distance"].values,
                mean,
                linestyle="-",
                marker=marker,
                color=color,
                label=f"Rep {rep_val} ({label_no_ldpc})",
            )
            plt.fill_between(
                df_rep_no["distance"].values,
                mean - ci,
                mean + ci,
                color=color,
                alpha=0.12,
            )

        df_rep_ldpc = ldpc_plot[ldpc_plot["rep"] == rep_val].sort_values("distance")
        if not df_rep_ldpc.empty:
            mean = df_rep_ldpc["mean_prr"].values
            ci = ci99(df_rep_ldpc["std_prr"].values, df_rep_ldpc["n"].values)
            plt.plot(
                df_rep_ldpc["distance"].values,
                mean,
                linestyle="--",
                marker=marker,
                color=color,
                label=f"Rep {rep_val} ({label_ldpc})",
            )
            plt.fill_between(
                df_rep_ldpc["distance"].values,
                mean - ci,
                mean + ci,
                color=color,
                alpha=0.2,
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
    plt.ylabel("Packet Reception Ratio ± 99% CB")
    plt.grid(True, which="both", linestyle="--", alpha=0.5)
    plt.legend(title="Repetitions")
    plt.tight_layout()
    if out_path:

        plt.savefig(out_path)
    plt.show()


def _infer_mcs_density(input_dir: str):
    input_dir_norm = os.path.normpath(input_dir)
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
    return mcs_folder, mcs_label, density_val, density_folder


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input-dir",
        default=None,
        help=(
            "Single-directory mode: directory with CSV files; typically something like "
            ".../<MCS>/density_<X>_csv/"
        ),
    )
    ap.add_argument(
        "--ldpc-input-dir",
        default=None,
        help=(
            "802.11bd/LDPC directory with CSV files; typically something like "
            ".../<MCS>/density_<X>_csv/"
        ),
    )
    ap.add_argument(
        "--no-ldpc-input-dir",
        default=None,
        help=(
            "802.11p/No-LDPC directory with CSV files; typically something like "
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
        help="Save aggregated results here (single-directory mode only)",
    )
    ap.add_argument(
        "--out-csv-ldpc",
        default="prr_vs_distance_aggregated_ldpc.csv",
        help="Save aggregated LDPC results here (dual-directory mode)",
    )
    ap.add_argument(
        "--out-csv-no-ldpc",
        default="prr_vs_distance_aggregated_no_ldpc.csv",
        help="Save aggregated No-LDPC results here (dual-directory mode)",
    )
    args = ap.parse_args()

    dual_mode = bool(args.ldpc_input_dir or args.no_ldpc_input_dir)
    if dual_mode:
        if not args.ldpc_input_dir or not args.no_ldpc_input_dir:
            raise SystemExit("Both --ldpc-input-dir and --no-ldpc-input-dir are required.")

        mcs_folder_ldpc, mcs_label_ldpc, density_val_ldpc, density_folder_ldpc = _infer_mcs_density(
            args.ldpc_input_dir
        )
        mcs_folder_no, mcs_label_no, density_val_no, density_folder_no = _infer_mcs_density(
            args.no_ldpc_input_dir
        )

        if mcs_folder_ldpc != mcs_folder_no:
            print(
                f"Warning: MCS folders differ: {mcs_folder_ldpc} vs {mcs_folder_no}"
            )
        if density_val_ldpc != density_val_no:
            print(
                f"Warning: density folders differ: {density_folder_ldpc} vs {density_folder_no}"
            )

        mcs_label = mcs_label_ldpc if mcs_label_ldpc else mcs_label_no
        density_val = density_val_ldpc if density_val_ldpc is not None else density_val_no

        print(
            f"Detected MCS from LDPC path: {mcs_folder_ldpc} -> {mcs_label_ldpc}"
        )
        if density_val_ldpc is not None:
            print(
                f"Detected density from LDPC path: {density_folder_ldpc} -> {density_val_ldpc} veh./km"
            )
        print(
            f"Detected MCS from No-LDPC path: {mcs_folder_no} -> {mcs_label_no}"
        )
        if density_val_no is not None:
            print(
                f"Detected density from No-LDPC path: {density_folder_no} -> {density_val_no} veh./km"
            )

        files_ldpc = find_files(args.ldpc_input_dir)
        files_no_ldpc = find_files(args.no_ldpc_input_dir)
        if not files_ldpc:
            raise SystemExit(f"No CSV files found in '{args.ldpc_input_dir}'.")
        if not files_no_ldpc:
            raise SystemExit(f"No CSV files found in '{args.no_ldpc_input_dir}'.")

        agg_ldpc = aggregate(files_ldpc)
        agg_no_ldpc = aggregate(files_no_ldpc)

        agg_ldpc.to_csv(args.out_csv_ldpc, index=False)
        agg_no_ldpc.to_csv(args.out_csv_no_ldpc, index=False)
        print(f"Aggregated LDPC data saved to {args.out_csv_ldpc}")
        print(f"Aggregated No-LDPC data saved to {args.out_csv_no_ldpc}")

        plot_agg_dual(
            agg_no_ldpc,
            agg_ldpc,
            args.out_plot,
            mcs_label=mcs_label,
            density_val=density_val,
        )
        print(f"Plot saved to {args.out_plot}")
        return

    if not args.input_dir:
        raise SystemExit("Provide --input-dir for single-directory mode.")


    mcs_folder, mcs_label, density_val, density_folder = _infer_mcs_density(args.input_dir)
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
