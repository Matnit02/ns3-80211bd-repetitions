# ns3-80211bd-repetitions

Simulation code, ns-3.45 extensions, generated data, and plotting scripts for the master thesis:<br />
**Performance Analysis of Packet Repetition in IEEE 802.11bd Networks**

This repository contains an ns-3.45-based simulation environment for analyzing frame repetition in IEEE 802.11bd vehicular networks. The main goal is to evaluate how repeated broadcast transmissions affect communication reliability and channel occupancy in a V2V highway scenario.

The work focuses on the trade-off introduced by frame repetition: additional copies can improve the probability of successful packet reception, but they also increase channel usage and may reduce access opportunities for other vehicles.

## Repository scope

This project is intended for research and reproducibility of thesis results. It is not a full IEEE 802.11bd implementation. Instead, it extends ns-3.45 with the selected mechanisms required for the thesis analysis:

* IEEE 802.11bd (NGV) frame,
* additional MCS indices,
* LDPC SNR gain modeling,
* frame repetitions,
* maximum ratio combining for repeated frames,
* PRR and CBR measurement,
* batch result processing and figure generation.

The main analyzed metrics are:

* **Packet Reception Ratio (PRR)**,
* **Channel Busy Ratio (CBR)**,
* **Net Channel Busy Ratio**.

## Project structure

```text
.
├── data/
│   ├── results_802_11bd/      # simulation results for IEEE 802.11bd-related runs
│   └── results_802_11p/       # simulation results for IEEE 802.11p-related runs
├── ns3.45/                    
│   ├── scratch/               # simulation scenario
│   └── src/wifi/              # modified Wi-Fi module
├── plots/                     # generated thesis figures
├── fig1.py                    # plotting / analysis scripts
├── fig1b.py
├── fig2.py
├── fig3.py
├── fig4.py
├── fig4_cbr.py
├── fig5.py
├── fig6.py
├── fig7.py
├── fig8.py
└── README.md
```
## Implemented features

The ns-3.45 model includes:

* IEEE 802.11p-compatible operation,
* IEEE 802.11bd-compatible operation (NGV frames),
* higher MCS indices,
* LDPC gain abstraction based on effective SNR correction,
* configurable frame repetition count,
* repeated-frame combining using maximum ratio combining,
* multi-lane highway mobility scenario,
* PRR versus distance measurement,
* CBR measurement,
* Python scripts for aggregation and plotting.

## Simulation scenario

The main scenario models a highway with three lanes in each direction. Vehicles move with normally distributed speeds around 120 km/h. Nodes periodically broadcast CAM-like safety messages using ad hoc Wi-Fi communication.

The simulations evaluate the impact of:

* vehicle density,
* modulation and coding scheme,
* number of repetitions,
* IEEE 802.11p / IEEE 802.11bd mode,
* transmitter-receiver distance,
* channel load.

## Requirements

Recommended environment:

* Linux or WSL2,
* Python 3.10+,
* ns-3.45,
* C++ compiler supported by ns-3,
* Python packages:

  * `numpy`,
  * `pandas`,
  * `matplotlib`,
  * `scipy`.

Install Python dependencies with:

```bash
python3 -m pip install numpy pandas matplotlib scipy
```

## Building ns-3

From the repository root:

```bash
cd ns3.45
./ns3 configure
./ns3 build
```

If the build fails after moving files between systems, clean and rebuild:

```bash
./ns3 clean
./ns3 configure
./ns3 build
```

## Running a single simulation

From inside the `ns3.45/` directory:

```bash
./ns3 run "scenario \
  --RngRun=10322 \
  --density=40 \
  --retransmissions=1 \
  --dataMode=OfdmRate6MbpsBW10MHz \
  --roadLength=2000 \
  --binWidth=50 \
  --outCsv=prr_vs_distance_d40_rep1_rng10322.csv \
  --cbrOutCsv=cbr_n0_d40_rep1_rng10322.csv \
  --cbrNodeId=0 \
  --cbrInterval=0.1 \
  --ldpcGainEnabled=false"
```

The simulation target name depends on the scenario file located in `ns3.45/scratch/`. If your file has a different name, replace `scenario` with the correct ns-3 target.

## Output files

PRR output files use the following format:

```text
prr_vs_distance_d{density}_rep{rep}_rng{rng}.csv
```

Example:

```text
prr_vs_distance_d40_rep1_rng10322.csv
```

CBR output files use the following format:

```text
cbr_n{node}_d{density}_rep{rep}_rng{rng}.csv
```

Example:

```text
cbr_n0_d40_rep1_rng10322.csv
```

PRR CSV files contain:

```text
distance;prr;successes;opportunities
```

CBR CSV files contain:

```text
time_s;cbr
```

The plotting scripts handle semicolon-separated CSV files and comma decimal notation.

## Supported data modes

| ns-3 mode                 | MCS folder name |
| ------------------------- | --------------- |
| `OfdmRate3MbpsBW10MHz`    | `bpsk_1_2`      |
| `OfdmRate4_5MbpsBW10MHz`  | `bpsk_3_4`      |
| `OfdmRate6MbpsBW10MHz`    | `qpsk_1_2`      |
| `OfdmRate9MbpsBW10MHz`    | `qpsk_3_4`      |
| `OfdmRate12MbpsBW10MHz`   | `16qam_1_2`     |
| `OfdmRate18MbpsBW10MHz`   | `16qam_3_4`     |
| `OfdmRate24MbpsBW10MHz`   | `64qam_2_3`     |
| `OfdmRate27MbpsBW10MHz`   | `64qam_3_4`     |
| `OfdmRate32_5MbpsBW10MHz` | `64qam_5_6`     |
| `OfdmRate39MbpsBW10MHz`   | `256qam_3_4`    |

## Plotting

The Python scripts in the repository aggregate simulation results and generate figures used in the thesis.

Examples:

```bash
python3 fig4.py --help
```

Typical usage pattern:

```bash
python3 fig1.py \
  --input-dir data/results_802_11p/qpsk_1_2/density_40_csv \
  --out-plot plots/fig1.svg
```

For scripts comparing IEEE 802.11p and IEEE 802.11bd results, use paths from both result directories:

```bash
python3 fig7.py \
  --no-ldpc-input-dir data/results_802_11p/qpsk_1_2/density_40_csv \
  --ldpc-input-dir data/results_802_11bd/qpsk_1_2/density_40_csv \
  --out-plot plots/fig7.svg
```

Command-line options may differ between scripts. Use `--help` to check the exact arguments.

## Data directories

The repository separates baseline and IEEE 802.11bd-related simulation outputs:

```text
data/results_802_11p/
data/results_802_11bd/
```

A typical result tree contains MCS folders and density-specific CSV folders, for example:

```text
data/results_802_11p/
└── qpsk_1_2/
    ├── density_40_csv/
    └── density_40_plots/
```

## Git notes

If `ns3.45/` was copied from another Git checkout, it may contain nested Git metadata. In that case, Git may try to treat it as a submodule and fail with:

```text
does not have a commit checked out
```

To commit `ns3.45/` as normal files, remove the nested metadata:

```bash
rm -rf ns3.45/.git
git add ns3.45/
```

Large generated files can make the repository heavy. To ignore generated outputs, use a `.gitignore` similar to:

```gitignore
*.pcap
*.png
*.pdf
*.svg
*.csv
__pycache__/
*.pyc
```

## Thesis context

This repository accompanies the master thesis:

```text
Mateusz Setkowicz,
"Performance Analysis of Packet Repetition in IEEE 802.11bd Networks",
Master's thesis, AGH University of Krakow, 2026.
```

The thesis investigates how IEEE 802.11bd frame repetitions affect reliability and channel occupancy in vehicular V2V communication. The simulation model was created because ns-3.45 does not provide a complete built-in IEEE 802.11bd implementation.
