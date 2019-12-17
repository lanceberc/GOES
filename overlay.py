#!/usr/bin/python
import os
import sys
import re
import time
import datetime
import argparse
#import subprocess
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from osgeo import gdal

"""
The general flow is to get geocolored GOES tiles from CIRA/RAMMB, paste them into a full-disk image,
warp it into a rectangular Mercator region, then overlay the relevant OPC Surface Analysis chart,
fading chart in/out around its valid time. Fetching the tiles and producing the full-disk image is
currently done elsewhere.

Hard-earned knowledge:

Tiles from CIRA/RAMMB are pasted together into a PNG. In order to use the GDAL tools to warp them
the PNG has to be geo-located via a sidecar file (foo.png.aux.xml). For GOES-17 at 2k resolution
the sidecar is:

<PAMDataset>
  <SRS>PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",6378169,298.2572221]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Geostationary_Satellite"],PARAMETER["central_meridian",-137],PARAMETER["satellite_height",35785831],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1],EXTENSION["PROJ4","+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027 +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-137 +over"]]</SRS>
  <Metadata domain="IMAGE_STRUCTURE">
    <MDI key="INTERLEAVE">PIXEL</MDI>
  </Metadata>
  <GeoTransform> -5434894.7009821739, 2004.0173154875411, 0.0, 5434894.7009821739, 0.0, -2004.0173154875411</GeoTransform>
</PAMDataset>

From https://github.com/pytroll/satpy/blob/master/satpy/readers/geocat.py - not quite the same as NASA:
+proj=geos +lon_0=-75 +h=35786023.0 +a=6378137.0 +b=6356752.31414 +sweep=x +units=m +no_defs

The Proj4 and GeoTransform numbers come from NASA's GOES Product User Guide Volume 3 (Level1b user
guide), pages 11-20.
https://www.goes-r.gov/users/docs/PUG-L1b-vol3.pdf

The geos projection specifies a Geostationary Satellite with a specific ellipsoid. AFAIK NASA/NOAA
renormalize L1b data centering it over 137 degrees west even though GOES-17 is currently at 137.2W
which allows warping to be pixel-perfect.

The GeoTransform parameters specify the dimenstions of the full-disk image in meters and the width
and height of the image pixel at nadir (directly underneath the satellite) in meters, in this case
just over 2km.

[ link to affine geometry page? ]

There are many ways to specify a projection to proj4. Using EPSG definitions is popular.
WGS84 =  EPSG:4326
Mercator =  EPSG:3395
Web Mercator =  EPSG:3857

The OPC charts are Mercator projections. The GOES image warped from geos (Geosynchronos)
projection to Mercator (instead of both warped to some other projection) to keep the text and
annotations readable. Besides, many are familiar with Mercator maps of the North Atlantic and Pacific.

The EPSG definition of Mercator (EPSG:3395) doesn't work with GDAL when crossing the anti-meridian.
Many workflows apparently process images and charts as East and West hemisphere halves and join them
later - this is both a hassle and distasteful. Instead use a Proj4 definition that allows specifying
ranges that cross the antimeridian with +over so our Pacific area is from -230 to -100 (aka 130E to 100W).
CENTER_LONG tells GDAL not to "go the other way" around the globe, centering on the antimerdian instead
of Greenwich.

+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over

After warping the image to Mercator convert from GDAL's format to a Pillow image via numpy. This is
done by transposing the axes so a linear readout of the GDAL buffer comes out in RGBA order. Using
numpy is much, much faster than a naive copy (like more than 30s per image faster).

Program flow:

  List the OPC maps in chrono order
  List the GOES images in chrono order
  For each GOES image
      Possibly advance surface analysis
      Overlay map w/ transparency
      Crop composite
      Overlay logos & labels
      Save overlay
"""

# Crop to (w x h) @ upper corner (x, y)
# Base image is 2712x2034
# Crop to 16x9 aspect ratio - step is 48x27,(96x54), (192x108), (240x135)
# Sizes might be 2400x1350, 2640x1485, 2688x1512

# Cropped Pacific analysis (getting rid of top/bottom margins) is 2441 x 1556
# Crop to 2160 x 1215 (281, 258, 2441, 1338)
# Crop to 2080 x 1170 (2441-2080, 1388-1170, 2441, 1388)

# Constants taken from NASA Product Users Guide, Volume 3, Section 5.1.2
# https://www.goes-r.gov/users/docs/PUG-L1b-vol3.pdf

goes16 = { # GOES-16, aka GOES-R, GOES-EAST
    "p_height": 35786023.0,           # perspective height from the ellipsoid
    "height": 42164160.0,             # from center of the earth
    "longitude": -75.0,
    "sweep_axis": 'x',
    "semi_major": 6378137.0,          # GRS-80 ellipsoid
    "semi_minor": 6356752.31414,      # GRS-80 ellipsoid
    "flattening": 298.257222096,
    "eccentricity": 0.0818191910435,
    # The other resolution (.5k, 4k, etc) can be added here
    "1k": {
        "resolution": 0.000028,       # radians per pixel
        "FD": {
            "x_offset": -0.151858,    # radians from nadir
            "y_offset":  0.151858,
            "shape": (10848, 10848),  # pixels in image
            "nanPoint": None          # Need to figure out how to handle off-earth for FD images
        },
        "CONUS": {
            "x_offset": -0.101346,
            "y_offset":  0.128226,
            "shape": (5000, 3000),
            # Brian Blaylock's Gulf of Alaska location for the corner of GOES-16 CONUS that's off-Earth
            "nanPoint": (-152, 57)    # Needed for pcolormesh when using Cartopy
        }
    },
    "2k": {
        "resolution": 0.000056,
        "FD": {
            "x_offset": -0.151844,
            "y_offset":  0.151844,
            "shape": (5424, 5424),
            "nanPoint": None
        },
        "CONUS": {
            "x_offset": -0.101332,
            "y_offset":  0.128212,
            "shape": (2500, 1500),
            "nanPoint": (-152, 57)
        }
    }
}

goes17 = { # GOES-17, aka GOES-S, GOES-WEST
    "p_height": 35786023.0,
    "height": 42164160.0,
    "longitude": -137.0,
    "sweep_axis": 'x',
    "semi_major": 6378137.0,
    "semi_minor": 6356752.31414,
    "flattening": 298.257222096,
    "eccentricity": 0.0818191910435,
    "1k": {
        "resolution": 0.000028,
        "FD": {
            "x_offset": -0.151858,
            "y_offset":  0.151858,
            "shape": (10848, 10848),
            "nanPoint": None
        },
        "CONUS": {
            "x_offset": -0.069986,
            "y_offset":  0.128226,
            "shape": (5000, 3000),
            "nanPoint": None
        }
    },
    "2k": {
        "resolution": 0.000056,
        "FD": {
            "x_offset": -0.151844,
            "y_offset":  0.151844,
            "shape": (5424, 5424),
            "nanPoint": None
        },
        "CONUS": {
            "x_offset": -0.069972,
            "y_offset":  0.128212,
            "shape": (2500, 1500),
            "nanPoint": None
        }
    },
}

# The EPSG definition of Mercator doesn't allow longitudes that extend 
# past -180 or 180 which makes working in the Pacific difficult. Define
# our own, plus one with the centralized on the anti-meridian to allow 
# working with GOES-17 continuous.
proj_mercator =      "+proj=merc +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over"
proj_anti_mercator = "+proj=merc +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over +lon_0=-180"

timezones = {}
timezones["PST"] = -7
timezones["EST"] = -4

# The first sfc is 2018-12-24_0600, so start 3 hours earlier for the first image
regions = {}
regions["Atlantic"] = {"sfc": "M:/NOAA/OPC/atlantic",
                       "ciradir": "M:/NASA/CIRA_GOES-East/composite",
                       "ciramapdir": "M:/NASA/CIRA_GOES-East/map",
                       "ciramapts": "20171201000000",
                       "nesdisdir": "M:/NASA/NESDIS_GOES-East",
                       "dest": "M:/NASA/Overlay-Atlantic",
                       "starttime": "201811200000",
                       "goes": "16",
                       "sat": goes16,
                       "res": "2k",
                       "sector": "FD",
                       "mercator": proj_mercator,
                       "interestArea": [-100, 16, 10, 65], # lat/long of lr, ul corner of Surface Analysis
                       "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                       #"crop": (2441-2160, 1488-1215, 2441, 1488)
                       "crop": (0, 135, 2441-240, 1556), # source is 2441x1556
                       "logos": [{"fn": "rammb_logo.png"}, {"fn": "cira18Logo.png"}, {"fn": "goesRDecalSmall.png"}, {"fn": "NOAA_logo.png"}, {"fn": "NWS_logo.png"}],
                       "oFormat": "jpg",
                       "oRes": (1920, 1080),
                       #"oRes": (1280, 720),
}

regions["Pacific"] = {"sfc": "M:/NOAA/OPC/pacific",
                      "ciradir": "M:/NASA/CIRA_GOES-West/composite",
                      "ciramapdir": "M:/NASA/CIRA_GOES-West/map",
                      "ciramapts": "20181115150038",
                      "nesdisdir": "M:/NASA/NESDIS_GOES-West",
                      "dest": "M:/NASA/Overlay-Pacific",
                      "goes": "17",
                      "sat": goes17,
                      "res": "2k",
                      "sector": "FD",
                      "starttime": "201812240300",
                      "interestArea": [-225, 16, -115, 65], # lat/long of ur, ll corner of Surface Analysis
                      "mercator": proj_anti_mercator,
                      "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                      "crop": (2441-2160, 1488-1215, 2441, 1488),
                      "logos": [{"fn": "rammb_logo.png"}, {"fn": "cira18Logo.png"}, {"fn": "GOES-S-Mission-Logo-1024x655.png"}, {"fn": "NOAA_logo.png"}, {"fn": "NWS_logo.png"}],
                      "logopos": "left",
                      "oFormat": "jpg",
                      "oRes": (1280, 720),
}

regions["Cali_01"] = {"sfc": None,
                      "ciradir": "M:/NASA/CIRA_Cali-FD/composite",
                      "ciramapdir": "M:/NASA/CIRA_Cali-FD/map",
                      "ciramapts": "20181115150038",
                      "dest": "M:/NASA/Cali_01",
                      "starttime": "201812240300",
                      "goes": "17",
                      "sat": goes17,
                      "res": "1k",
                      "sector": "FD",
                      "interestArea": [-128, 31.5, -113.5, 39.5], # lat/long of ur, ll corner of Surface Analysis
                      "mercator": proj_anti_mercator,
                      "sfcanalysisArea": (0, 0, 1920, 1080),
                      "crop": (0, 0, 1920, 1080),
                      "logos": [],
                      "fulldisktiles": 16,
                      "tilesize": 678,
                      "hoffset": 9,
                      "voffset": 2,
                      "htiles": 2,
                      "vtiles": 2,
                      "oFormat": "png",
                      "oRes": (1280, 720),
}

regions["Cali_02"] = {"sfc": None,
                      "ciradir": "M:/NASA/CIRA_Cali-FD/composite",
                      #"ciramapdir": "M:/NASA/CIRA_Cali-FD/map",
                      "ciramapts": "20181115150038",
                      "dest": "M:/NASA/Cali_02",
                      "starttime": "201911280000",
                      "goes": "17",
                      "sat": goes17,
                      "res": "1k",
                      "sector": "FD",
                      "interestArea": [-128, 31.5, -113.5, 39.5], # lat/long of ur, ll corner of Surface Analysis
                      "mercator": proj_anti_mercator,
                      "sfcanalysisArea": (0, 0, 1280, 720),
                      "crop": (0, 0, 1280, 720),
                      "logos": [],
                      "fulldisktiles": 16,
                      "tilesize": 678,
                      "hoffset": 9,
                      "voffset": 2,
                      "htiles": 2,
                      "vtiles": 2,
                      "oFormat": "jpg",
                      "oRes": (1280, 720),
}

regions["Eddy"] = {"sfc": None,
                   "nesdisdir": "M:/NASA/NESDIS_CONUS-West",
                   "dest": "M:/NASA/Eddy_01",
                   "goes": "17",
                   "sat": goes17,
                   "res": "1k",
                   "sector": "CONUS",
                   "interestArea": [-124.75, 31.45, -115.75, 37.0], # lat/long of ur, ll corner of Surface Analysis
                   "mercator": proj_anti_mercator,
                   "logos": [],
                   "starttime": "201908010000",
                   "sfcanalysisArea": (0, 0, 1280, 720),
                   "crop": (0, 0, 1280, 720),
                   "oFormat": "jpg",
                   "oRes": (1280, 720),
}

regions["Dorian"] = {"sfc": None,
                     "nesdisdir": "M:/NASA/NESDIS_GOES-East",
                     "dest": "M:/NASA/Dorian",
                     "sector": "FD",
                     "goes": "16",
                     "res": "2k",
                     "sat": goes16,
                     "sector": "FD",
                     "interestArea": [-90, 18.5, -57, 35], # lat/long of ur, ll corner of Surface Analysis
                     "mercator": proj_mercator,
                     "logos": [],
                     "starttime": "201908281200",
                     "sfcanalysisArea": (0, 0, 1920, 1080),
                     "crop": (0, 0, 1920, 1080),
                     "oFormat": "png",
                     "oRes": (1920, 1080),
}

regions["Snowcal"] = {"sfc": None,
                      "nesdisdir": "M:/NASA/NESDIS_CONUS-West",
                      "ciramapdir": "M:/NASA/CIRA_Cali-FD/map",
                      "ciramapts": "20181115150038",
                      "dest": "M:/NASA/Snocal_01",
                      "goes": "17",
                      "sat": goes17,
                      "res": "1k",
                      "sector": "CONUS",
                      "interestArea": [-134.0, 33.5, -115.0, 44.0], # lat/long of ur, ll corner of Surface Analysis
                      "mercator": proj_anti_mercator,
                      "logos": [],
                      "starttime": "201911250000",
                      "sfcanalysisArea": (0, 0, 1280, 720),
                      "crop": (0, 0, 1280, 720),
                      "oFormat": "jpg",
                      "oRes": (1280, 720),
}

regions["Storm201911"] = {"sfc": None,
                          "nesdisdir": "M:/NASA/NESDIS_CONUS-West",
                          "ciramapdir": "M:/NASA/CIRA_Cali-FD/map",
                          "ciramapts": "20181115150038",
                          "dest": "M:/NASA/Storm201911",
                          "goes": "17",
                          "sat": goes17,
                          "res": "1k",
                          "sector": "CONUS",
                          "interestArea": [-152.0, 30.0, -110.0, 50.0], # lat/long of ll, ur corner of Surface Analysis
                          "mercator": proj_anti_mercator,
                          "logos": [],
                          "starttime": "201911250000",
                          "sfcanalysisArea": (0, 0, 1280, 720),
                          "crop": (0, 0, 1280, 720),
                          "oFormat": "jpg",
                          "oRes": (1280, 720),
}

regions["CaliCoast"] = {"sfc": None,
                        "nesdisdir": "M:/NASA/NESDIS_CONUS-West",
                        "dest": "M:/NASA/CaliCoast",
                        "goes": "17",
                        "sat": goes17,
                        "res": "1k",
                        "sector": "CONUS",
                        "interestArea": [-127.5, 32.2, -115.0, 38.5],
                        "mercator": proj_anti_mercator,
                        "logos": [{"fn": "cira18Logo.png"}, {"fn": "GOES-S-Mission-Logo-1024x655.png"}],
                        "logopos": "left",
                        "tz": "PST",
                        "starttime": "201912071900",
                        "endtime": "201912121900",
                        "sfcanalysisArea": (0, 0, 1280, 720),
                        "crop": (0, 0, 1280, 720),
                        "oFormat": "jpg",
                        "oRes": (1280, 720),
}

regions["pacific2"] = {"sfc": "M:/NOAA/OPC/pacific",
                       "ciradir": "M:/NASA/CIRA_GOES-West/composite",
                       "ciramapdir": "M:/NASA/CIRA_GOES-West/map",
                       "ciramapts": "20181115150038",
                       "nesdisdir": "M:/NASA/NESDIS_GOES-West",
                       "dest": "M:/NASA/Overlay-Pacific",
                       "starttime": "201812240300",
                       "goes": "17",
                       "sat": goes17,
                       "res": "2k",
                       "sector": "FD",
                       "interestArea": [-225, 16, -115, 65], # lat/long of ur, ll corner of Surface Analysis
                       "mercator": proj_anti_mercator,
                       "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                       "crop": (2441-2160, 1488-1215, 2441, 1488),
                       "logos": [{"fn": "rammb_logo.png"}, {"fn": "cira18Logo.png"}, {"fn": "GOES-S-Mission-Logo-1024x655.png"}, {"fn": "NOAA_logo.png"}, {"fn": "NWS_logo.png"}],
                       "logopos": "left",
                       "oFormat": "jpg",
                       "oRes": (1280, 720),
}

regions["Atlantic2"] = {"sfc": "M:/NOAA/OPC/atlantic",
                        "ciradir": "M:/NASA/CIRA_GOES-East/composite",
                        "ciramapdir": "M:/NASA/CIRA_GOES-East/map",
                        "ciramapts": "20171201000000",
                        "nesdisdir": "M:/NASA/NESDIS_GOES-East",
                        "dest": "M:/NASA/Overlay-Atlantic2",
                        "starttime": "201911300000",
                        "goes": "16",
                        "sat": goes16,
                        "res": "2k",
                        "sector": "FD",
                        "mercator": proj_mercator,
                        "interestArea": [-100, 16, 10, 65], # lat/long of lr, ul corner of Surface Analysis
                        "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                        #"crop": (2441-2160, 1488-1215, 2441, 1488)
                        "crop": (0, 135, 2441-240, 1556), # source is 2441x1556
                        "logos": [{"fn": "rammb_logo.png"}, {"fn": "cira18Logo.png"}, {"fn": "goesRDecalSmall.png"}, {"fn": "NOAA_logo.png"}, {"fn": "NWS_logo.png"}],
                        "logopos": "left",
                        "oFormat": "jpg",
                        #"oRes": (1920, 1080),
                        "oRes": (1280, 720),
}

regions["SEStorm201912"] = {"sfc": None,
                            "nesdisdir": "M:/NASA/NESDIS_CONUS-East",
                            "dest": "M:/NASA/2019-12_SEStorm",
                            "goes": "16",
                            "sat": goes16,
                            "res": "1k",
                            "sector": "CONUS",
                            "interestArea": [-108.25, 23.5, -72.75, 42.5], # lat/long of ll, ur corner of Surface Analysis
                            "mercator": proj_mercator,
                            "logos": [{"fn": "cira18Logo.png"}, {"fn": "goesRDecalSmall.png"}],
                            "logopos": "left",
                            "tz": "EST",
                            "starttime": "201912131600",
                            "sfcanalysisArea": (0, 0, 1280, 720),
                            "crop": (0, 0, 1280, 720),
                            "oFormat": "jpg",
                            "oRes": (1280, 720),
}

overlaymap = None # If there's a transparent map to overlay

replace_png = False

# Give credit to the main organizations providing data
logos = []
logoheight = 96
def preplogos(region):
    global logos
    r = regions[region]
    logos = r["logos"]
    w, h = r['oRes']
    global logoheight
    logoheight = 96 if h > 1000 else 64
    for l in logos:
        img = Image.open(l["fn"])
        l["img"] = img.resize((int(img.width * (float(logoheight) / img.height)), logoheight), Image.ANTIALIAS)

def findsfc(region):
    path = regions[region]["sfc"]
    if not path:
        return None
    l = os.listdir(path)
    maps = []
    pat = re.compile("(\d\d\d\d\d\d\d\d\d\d\d\d).png")
    for e in l:
        m =  pat.match(e)
        if m:
            f = m.group(1)
            if (regions[region]["starttime"] <= f):
                maps.append(e)
            if 'endtime' in regions[region] and regions[region]['endtime'] < f:
                break
    maps.sort()
    return(maps)

def findcira(region):
    r = regions[region]
    path = r["ciradir"]
    d = os.listdir(path)
    cirapat = re.compile("GOES-%s_%s_(\d{12}).png$" % (r["goes"], r["sector"]))
    cira = []
    for date in d:
        datedir = "%s/%s" % (path, date)
        if not os.path.isdir(datedir):
            continue
        l = os.listdir(datedir)
        for e in l:
            m =  cirapat.match(e)
            if m:
                f = m.group(1)
                if (regions[region]["starttime"] <= f):
                    #logging.debug("CIRA image %s" % (m.group(1)))
                    cira.append("%s/%s/%s" % (path, date, e))
                if 'endtime' in regions[region] and regions[region]['endtime'] < f:
                    break
    cira.sort()
    logging.info("Found %d CIRA images" % (len(cira)))
    return(cira)

def findnesdis(region):
    r = regions[region]
    path = r["nesdisdir"]
    d = os.listdir(path)
    nesdispat = re.compile("GOES-%s_%s_(\d{12}).jpg$" % (r["goes"], r["sector"]))
    nesdis = []
    for date in d:
        datedir = "%s/%s" % (path, date)
        if not os.path.isdir(datedir):
            continue
        l = os.listdir(datedir)
        for e in l:
            m =  nesdispat.match(e)
            if m:
                f = m.group(1)
                if (regions[region]["starttime"] <= f):
                    nesdis.append("%s/%s/%s" % (path, date, e))
                if 'endtime' in regions[region] and regions[region]['endtime'] < f:
                    break
    nesdis.sort()
    logging.info("Found %d NESDIS images" % (len(nesdis)))
    return(nesdis)

def mergeciraandnesdis(cira, nesdis):
    # Mergesort with CIRA first if two images have same timestamp
    cira.sort()
    nesdis.sort()
    l = []
    c = 0
    n = 0
    while (c < len(cira)) or (n < len(nesdis)):
        if c == len(cira):
            l.append(nesdis[n])
            n += 1
        elif n == len(nesdis):
            l.append(cira[c])
            c += 1
        elif cira[c][:-4] <= nesdis[n][:-4]:
            l.append(cira[c])
            c += 1
        else:
            l.append(nesdis[n])
            n += 1
    return(l)

def prepsfc(region, fn):
    # Crop the sfc analysis to the map, make white pixels transparent, turn 'black' pixels white
    img = Image.open(fn).convert('RGBA')
    crop = img.crop(regions[region]["sfcanalysisArea"])
    pix = crop.getdata()

    sfc2 = []
    for p in pix:
        if p[0] == 255 and p[1] == 255 and p[2] == 255:
            sfc2.append((255, 255, 255, 0))
        elif p[0] == 0 and p[1] <= 30 and p[2] <= 35: # In the image black isn't quite black
            sfc2.append((255, 255, 255, 1))
        else:
            sfc2.append((p[0], p[1], p[2], 1))

    crop.putdata(sfc2)
    return crop

# cp GOES-17_baseline.png.aux.xml ${GOES}.aux.xml
# gdalwarp --config CENTER_LONG -180 -t_srs "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over" -te -225 16 -115 65 -te_srs EPSG:4326 -wo SOURCE_EXTRA=1000 ${GOES} -overwrite GOES-17_3395.tif  -ts 2441 1556

def goeswarp(region, fn):
    r = regions[region]
    jpg = (fn[-4:] == ".jpg")

    src = gdal.Open(fn, gdal.GA_ReadOnly)
    src.SetProjection(r["WKT"])
    src.SetGeoTransform(r["geotransform"])

    warpOptions = gdal.WarpOptions(
        format="MEM",
        width=r["sfcanalysisArea"][2] - r["sfcanalysisArea"][0],
        height=r["sfcanalysisArea"][3] - r["sfcanalysisArea"][1],
        outputBounds= r["interestArea"],
        outputBoundsSRS="EPSG:4326", # WGS84 - Allows use of lat/lon outputBounds
        # Setting GDAL_PAM_ENABLED should suppress sidecar emission, but it doesn't
        # warpOptions=["SOURCE_EXTRA=1000", "GDAL_PAM_ENABLED=FALSE", "GDAL_PAM_ENABLED=NO"],
        warpOptions=["SOURCE_EXTRA=500"],
        dstSRS = r["mercator"],
        multithread = True,
        )

    if False:
        logging.debug("Driver: {}/{}".format(src.GetDriver().ShortName,
                                             src.GetDriver().LongName))
        logging.debug("Size is {} x {} x {}".format(src.RasterXSize,
                                                    src.RasterYSize,
                                                    src.RasterCount))
        logging.debug("Projection is {}".format(src.GetProjection()))

        geotransform = src.GetGeoTransform()
        if geotransform:
            logging.debug("Origin = ({}, {})".format(geotransform[0], geotransform[3]))
            logging.debug("Pixel Size = ({}, {})".format(geotransform[1], geotransform[5]))
        
    logging.debug("Warping %s" % (fn))
    dst = gdal.Warp('', src, options=warpOptions)
    if not dst:
        logging.info("Warp failed %s" % (fn))
        img = None
    else:
        dsta = dst.ReadAsArray() # Array shape is [band, row, col]
        arr = dsta.transpose(1, 2, 0) # Virtually change the shape to [row, col, band]
        if jpg:
            rgb = Image.fromarray(arr, 'RGB')
            img = rgb.convert("RGBA")
        else:
            img = Image.fromarray(arr, 'RGBA') # fromarray() now reads linearly in RGBA order

    src = None
    dst = None
    dsta = None
    arr = None

    # A side effect of setting dst to None is that the sidecar is emmitted when dst is "closed"
    sidecar = "%s.aux.xml" % (fn)
    if os.path.isfile(sidecar): # Would be neat to figure out how to supress sidecar emission
        os.unlink(sidecar)

    return(img)

def prepmap(region):
    global overlaymap
    mapts = regions[region]["ciramapts"]
    mapdir = regions[region]["ciramapdir"]
    mapfn = "%s/%s/%s" % (mapdir, mapts, "map.png")
    if not os.path.isfile(mapfn):
        return
    logging.info("Using map %s" % (mapfn))
    overlaymap = goeswarp(region, mapfn)
    #overlaymap.save("%s/%s/%s" % (mapdir, mapts, "map-warp.png"))

ttfont = None
ttwidth, ttheight = (None, None)
def prepFont(region):
    r = regions[region]
    w, h = r['oRes']
    global ttfont, ttwidth, ttheight
    fontsize = 24 if h > 1000 else 16
    ttfont = ImageFont.truetype("lucon.ttf", fontsize) # lucida console - cour.ttf is ugly
    # getsize() returns for actual string, so figure out the greatest possible font height
    ttwidth, ttheight = ttfont.getsize("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+[{]}\|;:',<.>/?")

def decorate(img, region, goestime, sfctime):
    r = regions[region]
    goes = r["goes"]
    year = goestime[0:4]
    month = goestime[4:6]
    day = goestime[6:8]
    hour = goestime[8:10]
    minute = goestime[10:12]

    canvas = Image.new('RGBA', img.size, (255,255,255,0))
    draw = ImageDraw.Draw(canvas)

    tsstring = " GOES-%s %s-%s-%s %s:%sZ " % (goes, year, month, day, hour, minute)
    if "tz" in r:
        tz = r['tz']
        ts = datetime.datetime(year=int(year), month=int(month), day=int(day), hour=int(hour), minute=int(minute))
        offset = datetime.timedelta(hours=timezones[tz])
        ts = ts + offset
        tsstring = "GOES-%s %s%s" % (goes, ts.strftime("%Y-%m-%d %H:%M"), tz)
    x = 4
    y = 8
    ypad = 2
    w, h = ttfont.getsize(tsstring)
    draw.rectangle((x, y, x+w, y+ttheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
    draw.text((x, y+ypad), tsstring, fill=(0xff, 0xff, 0xff, 0xff), font=ttfont)
    # print ("x%d y%d w%d h%d ypad%d ttheight%d" % (x, y, w, h, ypad, ttheight))

    if (goes == "17") and ((int(year) < 2019) or
                           ((int(year) == 2019) and ((int(month) < 2) or
                                                     ((int(month) == 2) and ((int(day) < 12) or
                                                                             ((int(day) == 12) and (int(hour) < 6))))))):
        # GOES-17 was declared operational on the 12th, but NASA didn't say exactly when. 6GMT is about midnight Eastern
        wstring = " GOES-17 Preliminary, Non-Operational Data "
        w, h = ttfont.getsize(wstring)
        x = img.width - (w + x)
        draw.rectangle((x, y, x+w, y+ttheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
        draw.text((x, y+ypad), wstring, fill=(0xff, 0xff, 0xff, 0xff), font=ttfont)
        # print ("x%d y%d w%d h%d ypad%d ttheight%d" % (x, y, w, h, ypad, ttheight))

    if sfctime:
        year = sfctime[0:4]
        month = sfctime[4:6]
        day = sfctime[6:8]
        hour = sfctime[8:10]
        minute = sfctime[10:12]

        x = 4
        y = y+ttheight+ypad+ypad
        tsstring = " NOAA OPC Sfc Analysis %s-%s-%s %s:%sZ " % (year, month, day, hour, minute)
        w, h = ttfont.getsize(tsstring)
        draw.rectangle((x, y, x+w, y+ttheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
        draw.text((x, y+ypad), tsstring, fill=(0xff, 0xff, 0xff, 0xff), font=ttfont)

    if len(logos) != 0:
        logospacing = 4
        logomargin = 8
        logoleft = 'logopos' in r and r['logopos'] == 'left'
        #logoleft = (goes == "17")
        if logoleft:
            x = logomargin
            y = img.height - (logoheight + logomargin)
            for l in logos:
                img.paste(l["img"], (x, y), l["img"])
                x = x + l["img"].width + logospacing
        else:
            x = img.width - logomargin
            y = img.height - (logoheight + logomargin)
            for l in logos:
                x = x - (l["img"].width)
                # print("ciralogo %dx%d @ %d, %d" % (rammblogo.width, rammblogo.height, x, y))
                img.paste(l["img"], (x, y), l["img"])
                x = x - logospacing
        
        # afont = ImageFont.truetype("times.ttf", 24)
        text = " Image Credits "
        w, h = ttfont.getsize(text)
        y = img.height - (logoheight + h + logomargin + logospacing + ypad + ypad)
        if logoleft:
            x = logomargin
        else:
            x = img.width - (w + logomargin)
        
        # print("image credit %dx%d @ %d, %d" % (w, h, x, y))
        draw.rectangle((x, y, x+w, y+h), fill=(0,0,0,0x80))
        draw.text((x,y+ypad), text, fill=(255,255,255,255), font=ttfont)

    img = Image.alpha_composite(img, canvas)
    del draw
    return img

# Raster location through geotransform (affine) array [upperleftx, scalex, skewx, upperlefty, skewy, scaley]
# Upperleftx, upperlefty = distance from center (satellite nadir) in meters
# Scale = size of one pixel in units of raster projection in meters
def prepGeometry(region):
    r = regions[region]
    s = r['sat']

    # GDAL affine transformation
    # Compute upper left corner and resolution (per pixel) in Geostationary coordinates
    # for the image that might be a partial tiling of the entire image
    res = s[r['res']]
    sector = res[r['sector']]
    upper_left_x = (sector['x_offset'] + (0 if not 'tilesize' in r else (r['tilesize'] * r['hoffset'] * res['resolution']))) * s['p_height']
    upper_left_y = (sector['y_offset'] - (0 if not 'tilesize' in r else (r['tilesize'] * r['voffset'] * res['resolution']))) * s['p_height']
    resolution_m = res['resolution'] * s['p_height']
    r['geotransform'] = [upper_left_x, resolution_m, 0, upper_left_y, 0, -resolution_m]

    # Y resolution is negative because scan order goes from top (positive) towards bottom (negative)
    logging.debug("GeoTransform [%f, %f, %f, %f, %f, %f]" % (upper_left_x, resolution_m, 0, upper_left_y, 0, -resolution_m))

    # Proj projection geometry
    r["proj"]= "+proj=geos +lon_0=%f +h=%f +a=%f +b=%f +f=%f +units=m +no_defs -ellps=GRS80 +sweep=%s +over" % (
        s['longitude'], s['p_height'], s['semi_major'], s['semi_minor'], 1/s['flattening'], s['sweep_axis'])
    # Well Known Text (includes Proj description)
    r['WKT'] = 'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",%f,%f]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Geostationary_Satellite"],PARAMETER["central_meridian",%f],PARAMETER["satellite_height",%f],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1],EXTENSION["PROJ4","%s"]]' % (s['semi_major'], s['flattening'], s['longitude'], s['p_height'], r['proj'])
    logging.debug("Proj %s" % (r['proj']))
    logging.debug("WKT %s" % (r['WKT']))
    

def overlay(region, usecira, usenesdis, requiremap):
    r = regions[region]

    logging.info("Start region %s" % (region))
    prepFont(region)
    prepGeometry(region)
    
    if usecira and 'ciramapdir' in r:
        prepmap(region)
    preplogos(region)
    ddir = regions[region]["dest"]
    if not os.path.isdir(ddir):
        os.makedirs(ddir)

    sfcs = findsfc(region)
    images = []
    if usecira and usenesdis:
        images = mergeciraandnesdis(findcira(region), findnesdis(region))
    elif usecira:
        images = findcira(region)
    elif usenesdis:
        images = findnesdis(region)
    goes = r["goes"]
    sector = r['sector']

    sfcpat = re.compile("(\d{12}).png")
    cirapat = re.compile("GOES-%s_%s_(\d{12}).png$" % (goes, sector))
    nesdispat = re.compile("GOES-%s_%s_(\d{12}).jpg$" % (goes, sector))
    sfc = -1
    sfcdate = None

    skipping = 0

    for image in range(len(images)):
        processts = datetime.datetime.now(datetime.timezone.utc)
        goesfn = images[image]
        # Prefer using CIRA images over NESDIS
        m = None
        if usecira:
            m = cirapat.search(goesfn)
        if not m and usenesdis:
            m = nesdispat.search(goesfn)
        if not m:
            logging.info("Skipping (no source)  #%d %s" % (image, goesfn))
            continue

        goesdate = m.group(1)
        st = time.strptime(goesdate,"%Y%m%d%H%M")
        
        goests = int(time.mktime(st))

        jpgdestfn = "%s/%s/%s.%s" % (ddir, goesdate[0:8], goesdate, "jpg")
        pngdestfn = "%s/%s/%s.%s" % (ddir, goesdate[0:8], goesdate, "png")
        if os.path.isfile(jpgdestfn) and r['oFormat'] == "jpg":
            if skipping == 0:
                logging.info("Start skipping (exists) #%d %s" % (image, goesfn))
            skipping += 1
            logging.debug("Exists (jpg): %s" % (jpgdestfn))
            continue
        if os.path.isfile(pngdestfn) and (r['oFormat'] == "png" or not replace_png):
            if skipping == 0:
                logging.info("Start skipping (exists) #%d %s" % (image, goesfn))
            skipping += 1
            logging.debug("Exists (png): %s" % (pngdestfn))
            continue

        if skipping != 0:
            logging.info("Skipped %d images" % (skipping))
        skipping = 0
        logging.info("Image #%d: %s" % (image, goesfn))
        
        destfn = jpgdestfn if r['oFormat'] == "jpg" else pngdestfn
        logging.debug("Process -> %s" % (destfn))

        if sfcs != None:
            # if the next sfc is closer to the image time, switch to it
            if (sfc == -1) or ((nextsfcts != -1) and (abs(goests - nextsfcts) < abs(goests - sfcts))):
                while (sfc == -1) or ((nextsfcts != -1) and (abs(goests - nextsfcts) < abs(goests - sfcts))):
                    # Advance to next sfc analysis
                    sfc += 1
                    sfcfn = sfcs[sfc]
                    m = sfcpat.match(sfcfn)
                    sfcdate = m.group(1) + "00"
                    st = time.strptime(sfcdate,"%Y%m%d%H%M%S")
                    sfcts = int(time.mktime(st))
                    if (sfc+1 < len(sfcs)):
                        m = sfcpat.match(sfcs[sfc+1])
                        st = time.strptime(m.group(1) + "00","%Y%m%d%H%M%S")
                        nextsfcts = int(time.mktime(st))
                    else:
                        nextsfcts = -1
                logging.info("Advance to map #%03d: %s" % (sfc, sfcfn))
                sfcmap =  prepsfc(region, "%s/%s" % (r["sfc"], sfcfn))
                #sfcnp = np.array(sfcmap, dtype=float)
                #sfcnp[:,:,3] /= 255.0 # Scale alpha channel from[0..255] to [0..1]

            time2valid = abs(sfcts - goests)
            fadetime = 3 * 60 * 60 # three hours - half of 6 hours between updates
            fademax = 255 # 100% opaque
            fademin = 128 # 50% opaque at minimum
            if (time2valid > fadetime) and requiremap: # skip if there's no map and we need one
                logging.warning("Skipping (no sfc) %d (%d): %s %s" % (time2valid, fadetime, sfcfn, goesfn))
                continue
            
        goes = goeswarp(region, goesfn)
        if not goes:
            logging.info("Warp failed %s" % (goesfn))
            continue

        # Overlay the warped map if it exists
        if overlaymap != None:
            goes = Image.alpha_composite(goes, overlaymap)

        if sfcs != None:
            if time2valid <= fadetime: # fade in / out over fade time
                # Fade the map alpha channel based on how long to valid time
                opacity = int(round(((fadetime - time2valid) * (fademax-fademin)) / fadetime)) + fademin
                logging.debug("Fade %s %d%% (%d)" % (goesfn, (opacity*100/255), opacity))

                sfcnp = np.array(sfcmap)
                sfcnp[:,:,3] *= opacity
                #d = np.copy(sfcnp) # a copy of the surface analysis
                #d[:,:,3] *= opacity # Scale alpha channel to [0,opacity]
                overlay = Image.fromarray(sfcnp, mode="RGBA")
                goes.paste(overlay, None, overlay) # use overlay as its own mask
            else:
                logging.info("No overlay %d (%d): %s %s" % (time2valid, fadetime, sfcfn, goesfn))

        #goes.save("tst.png", "PNG")
        crop = goes.crop(r["crop"])
        resize = crop.resize(r['oRes'], Image.LANCZOS)

        img = decorate(resize, region, goesdate, sfcdate)

        logging.debug("Save %s" % (destfn))
        destdir = "%s/%s" % (ddir, goesdate[0:8])
        if not os.path.isdir(destdir):
            os.makedirs(destdir)
        if destfn[-4:] == ".jpg":
            img = img.convert("RGB")
            img.save(destfn, "JPEG")
            # Kludge for when replacing .png w/ .jpg - remove this at some point
            pngfn = destfn[:-4] + ".png"
            if os.path.exists(pngfn):
                logging.info("Unlink %s" % (pngfn))
                os.unlink(pngfn)
        else:
            img.save(destfn, "PNG")

        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = now - processts
        
        logging.info("Created (%ss) %s" % ("%s%02d.%02d" % (("" if elapsed.seconds < 60 else str(elapsed.seconds//60) + ":"), (elapsed.seconds%60), elapsed.microseconds/10000), destfn))
        
        #sys.exit(0)

if __name__ == '__main__':
    #os.environ['GDAL_PAM_ENABLED'] = 'NO' # Should be settable in warpOptions - this breaks warping
    #os.environ['CPL_DEBUG'] = 'ON' # GDAL option to turn on debuging info
    loglevel = logging.DEBUG
    parser = argparse.ArgumentParser()
    parser.add_argument("-atlantic", default=False, action='store_true', help="GOES-16 North Atlantic")
    parser.add_argument("-pacific", default=False, action='store_true', help="GOES-17 North Pacific")
    parser.add_argument("-cali", default=False, action='store_true', help="GOES-17 California Coast")
    parser.add_argument("-cali2", default=False, action='store_true', help="GOES-17 California Coast")
    parser.add_argument("-dorian", default=False, action='store_true', help="Hurricane Dorian")
    parser.add_argument("-snowcal", default=False, action='store_true', help="GOES-17 PACUS West Coast Storms")
    parser.add_argument("-storm", default=False, action='store_true', help="GOES-17 PACUS West Coast Storms")
    parser.add_argument("-sestorm", default=False, action='store_true', help="GOES-17 CONUS East Coast Storm")
    parser.add_argument("-eddy", default=False, action='store_true', help="GOES-17 PACUS Catalina Eddy")
    parser.add_argument("-coast", default=False, action='store_true', help="GOES-17 PACUS California SF-SD Coast")
    parser.add_argument("-atlantic2", default=False, action='store_true', help="GOES-16 CONUS Test")
    parser.add_argument("-cira", default=False, action='store_true', help="Use RAMMB/CIRA (png) images")
    parser.add_argument("-nesdis", default=False, action='store_true', help="Use NESDIS (jpeg) images")
    parser.add_argument("-norequiremap", default=False, action='store_true', help="Don't require a map (crop and resize only)")
    parser.add_argument("-log", choices=["debug", "info", "warning", "error", "critical"], default="info", help="Log level")
    parser.add_argument("-replacepng", default=False, action='store_true', help="Replace PNG files by reprojecting to JPG")
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

    replace_png = args.replacepng

    if args.atlantic:
        overlay("Atlantic", args.cira, args.nesdis, not args.norequiremap)
    if args.pacific:
        overlay("Pacific", args.cira, args.nesdis, not args.norequiremap)
    if args.cali:
        overlay("Cali_01", args.cira, args.nesdis, not args.norequiremap)
    if args.cali2:
        overlay("Cali_02", args.cira, args.nesdis, not args.norequiremap)
    if args.coast:
        overlay("CaliCoast", args.cira, args.nesdis, not args.norequiremap)
    if args.dorian:
        overlay("Dorian", args.cira, args.nesdis, not args.norequiremap)
    if args.snowcal:
        overlay("Snowcal", args.cira, args.nesdis, not args.norequiremap)
    if args.storm:
        overlay("Storm201911", args.cira, args.nesdis, not args.norequiremap)
    if args.sestorm:
        overlay("SEStorm201912", args.cira, args.nesdis, not args.norequiremap)
    if args.atlantic2:
        overlay("Atlantic2", args.cira, args.nesdis, not args.norequiremap)
