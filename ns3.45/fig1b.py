#!/usr/bin/env python3

import os, re, glob, numpy as np, pandas as pd, matplotlib.pyplot as plt
from scipy.stats import t
from itertools import cycle

PRR_RE = re.compile(r"prr_vs_distance_d(\d+)_rep(\d+)_rng(\d+)\.csv$", re.IGNORECASE)
CBR_RE = re.compile(r"cbr_n\d+_d(\d+)_rep(\d+)_rng(\d+)\.csv$", re.IGNORECASE)


def load_prr(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", header=None,
                     names=["distance", "prr", "successes", "opportunities"],
                     dtype=str, engine="python")
    mask = df["distance"].str.lower().eq("distance") | df["prr"].str.lower().eq("prr")
    df = df.loc[~mask].copy()
    for col in ["distance", "prr"]:
        df[col] = (df[col].str.replace(",", ".", regex=False)
                   .str.replace(" ", "", regex=False))
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["distance", "prr"])
    return df[["distance", "prr"]].sort_values("distance").reset_index(drop=True)

def load_cbr(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", header=0, dtype=str, engine="python")
    df.columns = [c.strip().lower().replace("_s", "") for c in df.columns]
    if "time" not in df.columns or "cbr" not in df.columns:
        df = df[~df.iloc[:, 0].str.lower().str.startswith("time")].copy()
        df.columns = [c.strip().lower().replace("_s", "") for c in df.columns]
    for col in ["time", "cbr"]:
        if col in df.columns:
            df[col] = (df[col].str.replace(",", ".", regex=False)
                       .str.replace(" ", "", regex=False))
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["cbr"]).rename(columns={"cbr": "CBR"})
    return df[["time", "CBR"]].sort_values("time").reset_index(drop=True)


def range_at_prr90(prr_df: pd.DataFrame, target=0.6) -> float:

    d = prr_df["distance"].to_numpy()
    p = prr_df["prr"].to_numpy()
    if d.size < 2:
        return np.nan
    order = np.argsort(d); d, p = d[order], p[order]
    above = np.where(p >= target)[0]
    if above.size == 0:
        return 0.0
    i = above[-1]
    if i == len(p) - 1:
        return float(d[-1])
    x0, x1, y0, y1 = d[i], d[i+1], p[i], p[i+1]
    if y1 == y0:
        return float(x0)
    t_ = (target - y0) / (y1 - y0)
    return float(x0 + t_ * (x1 - x0))

def net_cbr_from_series(cbr_df: pd.DataFrame, rep: int) -> float:
    mean_cbr = float(cbr_df["CBR"].mean())
    return mean_cbr / (1 + max(0, int(rep)))

def ci95_from_samples(vals: np.ndarray) -> float:
    vals = np.asarray(vals, dtype=float)
    vals = vals[~np.isnan(vals)]
    n = len(vals)
    if n <= 1:
        return 0.0
    s = np.std(vals, ddof=1)
    crit = t.ppf(0.975, df=n-1)
    return float(crit * s / np.sqrt(n))


def collect_files(input_dir: str):

    prr_files = glob.glob(os.path.join(input_dir, "**", "prr_vs_distance_d*_rep*_rng*.csv"),
                          recursive=True)
    cbr_files = glob.glob(os.path.join(input_dir, "**", "cbr_n*_d*_rep*_rng*.csv"),
                          recursive=True)

    prr_idx = {}
    for f in prr_files:
        m = PRR_RE.search(os.path.basename(f))
        if m:
            dens, rep, rng = map(int, m.groups())
            prr_idx.setdefault((dens, rep, rng), []).append(f)

    cbr_idx = {}
    for f in cbr_files:
        m = CBR_RE.search(os.path.basename(f))
        if m:
            dens, rep, rng = map(int, m.groups())
            cbr_idx.setdefault((dens, rep, rng), []).append(f)


    keys = sorted(set(prr_idx).intersection(cbr_idx))
    if not keys:
        raise SystemExit(f"No matched PRR/CBR pairs in '{input_dir}'.")
    return {k: (prr_idx[k], cbr_idx[k]) for k in keys}


def mean_prr_curve(prr_paths: list[str]) -> pd.DataFrame:

    curves = [load_prr(p) for p in prr_paths if os.path.getsize(p) > 0]
    curves = [c for c in curves if not c.empty]
    if not curves:
        return pd.DataFrame(columns=["distance", "prr"])


    grid = np.unique(np.concatenate([c["distance"].to_numpy() for c in curves]))
    grid.sort()

    prrs = []
    for c in curves:
        x = c["distance"].to_numpy(); y = c["prr"].to_numpy()

        order = np.argsort(x); x, y = x[order], y[order]
        prrs.append(np.interp(grid, x, y, left=y[0], right=y[-1]))
    prrs = np.vstack(prrs)
    mean_prr = np.nanmean(prrs, axis=0)
    return pd.DataFrame({"distance": grid, "prr": mean_prr})

def build_dataset_like_matlab(input_dir: str, target: float = 0.9, min_density: int | None = None) -> pd.DataFrame:
    bag = collect_files(input_dir)


    grouped: dict[tuple[int,int], tuple[list[str], list[str]]] = {}
    for (dens, rep, rng), (prr_paths, cbr_paths) in bag.items():
        key = (dens, rep)
        prr_list, cbr_list = grouped.setdefault(key, ([], []))
        prr_list.extend(prr_paths)
        cbr_list.extend(cbr_paths)

    rows = []
    for (dens, rep), (prr_paths, cbr_paths) in grouped.items():
        if min_density is not None and dens < min_density:
            continue

        mean_curve = mean_prr_curve(prr_paths)
        rng_mean = range_at_prr90(mean_curve, target=target)

        per_ranges = [range_at_prr90(load_prr(p), target=target) for p in prr_paths]
        ci_range = ci95_from_samples(np.array(per_ranges, dtype=float))

        per_netcbr = [net_cbr_from_series(load_cbr(c), rep) for c in cbr_paths]
        mean_netcbr = float(np.mean(per_netcbr)) if per_netcbr else np.nan
        ci_netcbr = ci95_from_samples(np.array(per_netcbr, dtype=float))

        rows.append({
            "density": dens,
            "rep": rep,
            "mean_range": rng_mean,
            "ci_range_95": ci_range,
            "mean_netcbr": mean_netcbr,
            "ci_netcbr_95": ci_netcbr,
            "n_repeats": max(len(prr_paths), len(cbr_paths)),
        })

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["mean_range", "mean_netcbr"]).sort_values(["rep", "density"])
    return df


def plot_fig4(agg: pd.DataFrame, out_path: str, prr_target: float = 0.9):
    plt.figure(figsize=(9, 6))
    reps = sorted(agg["rep"].unique())
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
    for idx, rep in enumerate(reps):
        dfrep = agg[agg["rep"] == rep]
        dfrep = dfrep.sort_values("mean_netcbr")
        x, y = dfrep["mean_netcbr"].values, dfrep["mean_range"].values
        xerr, yerr = dfrep["ci_netcbr_95"].values, dfrep["ci_range_95"].values
        color = colors[idx % len(colors)]
        marker = next(markers)
        (ln,) = plt.plot(x, y, "-", marker=marker, color=color, label=rep)
        plt.errorbar(x, y, xerr=xerr, fmt="none",
                     ecolor=ln.get_color(), alpha=0.35)

    plt.xlabel("Net CBR ± 95% Confidence Interval")
    plt.ylabel(f"Effective Communication Range at PRR ≥ {prr_target} [m]")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(title="Repetitions")
    plt.tight_layout()
    if out_path:
        plt.savefig(out_path, dpi=220)
    plt.show()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Plot Fig4 with MATLAB-like averaging")
    ap.add_argument("--input-dir", default="results_csv")
    ap.add_argument("--out-plot", default="fig4_range_vs_netcbr.png")
    ap.add_argument("--out-csv", default="fig4_aggregated.csv")
    ap.add_argument("--prr-target", type=float, default=0.9, help="PRR threshold used for range extraction (e.g. 0.9)")
    ap.add_argument("--min-density", type=int, default=None, help="Include only rows with density >= this value (default: no minimum)")
    args = ap.parse_args()

    agg = build_dataset_like_matlab(args.input_dir, target=args.prr_target, min_density=args.min_density)
    if agg.empty:
        raise SystemExit("No data rows built.")
    agg.to_csv(args.out_csv, index=False)
    print(f"[ok] wrote aggregated data -> {args.out_csv}")
    plot_fig4(agg, args.out_plot, args.prr_target)
    print(f"[ok] wrote plot -> {args.out_plot}")

if __name__ == "__main__":
    main()
