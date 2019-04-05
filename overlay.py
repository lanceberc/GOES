#!/usr/bin/python
import os
import sys
import re
import time
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

# The first sfc is 2018-12-24_0600, so start 3 hours earlier for the first image
regions = {}
regions["pacific"] = {"sfc": "M:/NOAA/OPC/pacific",
                      "image": "M:/NASA/GOES-17_03_geocolor/composite",
                      "dest": "M:/NASA/GOES-17_03_geocolor/overlay",
                      "starttime": "201812240300",
                      "goes": "17",
                      "WKT": 'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",6378169,298.2572221]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Geostationary_Satellite"],PARAMETER["central_meridian",-137],PARAMETER["satellite_height",35785831],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1],EXTENSION["PROJ4","+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027 +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-137 +over"]]',
                      "geos": "+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027 +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-137 +over",
                      # Raster location through geotransform (affine) array [upperleftx, scalex, skewx, upperlefty, skewy, scaley]
                      # Upperleftx, upperlefty = distance from center (satellite nadir) in meters
                      # Scale = size of one pixel in units of raster projection in meters
                      "geotransform": [-5434894.7009821739, 2004.0173154875411, 0.0, 5434894.7009821739, 0.0, -2004.0173154875411],
                      "interestArea": [-225, 16, -115, 65], # lat/long of ur, ll corner of Surface Analysis
                      "mercator": "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over",
                      "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                      "crop": (2441-2160, 1488-1215, 2441, 1488)}

regions["atlantic"] = {"sfc": "M:/NOAA/OPC/atlantic",
                       "image": "M:/NASA/GOES-16_03_geocolor/composite",
                       "dest": "M:/NASA/GOES-16_03_geocolor/overlay",
                       "starttime": "201901110000",
                       "goes": "16",
                       "WKT": 'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",6378169,298.2572221]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Geostationary_Satellite"],PARAMETER["central_meridian",-75],PARAMETER["satellite_height",35785831],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1],EXTENSION["PROJ4","+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027 +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-75 +over"]]',
                      "geos": "+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027 +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-75 +over",
                       # Raster location through geotransform (affine) array [upperleftx, scalex, skewx, upperlefty, skewy, scaley]
                       # Upperleftx, upperlefty = distance from center (satellite nadir) in meters
                       # Scale = size of one pixel in units of raster projection in meters
                       "geotransform": [-5434894.7009821739, 2004.0173154875411, 0.0, 5434894.7009821739, 0.0, -2004.0173154875411],
                       "mercator": "+proj=merc +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over",
                       "interestArea": [-100, 16, 10, 65], # lat/long of lr, ul corner of Surface Analysis
                       "sfcanalysisArea": (0, 8, 2441, 1564), # Cut off top 8 and bottom 36 pixels
                       #"crop": (2441-2160, 1488-1215, 2441, 1488)
                       "crop": (0, 135, 2441-240, 1556) # source is 2441x1556
}

outputRes = (1920, 1080) # HDTV

# Give credit to the main organizations providing data
logoheight = 96
logos = ({"fn": "rammb_logo.png"}, {"fn": "cira18Logo.png"}, {"fn": "goesRDecalSmall.png"}, {"fn": "NOAA_logo.png"}, {"fn": "NWS_logo.png"})
def preplogos(region):
    if region == "pacific":
        logos[2]["fn"] = "GOES-S-Mission-Logo-1024x655.png"
    for l in logos:
        img = Image.open(l["fn"])
        l["img"] = img.resize((int(img.width * (float(logoheight) / img.height)), logoheight), Image.ANTIALIAS)

def findsfc(region):
    path = regions[region]["sfc"]
    l = os.listdir(path)
    maps = []
    pat = re.compile("(\d\d\d\d\d\d\d\d\d\d\d\d).png")
    for e in l:
        m =  pat.match(e)
        if m:
            f = m.group(1)
            if (regions[region]["starttime"] <= f):
                maps.append(e)
    maps.sort()
    return(maps)

def findgoes(region):
    """ Find the CIRA png and NESDIS jpg images in the region. Return a list with CIRA images first since as PNGs
    they're better quality """
    path = regions[region]["image"]
    d = os.listdir(path)
    cirapat = re.compile("GOES-%s_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d)(\d\d).png$" % regions[region]["goes"])
    nesdispat = re.compile("GOES-%s_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d).jpg$" % regions[region]["goes"])
    cira = []
    nesdis = []
    for date in d:
        datedir = "%s/%s" % (path, date)
        if not os.path.isdir(datedir):
            continue
        l = os.listdir(datedir)
        for e in l:
            m =  cirapat.match(e)
            if m:
                logging.debug("CIRA image %s" % (m.group(1)))
                f = m.group(1)
                if (regions[region]["starttime"] <= f):
                    cira.append(e)
            m =  nesdispat.match(e)
            if m:
                logging.debug("NESDIS image %s" % (m.group(1)))
                f = m.group(1)
                if (regions[region]["starttime"] <= f):
                    nesdis.append(e)

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
        elif cira[c][:-6] <= nesdis[n][:-4]:
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
            sfc2.append((255, 255, 255, 255))
        else:
            sfc2.append(p)

    crop.putdata(sfc2)
    return crop

# cp GOES-17_baseline.png.aux.xml ${GOES}.aux.xml
# gdalwarp --config CENTER_LONG -180 -t_srs "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over" -te -225 16 -115 65 -te_srs EPSG:4326 -wo SOURCE_EXTRA=1000 ${GOES} -overwrite GOES-17_3395.tif  -ts 2441 1556

def goeswarp(region, fn):
    jpg = (fn[-4:] == ".jpg")

    reg = regions[region]
    src = gdal.Open(fn, gdal.GA_ReadOnly)
    src.SetProjection(reg["WKT"])
    src.SetGeoTransform(reg["geotransform"])

    warpOptions = gdal.WarpOptions(
        format="MEM",
        width=reg["sfcanalysisArea"][2] - reg["sfcanalysisArea"][0],
        height=reg["sfcanalysisArea"][3] - reg["sfcanalysisArea"][1],
        outputBounds= reg["interestArea"],
        outputBoundsSRS="EPSG:4326", # Allows use of lat/lon outputBounds
        # Setting GDAL_PAM_ENABLED should suppress sidecar emission, but it doesn't
        # warpOptions=["SOURCE_EXTRA=1000", "GDAL_PAM_ENABLED=FALSE", "GDAL_PAM_ENABLED=NO"],
        warpOptions=["SOURCE_EXTRA=500"],
        dstSRS = reg["mercator"],
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

ttfont = ImageFont.truetype("lucon.ttf", 24) # lucida console - cour.ttf is ugly
# getsize() returns for actual string, so figure out the greatest possible font height
ttwidth, ttheight = ttfont.getsize("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+[{]}\|;:',<.>/?")

def decorate(img, region, goestime, sfctime):
    goes = regions[region]["goes"]
    year = goestime[0:4]
    month = goestime[4:6]
    day = goestime[6:8]
    hour = goestime[8:10]
    minute = goestime[10:12]

    canvas = Image.new('RGBA', img.size, (255,255,255,0))
    draw = ImageDraw.Draw(canvas)

    tsstring = " GOES-%s %s-%s-%s %s:%sZ " % (goes, year, month, day, hour, minute)
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

    logospacing = 4
    logomargin = 8
    logoleft = (goes == "17")
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

def overlay(region):
    preplogos(region)
    ddir = regions[region]["dest"]
    if not os.path.isdir(ddir):
        os.makedirs(ddir)

    sfcs = findsfc(region)
    images = findgoes(region)
    goes = regions[region]["goes"]

    sfcpat = re.compile("(\d\d\d\d\d\d\d\d\d\d\d\d).png")
    cirapat = re.compile("GOES-%s_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d)(\d\d).png" % (goes))
    nesdispat = re.compile("GOES-%s_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d).jpg" % (goes))
    sfc = -1

    for image in range(len(images)):
        goesfn = images[image]
        logging.info("Image #%d: %s" % (image, goesfn))
        m = cirapat.match(goesfn)
        if not m:
            m = nesdispat.match(goesfn)
        goesdate = m.group(1) # Don't include seconds, just hours and minutes
        st = time.strptime(goesdate[0:12],"%Y%m%d%H%M")
        
        goests = int(time.mktime(st))

        destfn = "%s/%s.png" % (ddir,goesdate)
        if os.path.isfile(destfn):
            logging.debug("Exists: %s" % (destfn))
            continue

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
            sfcmap =  prepsfc(region, "%s/%s" % (regions[region]["sfc"], sfcfn))
            sfcnp = np.array(sfcmap)
            sfcnp[:,:,3] /= 255 # Scale alpha channel from[0..255] to [0..1]

        path = regions[region]["image"]
        goes = goeswarp(region, "%s/%s/%s" % (path, goesdate[0:8], goesfn))
        if not goes:
            logging.info("Warp failed %s" % (goesfn))
            continue

        time2valid = abs(sfcts - goests)
        fadetime = 3 * 60 * 60 # three hours - half of 6 hours between updates
        fademax = 255 # 100% opaque
        fademin = 64 # ~25% opaque at minimum
        if time2valid <= fadetime: # fade in / out over fade time
            # Fade the map alpha channel based on how long to valid time
            opacity = int(round(((fadetime - time2valid) * (fademax-fademin)) / fadetime)) + fademin
            logging.debug("Fade %s %d%% (%d)" % (goesfn, (opacity*100/255), opacity))

            d = np.copy(sfcnp) # a copy of the surface analysis
            d[:,:,3] *= opacity # Scale alpha channel to [0,opacity]
            overlay = Image.fromarray(d)
            goes.paste(overlay, None, overlay)
        else:
            logging.info("No overlay %d (%d): %s %s" % (time2valid, fadetime, sfcfn, goesfn))

        #goes.save("tst.png", "PNG")
        crop = goes.crop(regions[region]["crop"])
        resize = crop.resize(outputRes, Image.LANCZOS)

        img = decorate(resize, region, goesdate, sfcdate)

        logging.info("Save %s" % (destfn))
        img.save(destfn, "PNG")
        #sys.exit(0)

if __name__ == '__main__':
    #os.environ['GDAL_PAM_ENABLED'] = 'NO' # Should be settable in warpOptions - this breaks warping
    #os.environ['CPL_DEBUG'] = 'ON' # GDAL option to turn on debuging info
    loglevel = logging.DEBUG
    parser = argparse.ArgumentParser()
    parser.add_argument("-atlantic", default=False, action='store_true', help="GOES-16 North Atlantic")
    parser.add_argument("-pacific", default=False, action='store_true', help="GOES-17 North Pacific")
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

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)

    if args.atlantic:
        overlay("atlantic")
    if args.pacific:
        overlay("pacific")
