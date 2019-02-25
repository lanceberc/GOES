#!/usr/bin/python

from __future__ import print_function
import os
import os.path
import datetime
import argparse
import json
import urllib2
import re
import logging
import multiprocessing
from PIL import Image, ImageDraw, ImageFont, ImageFile

"""
https://cdn.star.nesdis.noaa.gov/GOES17/ABI/FD/GEOCOLOR/
<a href="20190281550_GOES17-ABI-FD-GEOCOLOR-5424x5424.jpg">
https://cdn.star.nesdis.noaa.gov/GOES17/ABI/FD/GEOCOLOR/20190242145_GOES17-ABI-FD-GEOCOLOR-5424x5424.jpg
"""

urlbase = "https://cdn.star.nesdis.noaa.gov/GOES%s/ABI/FD/GEOCOLOR/"
urlfn = "%s_GOES%s-ABI-FD-GEOCOLOR-5424x5424.jpg"
destbase = "S:/NASA/GOES-%s_03_geocolor/composite/%s%s%s"
destfn = "GOES-%s_03_full_%s%s%s%s%s.jpg"

urllib2knownerrors = {
    404: "Not found"
}

def fetchurl(url, fn):
    logging.debug("fetchurl %s" % (url))
    req = urllib2.Request(url)
    try:
        response = urllib2.urlopen(req)
    except urllib2.HTTPError, e:
        if e.code in urllib2knownerrors:
            logging.warning("Failed(%d): %s" % (e.code, urllib2knownerrors[e.code]))
        else:
            logging.warning("Error %d: %s" % (e.code, url))
            logging.warning(e.read())
        raise
                
    page = response.read()
    if fn:
        logging.debug("fetchfn %s" % (fn))
        with open(fn, "w+b") as f: # added +b for binary file - who knew?
            f.write(page)
        return(None)
    else:
        return page

def fetchdirectory(goes):
    """ Fetch directory url, filter for image timestamps w/ desired resolution"""
    root = "https://cdn.star.nesdis.noaa.gov/GOES%s/ABI/FD/GEOCOLOR/" % (goes)
    data = fetchurl(root, None)
    lines = data.split("\n")
    tspat = re.compile(r"<a href=\"(\d\d\d\d\d\d\d\d\d\d\d)_GOES%s-ABI-FD-GEOCOLOR-5424x5424.jpg\">" % goes)
    hrefpat = re.compile(r"<a href=\"(\d\d\d\d\d\d\d\d\d\d\d_GOES%s-ABI-FD-GEOCOLOR-5424x5424.jpg)\">" % goes)
    tslist = []
    for l in lines:
        ts = tspat.match(l)
        if ts:
            href = hrefpat.match(l)
            tslist.append([ts.group(1), root + href.group(1)])
    return tslist

def fetchts(goes, ts, url):
    """ NESDIS uses YEAR DAYOFYEAR TIME"""
    year = ts[0:4]
    doy = ts[4:7]
    dt = datetime.datetime.strptime(year + " " + doy, "%Y %j")
    month = "%02d" % (dt.month)
    day = "%02d" % (dt.day)
    hour = ts[7:9]
    minute = ts[9:11]

    destdir = destbase % (goes, year, month, day)
    fn = destfn % (goes, year, month, day, hour, minute)
    fn = "%s/%s" % (destdir, fn)
    
    if os.path.exists(fn):
        return

    if not os.path.exists(destdir):
        logging.info("Create directory %s" % (destdir))
        os.makedirs(destdir, mode=0777)
    logging.info("GOES-%s NESDIS %s: %s-%s-%s_%s:%sz" % (goes, ts, year, month, day, hour, minute))
    trash = fetchurl(url, fn)
    return(None)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-satellite", choices=["16", "17"], default="16", help="GOES Satellite")
    parser.add_argument("-force", default=False, action='store_true', dest="force", help="Overwrite existing output")
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
    force = args.force

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)
    logging.debug(args)

    tslist = fetchdirectory(goes)
    for ts in tslist:
        fetchts(goes, ts[0], ts[1])
