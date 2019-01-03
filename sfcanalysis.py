#!/usr/bin/python

from __future__ import print_function
import os
import os.path
import urllib2
from datetime import datetime
import logging

urllib2knownerrors = {
    404: "Not found"
}

maps = {}

def fetchurl(url):
    logging.debug("fetch %s" % (url))
    req = urllib2.Request(url)
    try:
        resp = urllib2.urlopen(req)
    except urllib2.HTTPError, e:
        if e.code in urllib2knownerrors:
            logging.warning("Failed(%d): %s" % (e.code, urllib2knownerrors[e.code]))
        else:
            logging.warning("Error %d: %s" % (e.code, url))
            logging.warning(e.read())
        raise
                
    data = resp.read()
    return data

def sfcanalysis(region):
    logging.debug("%s: %s" % (region, maps[region]))
    latest = fetchurl(maps[region])
    logging.debug("%s: %d bytes" % (maps[region], len(latest)))
    
    d = "%s/%s" % (rootdir, region)
    fn = "%s/%s" % (d, "last.png")

    last = ()
    try:
        with open(fn, 'r+b') as f:
            last = f.read()
    except:
        logging.debug("%s couldn't open %s" % (region, fn))
        
    if latest == last:
        logging.debug("%s same content" % (region))
        return
    
    if not os.path.isdir(d):
        logging.warning("Creating %s" % (d))
        os.makedirs(d)
    with open(fn, 'w+b') as f:
        last = f.write(latest)
    utc = datetime.utcnow()
    fn = "%s/%04d%02d%02d%02d%02d.png" % (d, utc.year, utc.month, utc.day, utc.hour - int(utc.hour % 6), 0)
    with open(fn, "w+b") as f:
        last = f.write(latest)
    logging.info("New Surface Analysis %s: %s" % (region, fn))
        
if __name__ == '__main__':
    #rootdir = "/Users/lance/Downloads/NOAA/OPC"
#    loglevel = logging.DEBUG
    loglevel = logging.INFO
    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)
    rootdir = "S:/NOAA/OPC"
    maps["atlantic"] = "https://ocean.weather.gov/A_sfc_full_ocean_color.png"
    maps["pacific"] = "https://ocean.weather.gov/P_sfc_full_ocean_color.png"

    for region in ("atlantic", "pacific"):
        try:
            sfcanalysis(region)
        except:
            print("Couldn't fetch %s" % (region))
