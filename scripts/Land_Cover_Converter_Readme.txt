NAMRIA 2025 Land Cover Converter
================================
1. Extract this converter ZIP. On Windows with Python 3.10 or newer, open
   Run_Land_Cover_Converter.cmd. It installs the converter's dependencies
   in a separate environment on the first run.
2. Provide the folder containing the NAMRIA regional ZIPs and the official
   Philippines province boundary (ADM2) as a GeoPackage, GeoJSON or shapefile.
   Provide a new output folder on a drive with enough space for the result.
   The converter reads direct GIS members from ZIP files without extracting
   the full source. If a package has an inner ZIP, extract that inner ZIP
   as a file into the source folder and move the outer archive out.
3. When conversion finishes, upload the contents of the output's nationwide
   folder to the existing private npaaad-safdz-data bucket. Keep these keys:

     nationwide/<island>/<province>/landcover.gpkg

   Add each province's landcover.gpkg alongside its npaaad.gpkg and
   safdz.gpkg. Use the manifest to verify file counts and size. Existing
   NPAAAD and SAFDZ files are not part of the converter output.

The converter checks the current Harmonizer catalog to match province keys.
It preserves classified polygon geometry and class labels. Output size
depends on the original geometries and can differ from the ZIP sizes.
An internet connection is needed for the catalog and first-run installation.

If class or province field detection is ambiguous, run the converter from
a command prompt with the optional switches:

  Run_Land_Cover_Converter.cmd "source-folder" "ADM2.gpkg" "output" --class-field LC_CLASS --province-field ADM2_EN

Source data: NAMRIA 2025 Land Cover Maps, Geoportal Philippines.
https://geoportal.gov.ph/
