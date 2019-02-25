
#!/usr/bin/bash -x
while [ 1 -gt 0 ] ; do
    date ;
    python.exe ./nesdis-fetch.py -satellite 16 ;
    python.exe ./nesdis-fetch.py -satellite 17 ;
    python.exe ./geocolor-fetch.py -satellite 16 -resolution 1k -region conus -all ;
    python.exe ./geocolor-fetch.py -satellite 17 -resolution 1k -region conus -all ;
    python.exe ./geocolor-fetch.py -satellite 16 -resolution 1k -region full -all ;
    python.exe ./geocolor-fetch.py -satellite 17 -resolution 1k -region full -all ;
    ./sfcanalysis.py ;
    date ;
    sleep 600 ;
done
