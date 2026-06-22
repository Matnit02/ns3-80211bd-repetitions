set terminal pngcairo size 1000,700 enhanced
set output './results_mag_ldpc/16qam_1_2/density_12_plots/prr_vs_distance_d12_rep0_rng10378.png'
set datafile separator ';'
set title 'PRR vs Distance'
plot '< tail -n +2 ./results_mag_ldpc/16qam_1_2/density_12_csv/prr_vs_distance_d12_rep0_rng10378.csv | sed s/,/./g' using 1:2 with linespoints title "PRR"
set xlabel 'Distance upper-edge (m)'
set ylabel 'Packet Reception Ratio'
set grid
set yrange [0:1]
