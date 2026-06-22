set terminal pngcairo size 1000,700 enhanced
set output './results_mag/64qam_5_6/density_20_plots/prr_vs_distance_d20_rep2_rng10379.png'
set datafile separator ';'
set title 'PRR vs Distance'
plot '< tail -n +2 ./results_mag/64qam_5_6/density_20_csv/prr_vs_distance_d20_rep2_rng10379.csv | sed s/,/./g' using 1:2 with linespoints title "PRR"
set xlabel 'Distance upper-edge (m)'
set ylabel 'Packet Reception Ratio'
set grid
set yrange [0:1]
