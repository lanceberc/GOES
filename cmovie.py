#!/usr/bin/python
import os
import sys
import argparse
import subprocess
import logging

ffmpeg = "C:/Users/Spear/ffmpeg-4.2-win64-static/bin/ffmpeg.exe"

regions = {
    "pacific" : {
        "goes": "17",
        "sourcedir": "M:/NASA/Overlay-Pacific",
    },
    "atlantic": {
        "goes": "16",
        "sourcedir": "M:/NASA/Overlay-Atlantic",
    },
    "cali" : {
        "goes": "17",
        "sourcedir": "M:/NASA/Cali_01",
    },
    "dorian" : {
        "goes": "17",
        "sourcedir": "M:/NASA/Dorian",
    },
    "storm" :{
        "goes": "17",
        #"sourcedir": "M:/NASA/2019-11_NorCal",
        "sourcedir": "M:/NASA/Storm201911",
    },
    "snow" :{
        "goes": "17",
        "sourcedir": "M:/NASA/Snocal_01",
    },
    "eddy" :{
        "goes": "17",
        "sourcedir": "M:/NASA/Eddy_01",
    },
    "coast" :{
        "goes": "17",
        "sourcedir": "M:/NASA/CaliCoast",
    },
    "sestorm" :{
        "goes": "16",
        "sourcedir": "M:/NASA/2019-12_SEStorm",
    }

}

def find_sources(region):
    sourcedir = regions[region]["sourcedir"]
    dates = os.listdir(sourcedir)
    dates.sort()
    l = []
    for date in dates:
        s = os.listdir("%s/%s" % (sourcedir, date))
        s.sort()
        files = [ fn for fn in s if fn.endswith(".png") or fn.endswith(".jpg") ]
        for f in files:
            l.append("%s/%s" % (date, f))
    return(l)

def make_concatfile(region, fns, start, end):
    sd = regions[region]["sourcedir"]
    fts = None
    lts = None
    print("Found %d source files" % (len(fns)))
    with open("%s-files.txt" % (region), "w") as f:
        for fn in fns:
            ts = fn[-16:-4]
            if (ts < start):
                continue
            if (ts > end):
                return((fts, lts))
            if not fts:
                fts = ts
            lts = ts
            f.write("file '%s/%s'\n" % (sd, fn))
            f.write("duration %5f\n" % (1.0/15.0))
    return((fts, lts))

def make_movie(region, size, ofile):
    scale = ''
    if size == "720":
        scale += '-s 1280x720'
    cmd = "%s -r 15 -y -benchmark -f concat -safe 0 -i %s-files.txt %s -c:v libx264 -crf 23 -probesize 20M -preset slow -pix_fmt yuv420p -an -movflags +faststart %s" % (ffmpeg, region, scale, ofile)
    logging.debug("Running %s" % (cmd))
    try:
        retcode = subprocess.check_call(cmd, shell=True)
    except subprocess.CalledProcessError as e:
        logging.info("Execution failed: %s" % (e))
    return(0)


if __name__ == '__main__':
    loglevel = logging.DEBUG
    parser = argparse.ArgumentParser()
    parser.add_argument("-atlantic", default=False, action='store_true', help="GOES-16 North Atlantic")
    parser.add_argument("-pacific", default=False, action='store_true', help="GOES-17 North Pacific")
    parser.add_argument("-cali", default=False, action='store_true', help="GOES-17 California Coast")
    parser.add_argument("-dorian", default=False, action='store_true', help="Hurricane Dorian")
    parser.add_argument("-storm", default=False, action='store_true', help="2019-11 Big Storm")
    parser.add_argument("-snow", default=False, action='store_true', help="GOES-17 SnoCal")
    parser.add_argument("-eddy", default=False, action='store_true', help="GOES-17 Catalina Eddy")
    parser.add_argument("-coast", default=False, action='store_true', help="GOES-17 California Coast SF - SD")
    parser.add_argument("-sestorm", default=False, action='store_true', help="2019 GOES-16 SE US CONUS Storm")
    parser.add_argument("-log", choices=["debug", "info", "warning", "error", "critical"], default="debug", help="Log level")
    parser.add_argument("-start", default="201801010000", help="Start timestamp")
    parser.add_argument("-end", default="202512312359", help="End timestamp")
    parser.add_argument("-f", default="", help="Output filename - defaults to SFC-<GOES>_<START>-<END>.mp4")
    parser.add_argument("-size", choices=["1080", "720"], default="1080", help="Size FullHD (1920x1080) or HD (1280x720)")
    args = parser.parse_args()

    if args.log == "debug":
        loglevel = logging.DEBUG
    if args.log == "info":
        loglevel = logging.INFO
    if args.log == "warning":
        loglevel = logging.WARNING
    if args.log == "error":
        loglevel = logging.ERROR
    if args.log == "critical":
        loglevel = logging.CRITICAL

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)

    logging.info(args)

    oceans = []
    if args.atlantic:
        oceans.append("atlantic")
    if args.pacific:
        oceans.append("pacific")
    if args.cali:
        oceans.append("cali")
    if args.dorian:
        oceans.append("dorian")
    if args.storm:
        oceans.append("storm")
    if args.snow:
        oceans.append("snow")
    if args.eddy:
        oceans.append("eddy")
    if args.coast:
        oceans.append("coast")
    if args.sestorm:
        oceans.append("sestorm")

    for o in oceans:
        ofile = args.f
        sources = find_sources(o)
        (fts, lts) = make_concatfile(o, sources, args.start, args.end)

        if ofile == "":
            s = "%s-%s-%s_%s%sZ" % (fts[0:4], fts[4:6], fts[6:8], fts[8:10], fts[10:12])
            et = sources[len(sources)-1]
            e = "%s-%s-%s_%s%sZ" % (lts[0:4], lts[4:6], lts[6:8], lts[8:10], lts[10:12])
            ofile = "%s_%s-%s.mp4" % (o, s, e)

        make_movie(o, args.size, ofile)
        os.remove("%s-files.txt" % (o))
        logging.info("Output in %s" % (ofile))
