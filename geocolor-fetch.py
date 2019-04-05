#!/usr/bin/python

from __future__ import print_function
import os
import os.path
import argparse
import json
import urllib2
import re
import logging
import multiprocessing
import shutil
from PIL import Image, ImageDraw, ImageFont, ImageFile
#ImageFile.LOAD_TRUNCATED_IMAGES = True

goes = ''
resolution = ''
region = ''
force = ''
composite = ''
hdtv = ''
reprocess = ''
urldir = ''
hoffset = 0
voffset = 0
htiles = 0
vtiles = 0
mapts = ""
mapurl = ""
mapdir = ""

urllib2knownerrors = {
    404: "Not found"
}

def fetchurl(arg):
    url, fn = arg
    logging.debug("Fetch: URL %s" % (url))
    req = urllib2.Request(url)
    try:
        response = urllib2.urlopen(req)
    except urllib2.HTTPError, e:
        logging.warning("Fetch URL: %s" % (url))
        if e.code in urllib2knownerrors:
            logging.warning("Fetch URL Failed(%d): %s" % (e.code, urllib2knownerrors[e.code]))
            return
        else:
            logging.warning("Fetch URL Error %d: %s" % (e.code, url))
            logging.warning(e.read())
            #raise
            return
                
    tile = response.read()
    logging.debug("Fetch: Write %s" % (fn))
    with open(fn, "w+b") as f: # added +b for binary file - who knew?
        f.write(tile)
    return

def fetchmap():
    global mapts
    if mapts == "": # should be a raise?
        return
    if not os.path.isdir(mapdir):
        logging.info("Fetch Map: %s" % (mapdir))
        os.makedirs(mapdir)

    urls = []
    for i in range(voffset, voffset + vtiles):
        for j in range(hoffset, hoffset + htiles):
            fn = "%s/%03d_%03d.png" % (mapdir, i, j)
            if not os.path.isfile(fn):
                urls.append(["%s/%03d_%03d.png" % (mapurl, i, j), fn])
    
    for ff in urls:
        logging.info("Map fetch: %s" % (ff))
        fetchurl(ff)
    #p = multiprocessing.Pool(4)
    #p.map(fetchurl, urls)

def fetchts(ts, destdir):
    date = ts[:8]
    imgurl = baseurl % (date, goes, ts, urldir)

    oldumask = os.umask(0)
    if not os.path.exists(destdir):
        year = ts[0:4]
        month = ts[4:6]
        day = ts[6:8]
        hour = ts[8:10]
        minute = ts[10:12]
        sec = ts[12:14]
        logging.info("Fetch: mkdir %s" % destdir)
        os.makedirs(destdir, mode=0777)

    urls = []
    for i in range(voffset, voffset + vtiles):
        for j in range(hoffset, hoffset + htiles):
            fn = "%s/%03d_%03d.png" % (destdir, i, j)
            if not os.path.isfile(fn):
                urls.append(["%s/%03d_%03d.png" % (imgurl, i, j), fn])
    # logging.debug("Tiles to fetch {}".format(urls))

    for ff in urls:
        fetchurl(ff)
    logging.info("Fetch: Complete")
    #p = multiprocessing.Pool(4)
    #p.map(fetchurl, urls)
    return

def makecomposite(ts, destdir):
    date = ts[:8]
    time = ts[8:12]
    
    # Put the tiles into a canvas, overlay the map, filet to HD size, then adorn w/ timestamp & logos
    compositedir = "%s/%s/%s" % (rootdir, "composite", date)
    compositefn = "%s/%s_%s_%s_%s.png" % (compositedir, prefix, urldir, region, ts)

    hdtvdir = "%s/%s/%s" % (rootdir, "hdtv", date)
    if reprocess:
        hdtvdir = "%s/%s/%s" % (rootdir, "reprocess", date)
    hdfn = "%s/%s_%s_%s_%s.png" % (hdtvdir, prefix, urldir, region, ts)

    if os.path.isfile(compositefn) and os.path.isfile(hdfn) and not force:
        logging.info("Composites Exist: %s %s" % (date, time))
        return

    if composite and not os.path.exists(compositedir):
        os.makedirs(compositedir)
    if hdtv and not os.path.exists(hdtvdir):
        os.makedirs(hdtvdir)

    logging.debug("Reading tiles: %s %s" % (date, time))
    base = Image.new('RGBA', (htiles*tilesize, vtiles*tilesize))
    for i in range(0, vtiles):
        for j in range(0, htiles):
            try:
                tile = Image.open("%s/%03d_%03d.png" % (destdir, i+voffset, j+hoffset)).convert('RGBA')
            except:
                logging.warning("Couldn't open %s/%03d_%03d.png" % (destdir, i+voffset, j+hoffset))
                return
            base.paste(tile, (j * tilesize, i * tilesize))

    # make the map overlay if all map tiles exist
    logging.debug("Reading map: %s %s" % (date, time))
    try:
        fetchmap()
        overlay = Image.new('RGBA', (htiles*tilesize, vtiles*tilesize))
        for i in range(0, vtiles):
            for j in range(0, htiles):
                tile = Image.open("%s/%03d_%03d.png" % (mapdir, i+voffset, j+hoffset)).convert('RGBA')
                overlay.paste(tile, (j * tilesize, i * tilesize))
        base = Image.alpha_composite(base, overlay)
    except:
        logging.warning("fetchmap failed")
        # pass # catch if fetchmap() fails

    if composite and not os.path.isfile(compositefn):
        logging.info("Composite created: %s" % (compositefn))
        base.save(compositefn)

    if hdtv:
        # Crop to (w x h) @ upper corner (x, y)
        # Base image is 2712x2034
        # Crop to 16x9 aspect ratio - step is 48x27,(96x54), (192x108), (240x135)
        # Sizes might be 2400x1350, 2640x1485, 2688x1512
        if goes == "16": # Eastern CONUS, not for hurricanes
            x = 150
            y = 200
            w = 2400
            h = 1350
        if goes == "17": # Eastern Pacific to watch snow & Hawaii sailing wx
            x = 24
            y = 180
            w = 2688
            h = 1512
        crop = base.crop((x , y, x+w, y+h))
        # crop.load()
        hdcanvas = crop.resize((1920, 1080), Image.LANCZOS)

        year = ts[0:4]
        month = ts[4:6]
        day = ts[6:8]
        hour = ts[8:10]
        minute = ts[10:12]

        cfont = ImageFont.truetype("lucon.ttf", 24) # lucida console - cour.ttf is ugly
        # getsize() returns for actual string, so figure out the greatest possible font height
        x, fheight = cfont.getsize("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+[{]}\|;:',<.>/?")
        tsstring = " GOES-%s %s-%s-%s %s:%sZ " % (goes, year, month, day, hour, minute)
        x = 4
        y = 8
        ypad = 2
        w, h = cfont.getsize(tsstring)
        canvas = Image.new('RGBA', hdcanvas.size, (255,255,255,0))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((x, y, x+w, y+fheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
        draw.text((x, y+ypad), tsstring, fill=(0xff, 0xff, 0xff, 0xff), font=cfont)
        # print ("x%d y%d w%d h%d ypad%d fheight%d" % (x, y, w, h, ypad, fheight))
        if goes == "17" and (int(year) < 2019) or ((int(year) == 2019) and ( (int(month) < 2) or ((int(month) == 2) and (int(day) < 12)))):
            wstring = " GOES-17 Preliminary, Non-Operational Data "
            w, h = cfont.getsize(wstring)
            x = hdcanvas.width - (w + x)
            draw.rectangle((x, y, x+w, y+fheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
            draw.text((x, y+ypad), wstring, fill=(0xff, 0xff, 0xff, 0xff), font=cfont)
            # print ("x%d y%d w%d h%d ypad%d fheight%d" % (x, y, w, h, ypad, fheight))

        logoheight = 96.0
        logospacing = 4
        logomargin = 8
        rammb1 = Image.open("rammb_logo.png")
        rammblogo = rammb1.resize((int(rammb1.width * (logoheight / rammb1.height)), int(logoheight)), Image.ANTIALIAS)
        x = hdcanvas.width - (rammblogo.width + logomargin)
        y = hdcanvas.height - (rammblogo.height + logomargin)
        # print("ciralogo %dx%d @ %d, %d" % (rammblogo.width, rammblogo.height, x, y))
        hdcanvas.paste(rammblogo, (x, y), rammblogo)

        cira1 = Image.open("cira18Logo.png")
        ciralogo = cira1.resize((int(cira1.width * (logoheight / cira1.height)), int(logoheight)), Image.ANTIALIAS)
        x = x - (ciralogo.width + logospacing)
        y = hdcanvas.height - (ciralogo.height + logomargin)
        # print("ciralogo %dx%d @ %d, %d" % (ciralogo.width, ciralogo.height, x, y))
        hdcanvas.paste(ciralogo, (x, y), ciralogo)
        
        if goes == "16":
            l1 = Image.open("goesRDecalSmall.png")
        if goes == "17":
            l1 = Image.open("GOES-S-Mission-Logo-1024x655.png")
        goeslogo = l1.resize((int(l1.width * (logoheight / l1.height)), int(logoheight)), Image.ANTIALIAS)
        x = x - (goeslogo.width + logospacing)
        y = hdcanvas.height - (goeslogo.height + logomargin)
        # print("goeslogo %dx%d @ %d, %d" % (goeslogo.width, goeslogo.height, x, y))
        hdcanvas.paste(goeslogo, (x, y), goeslogo)
        
        # afont = ImageFont.truetype("times.ttf", 24)
        text = " Image Credits "
        w, h = cfont.getsize(text)
        x = hdcanvas.width - (w + logomargin)
        y = hdcanvas.height - (logoheight + h + logomargin + logospacing + ypad + ypad)
        # print("image credit %dx%d @ %d, %d" % (w, h, x, y))
        draw.rectangle((x, y, x+w, y+h), fill=(0,0,0,0x80))
        draw.text((x,y+ypad), text, fill=(255,255,255,255), font=cfont)
        hdcanvas = Image.alpha_composite(hdcanvas, canvas)
        del draw
    
        hdcanvas.save(hdfn)
        logging.info("HD created: %s" % (hdfn))

    logging.info("rm %s" % (destdir))
    shutil.rmtree(destdir)

def gentimestamps():
    tslist = []
    dpat = re.compile(r"\d\d\d\d\d\d\d\d")
    tpat = re.compile(r"\d\d\d\d\d\d")
    logging.debug("gentimestamps()")
    for date in os.listdir(rootdir):
        if os.path.isdir(p1) and dpat.match(date) != None:
            for time in os.listdir(p1):
                p2 = "%s/%s" % (p1, time)
                if os.path.isdir("%s" % (p2)) and tpat.match(time) != None:
                    tslist.append("%s%s" % (date, time))
    return(tslist)

def reqtimestamps():
    logging.debug("reqtimestamps(%s)" % (tsurl))
    # Request the list of timestamps available from CIRA
    req = urllib2.Request(tsurl)
    response = urllib2.urlopen(req)
    timestampsjson = response.read()
    timestampsstruct = json.loads(timestampsjson)
    return(timestampsstruct["timestamps_int"])

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-satellite", choices=["16", "17"], default="16", help="GOES Satellite")
    parser.add_argument("-resolution", choices=["4k", "1k"], default="1k", help="Resolution")
    parser.add_argument("-region", choices=["full", "conus"], default="conus", help="Region")
    parser.add_argument("-force", default=False, action='store_true', dest="force", help="Overwrite existing output")
    parser.add_argument("-all", default=True, action='store_false', dest="last", help="Fetch all times, default last only")
    parser.add_argument("-reprocess", default=False, action='store_true', help="Redo all HD images")
    parser.add_argument("-composite", default=False, action='store_true', help="Create a full-disk composite")
    parser.add_argument("-hdtv", default=False, action='store_true', help="Create a HDTV-size composite of a certain area")
    parser.add_argument("-log", choices=["debug", "info", "warning", "error", "critical"], default="info", help="Log level")
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

    goes = args.satellite
    resolution = args.resolution
    region = args.region
    force = args.force
    reprocess = args.reprocess
    composite = args.composite
    hdtv = args.hdtv

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)
    logging.info(args)

    if resolution == "1k":
        urldir = "03"
        if region == "full":
            hoffset = 0
            voffset = 0
            htiles = 8
            vtiles = 8
        if (goes == "16") and (region == "conus"):
            hoffset = 1
            voffset = 0
            htiles = 4
            vtiles = 3
        if (goes == "17") and (region == "conus"):
            hoffset = 2
            voffset = 0
            htiles = 4
            vtiles = 3

    if resolution == "4k":
        urldir = "04"
        if region == "full":
            hoffset = 0
            voffset = 0
            htiles = 16
            vtiles = 16
        if (goes == "16") and (region == "conus"):
            hoffset = 2
            voffset = 1
            htiles = 7
            vtiles = 4
        if (goes == "17") and (region == "conus"):
            hoffset = 2
            voffset = 1
            htiles = 7
            vtiles = 4

    tilesize = 678
    prefix = "GOES-%s" % (goes)
    #rootdir = "/Users/lance/Downloads/NASA/%s_%s_geocolor" % (prefix, urldir)
    rootdir = "M:/NASA/%s_%s_geocolor" % (prefix, urldir)
    tsurl = "http://rammb-slider.cira.colostate.edu/data/json/goes-%s/full_disk/geocolor/latest_times.json" % (goes)
    baseurl = "http://rammb-slider.cira.colostate.edu/data/imagery/%s/goes-%s---full_disk/geocolor/%s/%s"

    mapts = ""
    if (goes == "16"):
        mapts = "20171201000000"
    if (goes == "17"):
        mapts = "20181115150038"

    #http://rammb-slider.cira.colostate.edu/data/map/goes-17/full_disk/white/20181115150038/04/004_003.png

    mapdir = "%s/map_full_disk/%s" % (rootdir, mapts)
    mapurl = "http://rammb-slider.cira.colostate.edu/data/map/goes-%s/full_disk/white/%s/%s" % (goes, mapts, urldir)

    timestamps = []
    if reprocess:
        timestamps = gentimestamps()
    else:
        timestamps = reqtimestamps()

    # Sort timestamps to fetch in chronological order
    timestamps.sort()
    if args.last:
        timestamps = [ timestamps[-1] ]

    for stamp in timestamps:
        ts = str(stamp)
        date = ts[:8]
        time = ts[8:14]
        docomposite = False
        dohdtv = False

        destdir = "%s/%s/%s" % (rootdir, date, time)
        if composite and not force:
            compositedir = "%s/%s/%s" % (rootdir, "composite", date)
            compositefn = "%s/%s_%s_%s_%s.png" % (compositedir, prefix, urldir, region, ts)
            docomposite = not os.path.isfile(compositefn)

        if hdtv and not force:
            hdtvdir = "%s/%s/%s" % (rootdir, "hdtv", date)
            if reprocess:
                hdtvdir = "%s/%s/%s" % (rootdir, "reprocess", date)
            hdfn = "%s/%s_%s_%s_%s.png" % (hdtvdir, prefix, urldir, region, ts)
            dohdtv = not os.path.isfile(hdtvfn)

        if docomposite or dohdtv or force:
            fetchts(ts, destdir)
            makecomposite(ts, destdir)
            datedir = "%s/%s" % (rootdir, date)
            times = os.listdir(datedir)
            if len(times) == 0:
                logging.info("Clean up %s" % (datedir))
                os.rmdir(datedir)
            dates = os.listdir(rootdir)
            if len(dates) == 0:
                logging.info("Clean up %s" % (rootdir))
                os.rmdir(rootdir)

"""
       if docomposite or dohdtv or force:
            try:
                fetchts(ts, destdir)
                makecomposite(ts, destdir)
            except:
                logging.warning("Image fetch failed: %s-%s-%s %s:%s:%sZ" % (date[0:4], date[4:6], date[6:8], time[0:2], time[2:4], time[4:6]))
                pass
"""
