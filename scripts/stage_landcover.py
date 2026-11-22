"""Build one spatially indexed NAMRIA 2025 provincial GeoPackage from an approved source."""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pyogrio
import shapely


def stage(source, layer, class_field, province_boundary, target):
    info = pyogrio.read_info(source, layer=layer)
    if not info["crs"] or class_field not in info["fields"]:
        raise ValueError("The source needs a valid CRS and the specified land-cover class field.")
    area = gpd.read_file(province_boundary)
    if area.empty or area.crs is None or not area.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Provide the selected province as a polygon layer with a valid CRS.")
    boundary = area.to_crs(info["crs"]).geometry.make_valid().union_all()
    if boundary.is_empty or boundary.area <= 0:
        raise ValueError("The province boundary has no polygon area.")

    offset = 0
    while True:
        batch = pyogrio.read_dataframe(source, layer=layer, columns=[class_field], bbox=boundary.bounds,
                                       skip_features=offset, max_features=256)
        if batch.empty:
            break
        offset += len(batch)
        batch = batch.rename(columns={class_field: "LANDCOVER"})
        batch["LANDCOVER"] = batch.LANDCOVER.astype("string").str.strip()
        batch = batch[batch.LANDCOVER.notna() & batch.LANDCOVER.ne("")]
        batch["geometry"] = shapely.make_valid(batch.geometry.array)
        clipped = gpd.clip(batch, boundary, keep_geom_type=True)
        clipped = clipped[clipped.geom_type.isin(["Polygon", "MultiPolygon"])]
        if not clipped.empty:
            clipped.to_file(target, driver="GPKG", layer="landcover", index=False,
                            mode="a" if target.exists() else "w")
    if not target.exists():
        raise ValueError("The source had no classified land-cover polygons inside the province.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Extracted NAMRIA 2025 Shapefile or GeoPackage")
    parser.add_argument("boundary", help="Polygon shapefile, GeoPackage, or GeoJSON for one province")
    parser.add_argument("output", type=Path, help="Destination ending in landcover.gpkg")
    parser.add_argument("--class-field", required=True, help="Source field with the NAMRIA class labels")
    parser.add_argument("--layer", help="Name of the source land-cover layer in a multi-layer GeoPackage")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as staging:
        result = Path(staging) / "landcover.gpkg"
        stage(args.source, args.layer, args.class_field, args.boundary, result)
        os.replace(result, args.output)


if __name__ == "__main__":
    main()
