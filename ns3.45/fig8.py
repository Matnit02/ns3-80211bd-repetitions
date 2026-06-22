#!/usr/bin/env python3


import os
import re
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
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
    m = FILE_REGEX.search(filename)
    if m:
        return int(m.group("density")), int(m.group("rng")), int(m.group("rep"))
    return None, None, None


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


def _filter_data(
    df: pd.DataFrame,
    target_densities,
    min_density,
) -> pd.DataFrame:
    if target_densities:
        df = df[df["density"].isin(target_densities)].copy()

    if min_density is not None:
        df = df[df["density"] >= min_density].copy()


    return df[(df["distance"] >= 50) & (df["distance"] <= 1000)].copy()


def _compute_improvement_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()


    per_run = (
        df.groupby(["density", "rep", "rng"])
        .agg(mean_prr=("prr", "mean"), net_cbr=("net_cbr", "mean"))
        .reset_index()
    )


    stats = (
        per_run.groupby(["density", "rep"])
        .agg(
            mean_prr=("mean_prr", "mean"),
            count=("mean_prr", "count"),
            std=("mean_prr", "std"),
            net_cbr=("net_cbr", "mean"),
        )
        .reset_index()
    )


    stats["std"] = stats["std"].fillna(0.0)


    return stats.sort_values(["rep", "net_cbr"])


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
    else:
        print(f"Using all found densities: {sorted(df['density'].unique())}")

    if min_density is not None:
        print(f"Applying min-density filter: density >= {min_density}")

    df = _filter_data(df, target_densities, min_density)
    if df.empty:
        print("No data found after distance filtering.")
        return

    print("Calculating mean PRR per repetition...")
    stats = _compute_improvement_stats(df)
    if stats.empty:
        print("No repetition data found after filtering.")
        return

    reps = sorted(stats["rep"].unique())
    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.1, 0.9, max(len(reps), 1)))
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
    rep_to_style = {
        rep: (colors[i], markers[i % len(markers)])
        for i, rep in enumerate(reps)
    }


    plt.figure(figsize=(9, 6))
    ax = plt.gca()

    for rep in reps:
        sub = stats[stats["rep"] == rep].sort_values("net_cbr")
        color, marker = rep_to_style[rep]

        x = sub["net_cbr"].values
        mean = sub["mean_prr"].values
        ci = ci99(sub["std"].values, sub["count"].values)
        lower = mean - ci
        upper = mean + ci


        ax.plot(x, mean, "-", marker=marker, color=color, label=f"{rep}")


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
            f"Mean PRR vs {x_label}\n"
            f"({mcs_label}, Distance Between Vehicles: 50m - 1000m)"
        )
    else:
        title = (
            f"Mean PRR vs {x_label} \n"
            "(99% Confidence Band, Distance Between Veh.: 50m - 1000m)"
        )


    xmin = stats["net_cbr"].min()
    xmax = stats["net_cbr"].max()
    pad = 0.03 * max(xmax - xmin, 1e-6)
    ax.set_xlim(max(0.0, xmin - pad), min(1.0, xmax + pad))
    ax.set_ylim(0.3, 0.55)
    plt.xlabel(x_label)
    plt.ylabel("Mean PRR ± 99% Confidence Band")
    plt.legend(title="Repetitions")
    plt.grid(True, which="both", axis="both", linestyle="--", alpha=0.7)

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    print(f"Plot saved to {out_path}")
    plt.show()


def plot_improvement_line_dual(
    ldpc_df: pd.DataFrame,
    no_ldpc_df: pd.DataFrame,
    target_densities,
    min_density,
    out_path: str,
    mcs_label: str = None,
    cbr_mode: str = "raw",
    label_no_ldpc: str = "802.11p",
    label_ldpc: str = "802.11bd",
):
    if target_densities:
        print(f"Filtering for specific densities: {target_densities}")
    else:
        print(f"Using all found densities: {sorted(no_ldpc_df['density'].unique())}")

    if min_density is not None:
        print(f"Applying min-density filter: density >= {min_density}")

    no_ldpc_df = _filter_data(no_ldpc_df, target_densities, min_density)
    ldpc_df = _filter_data(ldpc_df, target_densities, min_density)

    if no_ldpc_df.empty or ldpc_df.empty:
        print("No data found after distance filtering.")
        return

    print("Calculating mean PRR per repetition...")
    no_ldpc_stats = _compute_improvement_stats(no_ldpc_df)
    ldpc_stats = _compute_improvement_stats(ldpc_df)
    if no_ldpc_stats.empty or ldpc_stats.empty:
        print("No repetition data found after filtering.")
        return

    reps = sorted(set(no_ldpc_stats["rep"].unique()).union(ldpc_stats["rep"].unique()))
    if not reps:
        print("No repetition data found.")
        return

    cmap = plt.get_cmap("viridis")
    colors = cmap(np.linspace(0.1, 0.9, max(len(reps), 1)))
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
    rep_to_style = {
        rep: (colors[i], markers[i % len(markers)])
        for i, rep in enumerate(reps)
    }


    plt.figure(figsize=(9, 6))
    ax = plt.gca()

    for rep in reps:
        color = rep_to_style[rep]

        sub_no = no_ldpc_stats[no_ldpc_stats["rep"] == rep].sort_values("net_cbr")
        if not sub_no.empty:
            x = sub_no["net_cbr"].values
            mean = sub_no["mean_prr"].values
            ci = ci99(sub_no["std"].values, sub_no["count"].values)
            ax.plot(
                x,
                mean,
                "-",
                marker=color[1],
                color=color[0],
                label=f"{rep} ({label_no_ldpc})",
            )
            ax.fill_between(
                x,
                mean - ci,
                mean + ci,
                color=color[0],
                alpha=0.12,
            )

        sub_ldpc = ldpc_stats[ldpc_stats["rep"] == rep].sort_values("net_cbr")
        if not sub_ldpc.empty:
            x = sub_ldpc["net_cbr"].values
            mean = sub_ldpc["mean_prr"].values
            ci = ci99(sub_ldpc["std"].values, sub_ldpc["count"].values)
            ax.plot(
                x,
                mean,
                "--",
                marker=color[1],
                color=color[0],
                label=f"{rep} ({label_ldpc})",
            )
            ax.fill_between(
                x,
                mean - ci,
                mean + ci,
                color=color[0],
                alpha=0.18,
            )

    x_label = "Net CBR" if cbr_mode == "normalized" else "CBR"


    if mcs_label:
        title = (
            f"Mean PRR vs {x_label}\n"
            f"({mcs_label}, Distance Between Vehicles: 50m - 1000m)"
        )
    else:
        title = (
            f"Mean PRR vs {x_label} \n"
            "(99% Confidence Band, Distance Between Veh.: 50m - 1000m)"
        )


    xmin = min(no_ldpc_stats["net_cbr"].min(), ldpc_stats["net_cbr"].min())
    xmax = max(no_ldpc_stats["net_cbr"].max(), ldpc_stats["net_cbr"].max())
    pad = 0.03 * max(xmax - xmin, 1e-6)
    ax.set_xlim(max(0.0, xmin - pad), min(1.0, xmax + pad))
    ax.set_ylim(0.3, 0.55)
    plt.xlabel(x_label)
    plt.ylabel("Mean PRR ± 99% Confidence Band")
    plt.legend(title="Repetitions")
    plt.grid(True, which="both", axis="both", linestyle="--", alpha=0.7)

    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    print(f"Plot saved to {out_path}")
    plt.show()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root-dir",
        required=False,
        help="Single-mode root folder containing density_X_csv subfolders (e.g. .../qpsk_1_2/)",
    )
    ap.add_argument(
        "--ldpc-root-dir",
        required=False,
        help="LDPC/802.11bd root folder containing density_X_csv subfolders",
    )
    ap.add_argument(
        "--no-ldpc-root-dir",
        required=False,
        help="No-LDPC/802.11p root folder containing density_X_csv subfolders",
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

    dual_mode = bool(args.ldpc_root_dir or args.no_ldpc_root_dir)
    if dual_mode:
        if not args.ldpc_root_dir or not args.no_ldpc_root_dir:
            print("Both --ldpc-root-dir and --no-ldpc-root-dir are required in dual mode.")
            return

        if not os.path.exists(args.ldpc_root_dir):
            print("LDPC root directory not found.")
            return
        if not os.path.exists(args.no_ldpc_root_dir):
            print("No-LDPC root directory not found.")
            return

        mcs_raw_ldpc = os.path.basename(os.path.normpath(args.ldpc_root_dir))
        mcs_raw_no = os.path.basename(os.path.normpath(args.no_ldpc_root_dir))
        if mcs_raw_ldpc != mcs_raw_no:
            print(f"Warning: MCS folders differ: {mcs_raw_ldpc} vs {mcs_raw_no}")

        mcs_label = pretty_mcs(mcs_raw_ldpc)
        print(f"Detected MCS from LDPC path: {mcs_raw_ldpc} -> {mcs_label}")
        if mcs_raw_ldpc != mcs_raw_no:
            print(f"Detected MCS from No-LDPC path: {mcs_raw_no} -> {pretty_mcs(mcs_raw_no)}")

        ldpc_data = collect_data(args.ldpc_root_dir, args.cbr_mode)
        no_ldpc_data = collect_data(args.no_ldpc_root_dir, args.cbr_mode)
        if ldpc_data.empty:
            print("No LDPC data found.")
            return
        if no_ldpc_data.empty:
            print("No No-LDPC data found.")
            return

        plot_improvement_line_dual(
            ldpc_data,
            no_ldpc_data,
            args.densities,
            args.min_density,
            args.out_plot,
            mcs_label=mcs_label,
            cbr_mode=args.cbr_mode,
        )
        return

    if not args.root_dir:
        print("Provide --root-dir for single-mode plotting.")
        return

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
