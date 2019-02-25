#!/usr/bin/python
import os
import sys
import argparse
import subprocess
import logging

ffmpeg = "C:/Users/Spear/ffmpeg-4.1-win64-static/bin/ffmpeg.exe"

regions = {
    "pacific" : {
        "goes": "17",
        "sourcedir": "S:/NASA/GOES-17_03_geocolor/overlay",
        "linkdir": "S:/NASA/overlay-17",
    },
    "atlantic": {
        "goes": "16",
        "sourcedir": "S:/NASA/GOES-16_03_geocolor/overlay",
        "linkdir": "S:/NASA/overlay-16",
    }
}

def unlink_old(region):
    linkdir = regions[region]["linkdir"]
    d = os.listdir(linkdir)
    logging.debug("Unlinking %s" % (linkdir))
    for l in d:
        os.unlink("%s/%s" % (linkdir, l))
    return(0)

def find_sources(region):
    sourcedir = regions[region]["sourcedir"]
    s = os.listdir(sourcedir)
    l = [ fn for fn in s if fn.endswith(".png") ]
    l.sort()
    return(l)

def link_sources(region, fns, start, end):
    count = 0
    sd = regions[region]["sourcedir"]
    ld = regions[region]["linkdir"]
    for fn in fns:
        ts = fn[0:12]
        if (ts < start):
            continue
        if (ts > end):
            return
        s = "%s/%s" % (sd, fn)
        d = "%s/img-%04d.png" % (ld, count)
        # Python-2.7 on Windows under Cygwin can't make symlinks; this is a kludgey work-around
        #os.symlink(s, d)
        subprocess.call('export CYGWIN="winsymlinks:nativestrict"; ln -s %s %s' % (s, d), shell=True)
        count = count + 1
    return

def make_movie(region, size, ofile):
    #goes = regions[region]["goes"]
    ld =  regions[region]["linkdir"]
    scale = ''
    if size == "720":
        scale += '-s 1280x720'
    cmd = "%s -r 15 -y -benchmark -i \"%s/img-%s.png\" %s -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -movflags +faststart %s" % (ffmpeg, ld, "%04d", scale, ofile)
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
    parser.add_argument("-log", choices=["debug", "info", "warning", "error", "critical"], default="debug", help="Log level")
    parser.add_argument("-nolink", default=False, action='store_true', help="Don't remake symlinks")
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

    for o in oceans:
        ofile = args.f
        if ofile == "":
            ofile = "SFC-%s_%s-%s.mp4" % (regions[o]["goes"], args.start, args.end)

        if not args.nolink:
            unlink_old(o)
            sources = find_sources(o)
            link_sources(o, sources, args.start, args.end)
        make_movie(o, args.size, ofile)
        logging.info("Output in %s" % (ofile))
