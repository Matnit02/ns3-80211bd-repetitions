set terminal pngcairo size 1000,700 enhanced
set output './results_mag/qpsk_3_4/density_28_plots/prr_vs_distance_d28_rep0_rng10392.png'
set datafile separator ';'
set title 'PRR vs Distance'
plot '< tail -n +2 ./results_mag/qpsk_3_4/density_28_csv/prr_vs_distance_d28_rep0_rng10392.csv | sed s/,/./g' using 1:2 with linespoints title "PRR"
set xlabel 'Distance upper-edge (m)'
set ylabel 'Packet Reception Ratio'
set grid
set yrange [0:1]
