# GOES
Process images from NASA/NOAA GOES Weather Satellites

### Contents

This directory contains helper programs to:

* Fetch GOES GeoColored images from RAMMB / CIRA
* Fetch GOES GeoColored images from NESDIS
* Fetch Surface Analysis charts from NOAA / NCEP / OPC
* Process the images by reprojecting them and overlaying the surface analysis charts
* Process the overlays by creating a MP4 movie

### Examples
There are two examples for using GDAL and Cartopy for geolocating and warping GOES images. The principles work for other geosynchronous satellites.

* [GOES_GDAL.ipynb](GOES_GDAL.ipynb) uses GDAL and is pretty efficient.
* [GOES_Cartopy.ipynb](GOES_Cartopy.ipynb) uses Cartopy and Matplotlib and is less efficient.

### Workflow

* Create a repository of GOES images and OPC charts with `fetch.sh`
* `fetch.sh` uses `geocolor-fetch.py`, `nesdis-fetch.py` and `sfcanalysis.py` to retrieve from remote repositories
* `overlay.py` is the crux of the biscuit - it warps the GOES images and does the Surface Analysis overlay
* `cmovie.py` creates MPEG movies of the results

### Overlay Workflow

`overlay.py` leans on three standard libraries:

* **GDAL** does Geospatial manipulation of the GOES images including reprojection from GEOS to Mercator
* **numpy** is the standard Python library for array manipulation
* **Pillow** is a common Python library for image manipulation

### Manipulating GOES Images w/ GDAL, numpy and Pillow
It was very hard to find references / examples of using these three libraries together.
Getting them to work took a while and making them efficient took even longer.
I'm sure there are more easy optimizations possible.

#### Problems overcome
* Reprojecting GOES images

Reprojecting requires manually georeferencing the PNG/JPG images (they don't come as GeoTIFFs from these sources).
The WKT (Well Known Text) parameters for GOES-17 come from NASA's Product User Guide.

    <SRS>PROJCS["unnamed",GEOGCS["unnamed ellipse",
        DATUM["unknown",SPHEROID["unnamed",6378169,298.2572221]],
	PRIMEM["Greenwich",0],
	UNIT["degree",0.0174532925199433]],
        PROJECTION["Geostationary_Satellite"],
	PARAMETER["central_meridian",-137],
	PARAMETER["satellite_height",35785831],
	PARAMETER["false_easting",0],
	PARAMETER["false_northing",0],
	UNIT["Meter",1],
	EXTENSION["PROJ4","+proj=geos +h=35785831 +a=6378169 +b=6356583.8 +f=.00335281068119356027
            +units=m +no_defs -ellps=GRS80 +sweep=x +lon_0=-137 +over"]]
    </SRS>

The parameters for the affine GeoTransform also come from NASA's Guide - that two are negative was not obvious:

    <GeoTransform> -5434894.7009821739, 2004.0173154875411, 0.0, 5434894.7009821739, 0.0, -2004.0173154875411</GeoTransform>

GDAL can use Proj.4 projections defined by EPSG (European Petroleum Standards Group).
We should be able to use EPSG:3395 (Mercator), but it's defined in a way that prevents crossing the anti-meridian.
Instead we use a Proj.4 Mercator definition with +over to allow the Pacific projection to reach from Asia to North America.

    +proj=merc +lon_0=-180 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +over

Bounding box crop corners are defined with EPSG:4326 (the WGS84 Geoid) to allow defining with lat/lon pairs.

* Efficiently converting GOES images to Pillow image format

Converting the warped image to a Pillow format was taking a long time (more than 20 seconds per image).
We now use numpy's faster code as an intermediate format and processing is down to 5-6 seconds per image.
The trick is to use numpy's ability to virtually transpose axes to convert from GDAL's band-oriented [4,x,y]
orientation to Pillow's [x,y,4] RGBA format.

    dst = gdal.Warp('', src, options=warpOptions)
    dsta = dst.ReadAsArray() # Array shape is [band, row, col]
    arr = dsta.transpose(1, 2, 0) # Virtually change the shape to [row, col, band]
    if jpg:
        rgb = Image.fromarray(arr, 'RGB')
        img = rgb.convert("RGBA")
    else:
        img = Image.fromarray(arr, 'RGBA') # fromarray() now reads linearly in RGBA order

* Creating a transparent background for the Surface Analysis charts

The surface analysis charts needed a bit of cleaning.
1. Crop to the desired lat/lon box.
2. Turn the white background to transparent pixels for overlaying
3. Turn "black" pixels to white for contrast when overlaying.
The surprise is that "black" isn't black in the source, it's closer to a dark blue.

* Slowing down time-lapse movies

At 25 or 30 fps the weather features moved too fast.
We first tried slowing it with `"setpts=2*PTS"` but this replicated frames, making motion choppy.
the current approach is to specify 15fps which is slower but still smooth.

### References
* [CIRA / RAMMB SLIDER](http://rammb-slider.cira.colostate.edu) - Source of GeoColored PNGs
* [NOAA NESDIS](nesdis.noaa.gov) - Source of GeoColored JPGs (and other archived data)
* [NASA GOES-R Series Product Users Guide Volume 4](https://www.goes-r.gov/users/docs/PUG-GRB-vol4.pdf) - GOES image spatial specifications
* [NOAA OPC](https://opc.ncep.noaa.gov) - Ocean Prediction Center weather charts
* [Proj.4](https://proj4.org) - Geospatial coordinate transformation library
* [GDAL](https://www.gdal.org) - Geospatial Data Abstraction Library
* [numpy](http://www.numpy.org) - Python scientific computing library
* [Pillow](https://pillow.readthedocs.io/en/3.1.x/index.html) - A Python image manipulation library
