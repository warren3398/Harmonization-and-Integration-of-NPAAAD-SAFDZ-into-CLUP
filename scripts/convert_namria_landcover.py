"""Convert NAMRIA 2025 regional Land Cover files to R2-ready provincial GeoPackages."""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import geopandas as gpd
import pyogrio
import shapely

CATALOG_URL = "https://almed-gis-portal.onrender.com/api/r2/datasets"
CLASS_FIELDS = ("LANDCOVER", "LAND_COVER", "LC_CLASS", "LC2025", "LC_2025", "LC_TYPE",
                "LC_DESC", "LC_DESCRIP", "CLASS", "DESCRIPT", "DESCRIPTION")
PROVINCE_FIELDS = ("ADM2_EN", "province", "PROV_NAME", "shapeName", "NAME_1")


def canonical(name):
    key = re.sub("[^a-z0-9]", "", str(name).casefold())
    aliases = {"davaodeoro": "compostelavalley", "dinagatisland": "dinagatislands",
               "westernsamar": "samar", "basilang": "basilan", "cotabato": "northcotabato",
               "metromanila": "metropolitanmanila", "ncr": "metropolitanmanila"}
    return aliases.get(key, key)


def reference_catalog():
    with urlopen(CATALOG_URL, timeout=25) as response:
        rows = json.load(response)["datasets"]
    result = {}
    for row in rows:
        if row["layer"] in {"npaaad", "safdz"}:
            island, province = row["island"], row["province"]
            if re.fullmatch(r"luzon|visayas|mindanao", island) and re.fullmatch(r"[a-z0-9-]+", province):
                name = canonical(province)
                if name not in result or "-" in province:
                    result[name] = (island, province)
    if not result:
        raise ValueError("The Harmonizer reference catalog is empty.")
    return result


def source_candidates(path):
    if path.suffix.lower() != ".zip":
        return [str(path)]
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist()
                   if not name.startswith("__MACOSX/") and Path(name).suffix.lower() in {".shp", ".gpkg"}]
    if not members:
        raise ValueError(f"{path.name} has no directly readable GIS layers. Extract any inner ZIP to the input folder.")
    return [f"/vsizip/{{{path.resolve().as_posix()}}}/{name}" for name in members]


def select_layer(path, class_field):
    candidates = []
    for source in source_candidates(path):
        for layer, geometry_type in pyogrio.list_layers(source):
            if not geometry_type or "Polygon" not in geometry_type:
                continue
            info = pyogrio.read_info(source, layer=layer)
            fields = {name.upper(): name for name in info["fields"]}
            col = fields.get(class_field.upper()) if class_field else next(
                (fields[name] for name in CLASS_FIELDS if name in fields), None)
            if info["crs"] and col:
                candidates.append((source, layer, col, info["crs"]))
    if len(candidates) != 1:
        raise ValueError(f"{path.name}: found {len(candidates)} eligible polygon layers. "
                         "If needed, supply --class-field and place only the intended land-cover layer in the input folder.")
    return candidates[0]


def province_index(path, column, catalog):
    provinces = gpd.read_file(path)
    if provinces.empty or provinces.crs is None:
        raise ValueError("The province boundary file must have a readable CRS and polygon features.")
    if not column:
        column = next((name for name in PROVINCE_FIELDS if name in provinces.columns), None)
    if column not in provinces.columns:
        raise ValueError("Select the province-name field with --province-field.")
    provinces["partition"] = provinces[column].map(lambda value: catalog.get(canonical(value)))
    provinces = provinces.dropna(subset=["partition"]).copy()
    if provinces.empty:
        raise ValueError("No province names match the Harmonizer reference catalog.")
    provinces["geometry"] = shapely.make_valid(provinces.geometry.array)
    return provinces[["partition", "geometry"]].dissolve(by="partition", as_index=False)


def read_batches(path, layer, field, crs):
    with pyogrio.open_arrow(path, layer=layer, columns=[field], batch_size=512,
                            use_pyarrow=True) as (metadata, reader):
        geometry = metadata["geometry_name"] or "wkb_geometry"
        for chunk in reader:
            yield gpd.GeoDataFrame(
                {field: chunk[field].to_pylist()},
                geometry=gpd.GeoSeries.from_wkb(chunk[geometry].to_pylist(), crs=crs),
            )


def convert_source(source, provinces, stage, class_field):
    path, layer, field, crs = select_layer(source, class_field)
    local = provinces.to_crs(crs)
    offset = 0
    paths = set()
    for batch in read_batches(path, layer, field, crs):
        offset += len(batch)
        batch = batch.rename(columns={field: "LANDCOVER"})
        batch["LANDCOVER"] = batch.LANDCOVER.astype("string").str.strip()
        batch = batch[batch.LANDCOVER.notna() & batch.LANDCOVER.ne("")]
        batch = batch[~batch.geometry.is_empty & batch.geometry.notna()]
        batch["geometry"] = shapely.make_valid(batch.geometry.array)
        candidates = local.sindex.query(batch.geometry, predicate="intersects")
        for province_index in set(candidates[1]):
            area = local.iloc[province_index]
            island, province = area.partition
            selected = batch.iloc[candidates[0, candidates[1] == province_index]]
            clipped = gpd.clip(selected, area.geometry, keep_geom_type=True)
            clipped = clipped[clipped.geom_type.isin(["Polygon", "MultiPolygon"])]
            if clipped.empty:
                continue
            target = stage / "nationwide" / island / province / "landcover.gpkg"
            target.parent.mkdir(parents=True, exist_ok=True)
            clipped.to_crs(4326).to_file(target, layer="landcover", index=False, driver="GPKG",
                                        mode="a" if target.exists() else "w")
            paths.add(target)
        if offset % 8192 == 0:
            print(f"  {offset:,} features scanned", flush=True)
    if not paths:
        raise ValueError(f"{source.name}: no classified polygons intersect the indexed provinces.")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="Folder of NAMRIA ZIPs, shapefiles, or GeoPackages")
    parser.add_argument("boundary", nargs="?", type=Path, help="Philippines province polygon layer (ADM2)")
    parser.add_argument("output", nargs="?", type=Path, help="New output folder for R2 uploads")
    parser.add_argument("--class-field")
    parser.add_argument("--province-field")
    args = parser.parse_args()
    source = args.source or Path(input("Folder containing NAMRIA files: ").strip().strip('"'))
    boundary = args.boundary or Path(input("Province boundary file (ADM2 SHP, GPKG or GeoJSON): ").strip().strip('"'))
    output = args.output or Path(input("New output folder: ").strip().strip('"'))
    if not source.is_dir() or not boundary.is_file():
        raise ValueError("Choose an existing source folder and province boundary file.")
    sources = sorted(path for path in source.iterdir()
                     if path.is_file() and path != boundary and path.suffix.lower() in {".zip", ".shp", ".gpkg"})
    if not sources:
        raise ValueError("No NAMRIA ZIPs, shapefiles or GeoPackages were found.")
    if output.exists():
        raise ValueError("Choose a new output folder so earlier conversion results are preserved.")
    output.parent.mkdir(parents=True, exist_ok=True)
    catalog = reference_catalog()
    provinces = province_index(boundary, args.province_field, catalog)
    with TemporaryDirectory(dir=output.parent, prefix="landcover-conversion-") as temp:
        staging = Path(temp) / "output"
        paths = set()
        for path in sources:
            print(f"Reading {path.name}…", flush=True)
            paths.update(convert_source(path, provinces, staging, args.class_field))
        sizes = {path.relative_to(staging).as_posix(): path.stat().st_size for path in sorted(paths)}
        produced = {tuple(Path(path).parts[1:3]) for path in sizes}
        remaining = sorted(set(catalog.values()) - produced)
        (staging / "manifest.json").write_text(json.dumps({
            "source": "NAMRIA 2025 Land Cover Maps, Geoportal Philippines",
            "files": sizes,
            "total_bytes": sum(sizes.values()),
            "reference_provinces_without_output": ["/".join(key) for key in remaining],
        }, indent=2))
        staging.rename(output)
    print(f"Created {len(paths)} GeoPackages ({sum(sizes.values()) / 2**30:.2f} GiB) in {output}.")
    if remaining:
        print(f"{len(remaining)} catalog provinces had no output. Check manifest.json for details.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Conversion stopped: {error}", file=sys.stderr)
        sys.exit(1)
