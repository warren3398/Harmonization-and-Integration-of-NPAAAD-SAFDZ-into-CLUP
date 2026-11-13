"""Conservative ELU repair, enclosed-gap checks, and explicit class priorities."""
import math
import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely import make_valid
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union


def polygonal(geometry):
    if geometry is None or geometry.is_empty:
        return None
    geometry = make_valid(geometry) if not geometry.is_valid else geometry
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts = [polygonal(part) for part in geometry.geoms]
        parts = [part for part in parts if part is not None and not part.is_empty]
        return unary_union(parts) if parts else None
    return None


def options(form):
    fill = form.get('elu_fill_gaps') == 'yes'
    try:
        maximum = float(form.get('elu_gap_max_m2', '10'))
    except (TypeError, ValueError):
        raise ValueError('Small-gap tolerance must be a number in square metres.')
    if not math.isfinite(maximum) or not 0 <= maximum <= 10000:
        raise ValueError('Small-gap tolerance must be between 0 and 10,000 square metres.')
    policy = form.get('elu_overlap_policy', 'review')
    if policy not in ('review', 'priority'):
        raise ValueError('Choose Review or Explicit class priority for overlaps.')
    import json
    try:
        priorities = json.loads(form.get('elu_class_priorities', '{}') or '{}')
        if not isinstance(priorities, dict):
            raise ValueError()
        priorities = {str(key).strip():float(value) for key,value in priorities.items()}
        if not all(math.isfinite(value) and value >= 1 and value.is_integer() for value in priorities.values()):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('Class priorities must be unique positive whole numbers.')
    return {'fill_gaps':fill, 'gap_max_m2':maximum, 'overlap_policy':policy, 'priorities':priorities}


def clean(frame, field, settings):
    if frame.crs is None:
        raise ValueError('The ELU needs a readable CRS (.prj) before geometry cleanup.')
    if field not in frame.columns:
        raise ValueError('Choose a valid ELU classification field before cleanup.')
    if frame.empty:
        raise ValueError('The ELU layer is empty.')
    source_crs = frame.crs
    crs = CRS.from_user_input(source_crs)
    metric_crs = crs if crs.is_projected and all(axis.unit_name.lower() in ('metre','meter') for axis in crs.axis_info) else frame.estimate_utm_crs()
    work = frame[[field,'geometry']].copy()
    missing = work[field].isna() | work[field].astype(str).str.strip().eq('')
    invalid = int((~work.geometry.is_valid & work.geometry.notna()).sum())
    work[field] = work[field].fillna('').astype(str).str.strip()
    work['geometry'] = work.geometry.apply(polygonal)
    removed = int((work.geometry.isna() | work.geometry.is_empty).sum())
    work = work[work.geometry.notna() & ~work.geometry.is_empty].to_crs(metric_crs)
    if work.empty:
        raise ValueError('No positive-area polygons remain after repairing the ELU.')
    raw_area = float(work.geometry.area.sum())
    classes = {value:unary_union(group.geometry) for value,group in work.groupby(field,sort=True)}
    if len(classes) > 1000:
        raise ValueError('The ELU field has more than 1,000 classes. Choose a categorical classification field.')
    total_class_area = sum(geometry.area for geometry in classes.values())
    original_union = unary_union(list(classes.values()))
    if original_union.area <= 0:
        raise ValueError('The repaired ELU must have positive polygon area.')
    report = {'field':field,'metric_crs':str(metric_crs),'invalid_features_repaired':invalid,
              'empty_or_nonpolygon_features_removed':removed,'missing_class_features':int(missing.sum()),
              'input_features':len(frame),'same_class_overlap_removed_m2':round(max(0,raw_area-total_class_area),4),
              'gap_scope':'Enclosed gaps only; exterior omissions require an authoritative outer boundary.',
              'settings':settings,'overlap_conflicts':[],'conflicting_classes':[],
              'overlaps_resolved':False,'gaps_filled':0,'gap_area_filled_m2':0.0}
    names = list(classes)
    spatial = gpd.GeoSeries([classes[name] for name in names],crs=metric_crs)
    overlaps = []
    for i,geometry in enumerate(spatial):
        for j in spatial.sindex.query(geometry,predicate='intersects'):
            if j <= i:
                continue
            intersection = polygonal(geometry.intersection(spatial.iloc[j]))
            if intersection is None or intersection.area <= 1e-6:
                continue
            if len(overlaps) >= 5000:
                raise ValueError('The ELU has more than 5,000 class conflicts. Review the classification field and source topology.')
            a,b=names[i],names[j]
            report['overlap_conflicts'].append({'class_a':a,'class_b':b,'area_m2':round(intersection.area,4)})
            overlaps.append({'class_a':a,'class_b':b,'area_m2':intersection.area,'geometry':intersection})
    report['conflicting_classes'] = sorted({row[key] for row in overlaps for key in ('class_a','class_b')})
    report['overlap_area_m2'] = round(unary_union([row['geometry'] for row in overlaps]).area,4) if overlaps else 0.0
    priorities = settings['priorities']
    involved = report['conflicting_classes']
    valid_priority = all(name in priorities for name in involved) and len({priorities.get(name) for name in involved}) == len(involved)
    if overlaps and settings['overlap_policy']=='priority' and valid_priority and not report['missing_class_features']:
        assigned = None
        resolved = {}
        for name in sorted(classes,key=lambda name:(priorities.get(name,float('inf')),name)):
            geometry = classes[name] if assigned is None else classes[name].difference(assigned)
            geometry = polygonal(geometry)
            if geometry is not None and not geometry.is_empty:
                resolved[name]=geometry
                assigned=geometry if assigned is None else assigned.union(geometry)
        classes=resolved
        report['overlaps_resolved']=True
    # Only holes enclosed by the ELU footprint can be identified without an
    # authoritative outside boundary. Never invent a convex hull for gap filling.
    footprint = unary_union(list(classes.values()))
    holes = []
    for part in ([footprint] if isinstance(footprint,Polygon) else footprint.geoms):
        for ring in part.interiors:
            gap = polygonal(Polygon(ring).difference(footprint))
            if gap is None:
                continue
            holes.extend([gap] if isinstance(gap,Polygon) else gap.geoms)
    gap_rows = []
    for gap in holes:
        neighbors=[name for name,geometry in classes.items() if gap.boundary.intersection(geometry.boundary).length > 1e-6]
        eligible = gap.area <= settings['gap_max_m2'] and len(neighbors)==1
        filled = settings['fill_gaps'] and eligible and (not overlaps or report['overlaps_resolved']) and not report['missing_class_features']
        if filled:
            name=neighbors[0]
            classes[name]=classes[name].union(gap)
            report['gaps_filled']+=1
            report['gap_area_filled_m2']+=gap.area
        gap_rows.append({'area_m2':gap.area,'adjacent_classes':', '.join(neighbors),'filled':bool(filled),
                         'review_reason':'filled' if filled else 'multiple adjacent classes' if len(neighbors)!=1 else 'preserved by settings or tolerance',
                         'geometry':gap})
    report['enclosed_gaps_detected']=len(holes)
    report['gap_area_detected_m2']=round(sum(gap.area for gap in holes),4)
    report['gap_area_filled_m2']=round(report['gap_area_filled_m2'],4)
    report['gaps_preserved']=len(holes)-report['gaps_filled']
    report['requires_review']=bool((overlaps and not report['overlaps_resolved']) or report['missing_class_features'])
    report['requires_change_confirmation']=bool(report['overlaps_resolved'] or report['gaps_filled'])
    cleaned=gpd.GeoDataFrame({field:list(classes)},geometry=list(classes.values()),crs=metric_crs)
    issues={}
    if overlaps: issues['overlaps_review']=gpd.GeoDataFrame(overlaps,crs=metric_crs)
    if gap_rows: issues['gaps_review']=gpd.GeoDataFrame(gap_rows,crs=metric_crs)
    return cleaned.to_crs(source_crs),report,issues


def write_review(path, cleaned, issues):
    cleaned.to_file(path,layer='elu_cleaned',driver='GPKG',index=False)
    for layer,frame in issues.items():
        frame.to_file(path,layer=layer,driver='GPKG',index=False)


REVIEW_TTL=3*3600

def save_review(cleaned, report, issues):
    import json, tempfile, time, uuid, zipfile, shutil
    from pathlib import Path
    root=Path(tempfile.gettempdir())/'harmonizer-elu-reviews'
    root.mkdir(exist_ok=True)
    for folder in root.iterdir():
        try:
            if folder.is_dir() and time.time()-folder.stat().st_mtime>REVIEW_TTL:
                shutil.rmtree(folder,ignore_errors=True)
        except FileNotFoundError:
            pass
    token=uuid.uuid4().hex
    folder=root/token;folder.mkdir()
    gpkg=folder/'ELU_GEOMETRY_REVIEW.gpkg'
    write_review(gpkg,cleaned,issues)
    bundle=folder/'ELU_GEOMETRY_REVIEW.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(gpkg,gpkg.name)
        archive.writestr('ELU_CLEANUP_REPORT.json',json.dumps(report,indent=2))
    gpkg.unlink()
    return token


def review_path(token):
    import re,tempfile,time
    from pathlib import Path
    if not re.fullmatch('[a-f0-9]{32}',str(token)):
        raise ValueError('Invalid ELU review download.')
    path=Path(tempfile.gettempdir())/'harmonizer-elu-reviews'/token/'ELU_GEOMETRY_REVIEW.zip'
    if not path.is_file() or time.time()-path.stat().st_mtime>REVIEW_TTL:
        raise ValueError('The ELU review file expired or the service restarted. Inspect the upload again.')
    return path
