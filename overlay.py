#!/usr/bin/python
import os
import re
import time
import subprocess
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFile

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

The Proj4 and GeoTransform numbers come from NASA's GOES Product User Guide Volume 3 (Level1b user
guide), pages 11-20.
https://www.goes-r.gov/users/docs/PUG-L1b-vol3.pdf

The geos projection specifies a Geostationary Satellite with a specific ellipsoid. AFAIK NASA/NOAA
 renormalize L1b data centering it over 137 degrees west even though GOES-17 is currently at 137.2W.
This allows warping to be pixel-perfect.

The GeoTransform parameters specify the dimenstions of the full-disk image in meters and the width
and height of the image pixel at nadir (directly underneath the satellite) in meters, in this case
just over 2km.

[ link to affine geometry page? ]

There are many ways to specify a projection to proj4. Using EPSG definitions is popular.
WGS84 =  EPSG:4326
Mercator =  EPSG:3395
Web Mercator =  EPSG:3857

The OPC chart is a Mercator projection. The GOES image is warped to Mercator (instead of both warped
to some other projection) to keep the text and annotations readable. Besides, many are familiar with
Mercator maps of the North Atlantic and Pacific.

The EPSG definition of Mercator (EPSG:3395) doesn't work with GDAL when crossing the anti-meridian.
Many workflows apparently process images and charts as East and West hemisphere halves and join them
later - this is both a hassle and distasteful. Instead use a Proj4 definition that allows specifying
ranges that cross the antimeridian with +over so our Pacific area is from -230 to -100 (aka 130E to 100W).
CENTER_LONG tells GDAL not to "go the other way" around the globe, centering on the antimerdian instead
of Greenwich.

+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over

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

# Pacific analysis is 2441 x 1556
# Crop to 2160 x 1215 (281, 258, 2441, 1338)
# Crop to 2080 x 1170 (2441-2080, 1388-1170, 2441, 1388)

# The first sfc is 2018-12-24_0600, so start 3 hours earlier for the first image
regions = {}
regions["pacific"] = {"sfc": "S:/NOAA/OPC/pacific",
                      "image": "S:/NASA/GOES-17_03_geocolor/composite",
                      "dest": "S:/NASA/GOES-17_03_geocolor/overlay",
                      "starttime": "201812240300",
                      "crop": (2441-2160, 1488-1215, 2441, 1488)}

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
    path = regions[region]["image"]
    d = os.listdir(path)
    image = []
    pat = re.compile("GOES-17_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d)(\d\d).png")
    for date in d:
        datedir = "%s/%s" % (path, date)
        if not os.path.isdir(datedir):
            continue
        l = os.listdir(datedir)
        for e in l:
            m =  pat.match(e)
            if m:
                f = m.group(1)
                if (regions[region]["starttime"] <= f):
                    image.append(e)
    image.sort()
    return(image)

def prepsfc(fn):
    # Crop the sfc analysis to the map, make white pixels transparent, turn 'black' pixels white
    img = Image.open(fn).convert('RGBA')
    crop = img.crop((0, 8, img.width, img.height - 36))
    pix = crop.getdata()

    sfc2 = []
    for p in pix:
        if p[0] == 255 and p[1] == 255 and p[2] == 255:
            sfc2.append((255, 255, 255, 0))
        elif p[0] == 0 and p[1] <= 30 and p[2] <= 35: # what NOAA uses for black?
            sfc2.append((255, 255, 255, 255))
        else:
            sfc2.append(p)

    crop.putdata(sfc2)
    return crop

# cp GOES-17_baseline.png.aux.xml ${GOES}.aux.xml
# gdalwarp --config CENTER_LONG -180 -t_srs "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over" -te -225 16 -115 65 -te_srs EPSG:4326 -wo SOURCE_EXTRA=1000 ${GOES} -overwrite GOES-17_3395.tif  -ts 2441 1556

proj4merc = "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over"
tmpfn = "tmp.tif"
def goeswarp(fn):
    cmd = "cp GOES-17_baseline.png.aux.xml %s.aux.xml" % (fn)
    subprocess.call(cmd)
    cmd = 'gdalwarp --config CENTER_LONG -180 -t_srs "+proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over" -te -225 16 -115 65 -te_srs EPSG:4326 -wo SOURCE_EXTRA=1000 -wo NUM_THREADS=4 %s -ts 2441 1556 -overwrite %s' % (fn, tmpfn)
    subprocess.call(cmd)
    cmd = "rm %s.aux.xml" % (fn)
    subprocess.call(cmd)
    warp = Image.open(tmpfn)
    return warp

def decorate(img, region, goestime, sfctime):
    if region == "atlantic":
        goes = "16"
    if region == "pacific":
        goes = "17"

    year = goestime[0:4]
    month = goestime[4:6]
    day = goestime[6:8]
    hour = goestime[8:10]
    minute = goestime[10:12]

    cfont = ImageFont.truetype("lucon.ttf", 24) # lucida console - cour.ttf is ugly
    # getsize() returns for actual string, so figure out the greatest possible font height
    x, fheight = cfont.getsize("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+[{]}\|;:',<.>/?")
    tsstring = " GOES-%s %s-%s-%s %s:%sZ " % (goes, year, month, day, hour, minute)
    x = 4
    y = 8
    ypad = 2
    w, h = cfont.getsize(tsstring)
    canvas = Image.new('RGBA', img.size, (255,255,255,0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((x, y, x+w, y+fheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
    draw.text((x, y+ypad), tsstring, fill=(0xff, 0xff, 0xff, 0xff), font=cfont)
    # print ("x%d y%d w%d h%d ypad%d fheight%d" % (x, y, w, h, ypad, fheight))

    if goes == "17":
        wstring = " GOES-17 Preliminary, Non-Operational Data "
        w, h = cfont.getsize(wstring)
        x = img.width - (w + x)
        draw.rectangle((x, y, x+w, y+fheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
        draw.text((x, y+ypad), wstring, fill=(0xff, 0xff, 0xff, 0xff), font=cfont)
        # print ("x%d y%d w%d h%d ypad%d fheight%d" % (x, y, w, h, ypad, fheight))

    year = sfctime[0:4]
    month = sfctime[4:6]
    day = sfctime[6:8]
    hour = sfctime[8:10]
    minute = sfctime[10:12]

    x = 4
    y = y+fheight+ypad+ypad
    tsstring = " NOAA OPC Sfc Analysis %s-%s-%s %s:%sZ " % (year, month, day, hour, minute)
    w, h = cfont.getsize(tsstring)
    draw.rectangle((x, y, x+w, y+fheight+ypad+ypad), fill=(0,0,0,0x80)) # Add some Y padding - X is padded w/ spaces
    draw.text((x, y+ypad), tsstring, fill=(0xff, 0xff, 0xff, 0xff), font=cfont)

    logospacing = 4
    logomargin = 8
    logoleft = True
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
    w, h = cfont.getsize(text)
    y = img.height - (logoheight + h + logomargin + logospacing + ypad + ypad)
    if logoleft:
        x = logomargin
    else:
        x = img.width - (w + logomargin)
        
    # print("image credit %dx%d @ %d, %d" % (w, h, x, y))
    draw.rectangle((x, y, x+w, y+h), fill=(0,0,0,0x80))
    draw.text((x,y+ypad), text, fill=(255,255,255,255), font=cfont)
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

    if region == "atlantic":
        goes = "16"
    if region == "pacific":
        goes = "17"

    sfcpat = re.compile("(\d\d\d\d\d\d\d\d\d\d\d\d).png")
    goespat = re.compile("GOES-%s_03_full_(\d\d\d\d\d\d\d\d\d\d\d\d)(\d\d).png" % (goes))
    sfc = -1

    for image in range(len(images)):
        logging.debug("Image #%d" % image)
        goesfn = images[image]
        m = goespat.match(goesfn)
        goesdate = m.group(1) + m.group(2)
        st = time.strptime(goesdate[0:14],"%Y%m%d%H%M%S")
        goests = int(time.mktime(st))

        destfn = "%s/%s.png" % (ddir,goesdate)
        if os.path.isfile(destfn):
            logging.info("Exists: %s" % (destfn))
            continue

        # if the next sfc is closer to the image time, switch to it
        if (sfc == -1) or ((nextsfcts != -1) and (abs(goests - nextsfcts) < abs(goests - sfcts))):
            while (sfc == -1) or ((nextsfcts != -1) and (abs(goests - nextsfcts) < abs(goests - sfcts))):
                # Advance to next sfc
                sfc += 1
                sfcfn = sfcs[sfc]
                logging.debug("Advance to map #%d: %s" % (sfc, sfcfn))
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
            sfcmap =  prepsfc("%s/%s" % (regions[region]["sfc"], sfcfn))

        path = regions[region]["image"]
        goes = goeswarp("%s/%s/%s" % (path, goesdate[0:8], goesfn))

        time2valid = abs(sfcts - goests)
        fadetime = 3 * 60 * 60 # three hours - half of 6 hours between updates
        fademin = 64 # about 25% opaque at minimum
        if time2valid < fadetime: # fade in / out over fade time
            # Make a mask based on how long to valid time
            opacity = int(((fadetime - time2valid) * (255-fademin)) / fadetime) + fademin
            logging.debug("Fade %s %d%% (%d)" % (goesfn, (opacity*100/255), opacity))
            # mask = Image.new("RGBA", (sfcmap.width, sfcmap.height), (0,0,0,opacity))

            if True:
                # This might be the slowest possible way to make the opacity alpha mask
                mask = sfcmap.copy()
                for y in range(mask.height):
                    for x in range(mask.width):
                        p = mask.getpixel((x, y))
                        if (p[3] == 255):
                            mask.putpixel((x, y), (p[0], p[1], p[2], opacity))
                goes.paste(mask, None, mask)

            if False:
                d = np.array(sfcmap)
                red, green, blue, alpha = d.T
                print alpha
                #overlayareas = (alpha == 255)
                #overlayareas = overlayareas * (opacity / 255)
                for y in range(alpha.width):
                    for x in range(alpha.height):
                        if (alpha[i] != 0):
                            alpha[i] = opacity
                            sfcmap.putalpha(alpha)
                goes.paste(sfcmap, None, sfcmap)
        else:
            logging.debug("No overlay %d (%d): %s %s" % (time2valid, fadetime, sfcfn, goesfn))

        logging.debug("Crop area (%d %d %d %d)" % (regions[region]["crop"][0],regions[region]["crop"][1],regions[region]["crop"][2],regions[region]["crop"][3]))
        crop = goes.crop(regions[region]["crop"])
        resize = crop.resize((1920, 1080), Image.LANCZOS)

        img = decorate(resize, region, goesdate, sfcdate)

        logging.info("Save %s" % (destfn))
        img.save(destfn, "PNG")

if __name__ == '__main__':
    loglevel = logging.DEBUG
    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=loglevel)
    
    overlay("pacific")