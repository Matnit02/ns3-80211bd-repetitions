set terminal pngcairo size 1000,700 enhanced
set output './results_mag_ldpc/16qam_1_2/density_2_plots/prr_vs_distance_d2_rep3_rng10454.png'
set datafile separator ';'
set title 'PRR vs Distance'
plot '< tail -n +2 ./results_mag_ldpc/16qam_1_2/density_2_csv/prr_vs_distance_d2_rep3_rng10454.csv | sed s/,/./g' using 1:2 with linespoints title "PRR"
set xlabel 'Distance upper-edge (m)'
set ylabel 'Packet Reception Ratio'
set grid
set yrange [0:1]
