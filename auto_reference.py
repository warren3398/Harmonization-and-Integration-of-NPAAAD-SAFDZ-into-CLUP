"""Spatial reference detection. Search masks are never used as output geometry."""
import hashlib
import io
import json
import math
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(tempfile.gettempdir()) / 'harmonizer-auto-references'
TTL = 3 * 3600
LOCK = threading.Lock()
INDEX_URL = 'https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_PHL_1.json.zip'


def canonical(name):
    value = re.sub('[^a-z0-9]', '', name.lower())
    return {'davaodeoro':'compostelavalley', 'dinagatisland':'dinagatislands',
            'westernsamar':'samar', 'basilang':'basilan', 'cotabato':'northcotabato',
            'metromanila':'metropolitanmanila', 'maguindanaodelnorte':'maguindanao',
            'maguindanaodelsur':'maguindanao'}.get(value, value)


@lru_cache(maxsize=1)
def regions():
    # GADM 4.1 provincial masks only find candidate R2 keys. Buffer conservatively
    # for boundary differences. All outputs use exact source / ELU intersections.
    request = urllib.request.Request(INDEX_URL, headers={'User-Agent':'CLUP-Harmonizer/2.8'})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('Reference location index exceeds its expected size.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = next(name for name in archive.namelist() if name.endswith('.json'))
        with archive.open(name) as source:
            features = json.load(source)['features']
    if len(features) < 70:
        raise ValueError('The reference location index is incomplete. Retry detection shortly.')
    result = {canonical(f['properties']['NAME_1']):shape(f['geometry']).buffer(0.01) for f in features}
    # Conservative parent masks handle older GADM province divisions and cities.
    for child,parent in [('davaooccidental','davaodelsur'),('cotabatocity','maguindanao'),('isabelacity','basilan')]:
        result[child] = result[parent]
    return result


def boundary(elu):
    if elu.crs is None:
        raise ValueError('The ELU has no readable CRS. Include its correct .prj file for automatic detection.')
    if elu.empty or not elu.geom_type.isin(['Polygon','MultiPolygon']).all():
        raise ValueError('The ELU must contain polygon land use features.')
    frame = elu.to_crs(4326)
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise ValueError('The ELU contains empty geometries. Repair or remove them before uploading.')
    mask = unary_union([geom if geom.is_valid else shapely.make_valid(geom) for geom in frame.geometry])
    if mask.is_empty or mask.area <= 0 or not all(math.isfinite(n) for n in mask.bounds):
        raise ValueError('The ELU boundary has invalid coordinates. Check the original CRS.')
    if not (-180 <= mask.bounds[0] <= mask.bounds[2] <= 180 and -90 <= mask.bounds[1] <= mask.bounds[3] <= 90):
        raise ValueError('The ELU coordinates do not match its CRS. Check the .prj file.')
    return mask


def fingerprint(elu):
    return hashlib.sha256(shapely.normalize(boundary(elu)).wkb).hexdigest()


def clean_expired():
    ROOT.mkdir(parents=True, exist_ok=True)
    for path in ROOT.iterdir():
        if path.is_dir() and time.time() - path.stat().st_mtime > TTL:
            shutil.rmtree(path, ignore_errors=True)


def load(token, elu):
    if not re.fullmatch('[a-f0-9]{32}', str(token or '')):
        raise ValueError('Detect the reference datasets again before running harmonization.')
    path = ROOT / token
    try:
        meta = json.loads((path / 'metadata.json').read_text())
        if time.time() - meta['created'] > TTL:
            raise ValueError('The references expired. Upload the ELU again to refresh them.')
        if meta['boundary_hash'] != fingerprint(elu):
            raise ValueError('The ELU boundary changed. Detect and crop the reference files again.')
        if not all((path / (role+'.gpkg')).is_file() for role in ('npaaad','safdz')):
            raise FileNotFoundError()
        return path, meta
    except (FileNotFoundError, json.JSONDecodeError):
        raise ValueError('The references are unavailable after a restart. Upload the ELU again.')


def prepare(elu, rows, download, suggest_field, normalize):
    if not LOCK.acquire(blocking=False):
        raise ValueError('Another ELU reference detection is running. Please retry shortly.')
    folder = None
    try:
        clean_expired()
        mask = boundary(elu)
        masks = regions()
        pairs = {}
        for row in rows:
            name = canonical(row['province'])
            if name not in masks:
                continue  # Do not infer locations for ambiguous malformed aliases.
            group = pairs.setdefault((row['island'], name), {})
            role = row['layer']
            candidate = dict(row, key=f"nationwide/{row['island']}/{row['province']}/{role}.gpkg")
            if role not in group or ('-' in row['province'] and '-' not in group[role]['province']):
                group[role] = candidate
        candidates = [(location,layers) for location,layers in pairs.items() if masks[location[1]].intersects(mask)]
        if not candidates:
            raise ValueError('No available NPAAAD/SAFDZ sources were detected at the ELU location. Check its CRS and source coverage.')
        token = uuid.uuid4().hex
        folder = ROOT / token
        folder.mkdir()
        clips = {'npaaad':[], 'safdz':[]}
        sources = []
        target_crs = None
        for (island,province),layers in sorted(candidates):
            hit_roles = set()
            for role in ('npaaad','safdz'):
                row = layers.get(role)
                if not row:
                    continue
                local = folder / 'source.gpkg'
                try:
                    download(row['key'], local)
                    info = pyogrio.read_info(local)
                    if not info.get('crs'):
                        raise ValueError(f"{row['province']} {role.upper()} has no readable CRS.")
                    local_mask = gpd.GeoSeries([mask], crs=4326).to_crs(info['crs']).iloc[0]
                    field = suggest_field(list(info['fields']), role)
                    if not field:
                        raise ValueError(f"{row['province']} {role.upper()} has no classification field.")
                    data = gpd.read_file(local, bbox=local_mask.bounds, columns=[field])
                    if data.empty:
                        continue
                    data = normalize(data)
                    data = data[data.intersects(local_mask)].copy()
                    if data.empty:
                        continue
                    data['geometry'] = data.geometry.intersection(local_mask)
                    data = normalize(data)
                    data = data[data.geometry.area > 0].copy()
                    if data.empty:
                        continue
                    if target_crs is None:
                        from pyproj import CRS
                        crs = CRS.from_user_input(info['crs'])
                        target_crs = crs if crs.is_projected and all(axis.unit_name.lower() in ('metre','meter') for axis in crs.axis_info) else elu.estimate_utm_crs()
                    data = data.rename(columns={field:role.upper()})[[role.upper(),'geometry']].to_crs(target_crs)
                    clips[role].append(data)
                    hit_roles.add(role)
                    sources.append({'island':island,'province':row['province'],'layer':role,'key':row['key'],'features':len(data)})
                finally:
                    local.unlink(missing_ok=True)
            if hit_roles and set(layers) != {'npaaad','safdz'}:
                raise ValueError(f'{province}: both NPAAAD and SAFDZ sources are required; one source is unavailable.')
        coverage = {}
        for role,frames in clips.items():
            if not frames:
                raise ValueError(f'No {role.upper()} polygons intersect the ELU. Check source coverage and the ELU CRS.')
            data = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=target_crs)
            keep = ~pd.DataFrame({'class':data[role.upper()].astype(str), 'wkb':data.geometry.to_wkb()}).duplicated()
            data = data.loc[keep].copy()
            # Final intersection in the output CRS prevents reprojection artifacts
            # from leaving tiny output slivers outside the uploaded ELU.
            output_mask = gpd.GeoSeries([mask],crs=4326).to_crs(target_crs).iloc[0]
            data['geometry'] = data.geometry.intersection(output_mask)
            data = normalize(data)
            coverage[role] = round(min(100.0, 100.0*unary_union(data.geometry).area/output_mask.area),2)
            data.to_file(folder / (role+'.gpkg'),driver='GPKG',index=False)
        meta = {'token':token,'created':time.time(),'expires_in':TTL,'boundary_hash':fingerprint(elu),
                'sources':sources,'crs':str(target_crs),'cropped_to_elu':True,'coverage_percent':coverage}
        (folder / 'metadata.json').write_text(json.dumps(meta))
        return folder, meta
    except Exception:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        LOCK.release()
