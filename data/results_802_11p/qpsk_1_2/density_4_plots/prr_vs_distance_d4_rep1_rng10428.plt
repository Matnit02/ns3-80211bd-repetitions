set terminal pngcairo size 1000,700 enhanced
set output './results_mag/OfdmRate6MbpsBW10MHz_--ldpcGainEnabled_false/density_4_plots/prr_vs_distance_d4_rep1_rng10428.png'
set datafile separator ';'
set title 'PRR vs Distance'
plot '< tail -n +2 ./results_mag/OfdmRate6MbpsBW10MHz_--ldpcGainEnabled_false/density_4_csv/prr_vs_distance_d4_rep1_rng10428.csv | sed s/,/./g' using 1:2 with linespoints title "PRR"
set xlabel 'Distance upper-edge (m)'
set ylabel 'Packet Reception Ratio'
set grid
set yrange [0:1]
