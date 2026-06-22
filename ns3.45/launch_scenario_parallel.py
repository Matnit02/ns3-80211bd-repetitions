#!/usr/bin/env python3
import argparse
import itertools
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Tuple, Dict, Tuple as Tup, List

MODE_MAPPING = {
    "OfdmRate3MbpsBW10MHz":   "bpsk_1_2",
    "OfdmRate4_5MbpsBW10MHz": "bpsk_3_4",
    "OfdmRate6MbpsBW10MHz":   "qpsk_1_2",
    "OfdmRate9MbpsBW10MHz":   "qpsk_3_4",
    "OfdmRate12MbpsBW10MHz":  "16qam_1_2",
    "OfdmRate18MbpsBW10MHz":  "16qam_3_4",
    "OfdmRate24MbpsBW10MHz":  "64qam_2_3",
    "OfdmRate27MbpsBW10MHz":  "64qam_3_4",

    "OfdmRate32_5MbpsBW10MHz": "64qam_5_6",
    "OfdmRate39MbpsBW10MHz":   "256qam_3_4",
}

def get_folder_name(ns3_mode_str: str) -> str:
    """Returns the friendly folder name if mapped, else cleans the original string."""
    if ns3_mode_str in MODE_MAPPING:
        return MODE_MAPPING[ns3_mode_str]
    return ns3_mode_str.replace(" ", "_").replace("/", "-")

_running: Dict[Tup[int, int, int, str], subprocess.Popen] = {}
_running_lock = threading.Lock()
_cancel_event = threading.Event()

def _register_proc(key: Tup[int, int, int, str], proc: subprocess.Popen):
    with _running_lock:
        _running[key] = proc

def _unregister_proc(key: Tup[int, int, int, str]):
    with _running_lock:
        _running.pop(key, None)

def _terminate_all_children(grace_seconds: float = 3.0):
    """Send TERM to all child process groups, then KILL after a grace period."""
    procs = []
    with _running_lock:
        procs = list(_running.items())

    for key, proc in procs:
        if proc.poll() is not None:
            continue
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            pass

    end = time.time() + grace_seconds
    while time.time() < end:
        all_done = True
        with _running_lock:
            for proc in _running.values():
                if proc.poll() is None:
                    all_done = False
                    break
        if all_done:
            break
        time.sleep(0.1)

    with _running_lock:
        procs = list(_running.items())

    for key, proc in procs:
        if proc.poll() is not None:
            continue
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass

def timestamp():
    return datetime.now().strftime("%H:%M:%S")

def parse_densities(spec: str) -> List[int]:
    out: List[int] = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            parts = tok.split(":")
            if len(parts) == 2:
                a, b = int(parts[0]), int(parts[1])
                s = 1
            elif len(parts) == 3:
                a, s, b = int(parts[0]), int(parts[1]), int(parts[2])
            else:
                raise ValueError(f"Invalid range spec: {tok}")
            if s <= 0:
                raise ValueError("Step in range must be > 0")
            out.extend(list(range(a, b + 1, s)))
        elif "-" in tok and not tok.startswith("-"):
            if ":" in tok:
                rng, step = tok.split(":")
                a, b = map(int, rng.split("-"))
                s = int(step)
            else:
                a, b = map(int, tok.split("-"))
                s = 1
            if s <= 0:
                raise ValueError("Step in range must be > 0")
            out.extend(list(range(a, b + 1, s)))
        else:
            out.append(int(tok))
    seen = set()
    res = []
    for d in out:
        if d not in seen:
            res.append(d)
            seen.add(d)
    return res


def parse_rng_thresholds(spec: str) -> List[Tuple[str, int, int]]:
    """
    Parse threshold spec like "<=10:450, <=30:250, >30:20"
    Returns list of (operator, threshold, num_runs) tuples.
    """
    thresholds = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("<="):
            rest = tok[2:]
            op = "<="
        elif tok.startswith(">="):
            rest = tok[2:]
            op = ">="
        elif tok.startswith("<"):
            rest = tok[1:]
            op = "<"
        elif tok.startswith(">"):
            rest = tok[1:]
            op = ">"
        else:
            raise ValueError(f"Invalid threshold spec (must start with <, <=, >, >=): {tok}")

        if ":" not in rest:
            raise ValueError(f"Invalid threshold spec (missing :runs): {tok}")
        threshold_str, runs_str = rest.split(":", 1)
        threshold = int(threshold_str)
        runs = int(runs_str)
        thresholds.append((op, threshold, runs))
    return thresholds


def get_num_runs_for_density(density: int, thresholds: List[Tuple[str, int, int]], default_runs: int) -> int:
    """
    Get number of RNG runs for a given density based on thresholds.
    Thresholds are evaluated in order; first match wins.
    """
    for op, threshold, runs in thresholds:
        if op == "<=" and density <= threshold:
            return runs
        elif op == "<" and density < threshold:
            return runs
        elif op == ">=" and density >= threshold:
            return runs
        elif op == ">" and density > threshold:
            return runs
    return default_runs


def build_expected_csv_paths(output_dir: str,
                             data_mode: str,
                             dens: int,
                             rep: int,
                             rng: int,
                             cbr_node: int) -> Tuple[str, str]:
    folder_name = get_folder_name(data_mode)
    csv_dir = os.path.join(output_dir, folder_name, f"density_{dens}_csv")
    prr_name = f"prr_vs_distance_d{dens}_rep{rep}_rng{rng}.csv"
    cbr_name = f"cbr_n{cbr_node}_d{dens}_rep{rep}_rng{rng}.csv"
    return (os.path.join(csv_dir, prr_name), os.path.join(csv_dir, cbr_name))


def _is_nonempty_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) > 0


def is_setting_already_done(output_dir: str,
                            data_mode: str,
                            dens: int,
                            rep: int,
                            rng: int,
                            cbr_node: int) -> bool:
    prr_path, cbr_path = build_expected_csv_paths(output_dir, data_mode, dens, rep, rng, cbr_node)
    if cbr_node < 0:
        return _is_nonempty_file(prr_path)
    return _is_nonempty_file(prr_path) and _is_nonempty_file(cbr_path)

def run_one(sim_cmd: str,
            sim_name: str,
            rng: int,
            rep: int,
            dens: int,
            data_mode: str,
            output_dir: str,
            road_len: int,
            bin_width: int,
            cbr_node: int,
            cbr_interval: float,
            ldpc_gain_enabled: bool,
            verbose: bool) -> Tuple[int, int, int, str, int]:
    """
    Run a single ns-3 job. Returns (rng, rep, dens, data_mode, exit_code).
    """
    if _cancel_event.is_set():
        return (rng, rep, dens, data_mode, 130)

    prr_name = f"prr_vs_distance_d{dens}_rep{rep}_rng{rng}.csv"
    cbr_name = f"cbr_n{cbr_node}_d{dens}_rep{rep}_rng{rng}.csv"

    inner = (
        f'{sim_name}'
        f' --RngRun={rng}'
        f' --retransmissions={rep}'
        f' --density={dens}'
        f' --dataMode={data_mode}'
        f' --roadLength={road_len}'
        f' --binWidth={bin_width}'
        f' --outCsv={prr_name}'
        f' --cbrOutCsv={cbr_name}'
        f' --cbrNodeId={cbr_node}'
        f' --cbrInterval={cbr_interval}'
        f' --ldpcGainEnabled={str(ldpc_gain_enabled).lower()}'
    )

    if output_dir:
        folder_name = get_folder_name(data_mode)
        base_mode_dir = os.path.join(output_dir, folder_name)
        csv_dir = os.path.join(base_mode_dir, f"density_{dens}_csv")
        graph_dir = os.path.join(base_mode_dir, f"density_{dens}_plots")
        inner += f' --csvDir={csv_dir} --graphDir={graph_dir}'

    cmd = f'{sim_cmd} "{inner}"'

    stdout = None if verbose else subprocess.DEVNULL
    stderr = None if verbose else subprocess.DEVNULL

    if os.name == "posix":
        popen_kwargs = dict(shell=True, executable="/bin/bash", stdout=stdout, stderr=stderr, start_new_session=True)
    else:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        popen_kwargs = dict(shell=True, stdout=stdout, stderr=stderr, creationflags=CREATE_NEW_PROCESS_GROUP)

    proc = None
    key = (rng, rep, dens, data_mode)
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
        _register_proc(key, proc)

        while True:
            if _cancel_event.is_set():
                break
            ret = proc.poll()
            if ret is not None:
                return (rng, rep, dens, data_mode, ret)
            time.sleep(0.1)

        proc.wait(timeout=10)
        return (rng, rep, dens, data_mode, proc.returncode if proc.returncode is not None else 130)
    except subprocess.TimeoutExpired:
        return (rng, rep, dens, data_mode, 137)
    except Exception:
        return (rng, rep, dens, data_mode, 1)
    finally:
        _unregister_proc(key)


def main():
    def _str2bool(v: str) -> bool:
        if isinstance(v, bool):
            return v
        lv = v.strip().lower()
        if lv in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if lv in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise argparse.ArgumentTypeError(f"Invalid boolean value: {v}")

    ap = argparse.ArgumentParser(description="Parallel ns-3 launcher for fig4 (densities × repetitions × dataModes).")
    ap.add_argument("--sim-cmd", default="./ns3 run", help="Launcher command")
    ap.add_argument("--sim-name", default="scenario", help="Simulation target (program name)")

    ap.add_argument("--densities", default="1:10, 12:2:30, 40:20:100",
                    help="Vehicle densities (veh/km) - MATLAB style: start:end or start:step:end")
    ap.add_argument("--reps", type=int, nargs="+", default=[0, 1, 2, 3],
                    help="Retransmissions (extra copies)")
    ap.add_argument("--dataMode", nargs="+", default=["OfdmRate6MbpsBW10MHz"],
                    help="Wifi Mode(s) to simulate")

    ap.add_argument("--rng-start", type=int, default=10322, help="Starting RngRun")
    ap.add_argument("--num-runs", type=int, default=50, help="Default RngRuns per (density,rep,mode) if no threshold matches")
    ap.add_argument("--rng-thresholds", default="",
                    help="Density-based RNG runs, e.g. '<=10:450, <=30:250, >30:20'")

    ap.add_argument("--max-procs", type=int, default=0,
                    help="Max concurrent processes (0 = 12)")

    ap.add_argument("--output-dir", required=True, help="Base directory for outputs")
    ap.add_argument("--roadLength", type=int, default=2000, help="Highway length (m)")
    ap.add_argument("--binWidth", type=int, default=50, help="PRR distance bin width (m)")
    ap.add_argument("--cbrNodeId", type=int, default=0, help="Reference vehicle NodeId for CBR")
    ap.add_argument("--cbrInterval", type=float, default=0.1, help="CBR measurement interval (s)")
    ap.add_argument("--ldpcGainEnabled", type=_str2bool, default=False,
                    help="Enable/disable LDPC gain model in simulation (true/false)")
    ap.add_argument("--verbose", action="store_true", help="Show child stdout/stderr")
    args = ap.parse_args()

    densities = parse_densities(args.densities)

    rng_thresholds = []
    if args.rng_thresholds:
        rng_thresholds = parse_rng_thresholds(args.rng_thresholds)

    if args.output_dir:
        for mode in args.dataMode:
            folder_name = get_folder_name(mode)
            for d in densities:
                base_path = os.path.join(args.output_dir, folder_name)
                path_csv = os.path.join(base_path, f"density_{d}_csv")
                path_plots = os.path.join(base_path, f"density_{d}_plots")
                os.makedirs(path_csv, exist_ok=True)
                os.makedirs(path_plots, exist_ok=True)

    if args.max_procs and args.max_procs > 0:
        max_workers = args.max_procs
    else:
        max_workers = 12

    tasks = []
    skipped_existing = 0
    planned_total = 0
    runs_summary = {}
    for dens in densities:
        num_runs = get_num_runs_for_density(dens, rng_thresholds, args.num_runs)
        runs_summary[dens] = num_runs
        rng_values = [args.rng_start + i for i in range(num_runs)]
        for rng, rep, mode in itertools.product(rng_values, args.reps, args.dataMode):
            planned_total += 1
            if is_setting_already_done(args.output_dir, mode, dens, rep, rng, args.cbrNodeId):
                skipped_existing += 1
                continue
            tasks.append((args.sim_cmd, args.sim_name, rng, rep, dens, mode,
                          args.output_dir, args.roadLength, args.binWidth,
                          args.cbrNodeId, args.cbrInterval,
                          args.ldpcGainEnabled, args.verbose))

    total = len(tasks)

    print("----------------------------------------------")
    print("Starting parallel ns-3 simulations for fig4")
    print(f"→ Densities (veh/km):    {densities}")
    print(f"→ Retransmissions:       {args.reps}")
    print(f"→ Data Modes:            {args.dataMode}")
    print(f"→ Mapped Folders:        {[get_folder_name(m) for m in args.dataMode]}")
    if rng_thresholds:
        print(f"→ RNG Thresholds:        {args.rng_thresholds}")
        print(f"→ Runs per density:      {runs_summary}")
    else:
        print(f"→ RNG runs per setting:  {args.num_runs}")
    print(f"→ Planned simulations:   {planned_total}")
    print(f"→ Skipped existing:      {skipped_existing}")
    print(f"→ Simulations to run:    {total}")
    print(f"→ Max concurrent:        {max_workers}")
    print(f"→ CBR Node ID:           {args.cbrNodeId}")
    print(f"→ CBR Interval:          {args.cbrInterval} s")
    print(f"→ LDPC Gain Enabled:     {str(args.ldpcGainEnabled).lower()}")
    print(f"→ Output Base Dir:       {args.output_dir}")
    print("----------------------------------------------")

    interrupted = {"count": 0}

    def _handle_signal(sig, frame):
        interrupted["count"] += 1
        if interrupted["count"] == 1:
            print(f"\n[INFO] Signal {sig} detected — cancelling...", flush=True)
            _cancel_event.set()
            _terminate_all_children()
        else:
            os._exit(1)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTSTP"):
        signal.signal(signal.SIGTSTP, _handle_signal)

    completed = 0
    failed = 0

    futures = []
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for t in tasks:
                if _cancel_event.is_set():
                    break
                futures.append(pool.submit(run_one, *t))

            for fut in as_completed(futures):
                rng, rep, dens, mode, code = fut.result()
                completed += 1
                fname = get_folder_name(mode)
                if code != 0:
                    failed += 1
                    print(f"[FAIL  {timestamp()}] {fname} | d={dens} | r={rep} | seed={rng} (exit={code})")
                else:
                    print(f"[DONE  {timestamp()}] {fname} | d={dens} | r={rep} | seed={rng}")

                print(f"[{timestamp()}] PROGRESS: {completed}/{total} done | {failed} failed", end="\r", flush=True)

                if _cancel_event.is_set():
                    break
    except KeyboardInterrupt:
        _cancel_event.set()
        _terminate_all_children()
    finally:
        _terminate_all_children()

    print()
    print("----------------------------------------------")
    if _cancel_event.is_set():
        print("Cancelled by user.")
    else:
        print("Completed.")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Success: {completed - failed}, Failed: {failed}")
    print("Outputs per run (under csvDir if provided):")
    print("  • prr_vs_distance_d{dens}_rep{rep}_rng{rng}.csv")
    print(f"  • cbr_n{args.cbrNodeId}_d{{dens}}_rep{{rep}}_rng{{rng}}.csv")
    print("----------------------------------------------")


if __name__ == "__main__":
    main()
