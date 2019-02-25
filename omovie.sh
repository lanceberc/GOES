#!/bin/bash

export CYGWIN="winsymlinks:nativestrict"

for GOES in 17 16;
	    do
		date
		echo "Link GOES-${GOES} source images"
		mkdir /cygdrive/S/NASA/overlay-${GOES}
		x=0; for i in `find S:/NASA/GOES-${GOES}_03_geocolor/overlay -name "*.png"`; do counter=$(printf %04d $x); ln -s "$i" /cygdrive/S/NASA/overlay-${GOES}/img-"$counter".png >/dev/null 2>&1; x=$(($x+1)); done

#		date
#		echo
#		# With x265
#		C:/Users/Spear/ffmpeg-4.1-win64-static/bin/ffmpeg.exe -y -benchmark -i "S:/NASA/overlay-${GOES}/img-%04d.png"  -filter:v "setpts=2*PTS" -c:v libx265 -an -x265-params crf=25 -tag:v hvc1 -pix_fmt yuv420p SFC-${GOES}_h265_1080.mp4
#		echo

		# With x264
		date
		C:/Users/Spear/ffmpeg-4.1-win64-static/bin/ffmpeg.exe -y -benchmark -i "S:/NASA/overlay-${GOES}/img-%04d.png" -filter:v "setpts=2*PTS" -c:v libx264 -pix_fmt yuv420p SFC-${GOES}_h264_1080.mp4
		echo

#		rm -rf /cygdrive/S/NASA/tmp-${GOES}
#		echo

		date
		echo
done
