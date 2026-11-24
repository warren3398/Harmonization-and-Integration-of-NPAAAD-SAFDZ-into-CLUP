"""Provincial overlays in equal-area metres and original-precision vector exports."""
import json
import os
import re
from collections import OrderedDict, defaultdict
from math import floor, isfinite, log10
from pathlib import Path

import geopandas as gpd
import pyogrio
import shapely as sh
from shapely.geometry import Polygon, box

from bswm_land_use import CROP_BY_CODE, LAND_USE
from sources import ZONES, DataError, canonical

CRS_AREA = 'EPSG:6933'
NP = {'A', 'B', 'C', 'D', 'E', 'G'}
SA = {str(i) for i in range(1, 8)}
VALID = {'npaaad': NP | {'F', 'H', 'BU'}, 'safdz': SA | {'8', '9', '10', 'BU', 'WB'}}
LEGACY_CROPS = ('Rice', 'Irrigated Rice', 'Rainfed Rice', 'Upland Rice', 'Corn', 'Sweet Corn', 'Coconut', 'Mango', 'Banana',
         'Coffee', 'Cacao', 'Onion', 'Garlic', 'Pineapple', 'Sugarcane', 'Abaca', 'Cassava',
         'Sweet Potato', 'Potato', 'Durian', 'Avocado', 'Rubber', 'Peanut', 'Mungbean', 'Soybean',
         'Sorghum', 'Vegetables', 'Strawberry', 'Citrus', 'Tobacco', 'Papaya', 'Oil Palm',
         'Dragon Fruit', 'Rambutan', 'Cut Flowers', 'Diversified Crops', 'Mixed Fruit Trees', 'Other Agricultural Crops')
CROPS = tuple(dict.fromkeys((*LEGACY_CROPS, *CROP_BY_CODE.values())))
COLORS = {'Both': '#187354', 'NPAAAD only': '#4e65aa', 'SAFDZ only': '#a763b0',
          'Outside both': '#e6963e', 'Missing coverage': '#7d8799', 'Within NPAAAD': '#187354',
          'Outside NPAAAD': '#e6963e', 'Within SAFDZ': '#187354', 'Outside SAFDZ': '#e6963e'}
CROP_MAP = {canonical(name): name for name in CROPS}
CROP_MAP.update({canonical(LAND_USE[code]): crop for code, crop in CROP_BY_CODE.items()})
CROP_MAP.update({'mixedvegetables': 'Vegetables', 'diversifiedcrop': 'Diversified Crops', 'abacca': 'Abaca', 'maize': 'Corn', 'sugarcaneplantation': 'Sugarcane', 'coconutplantation': 'Coconut',
                 'palay': 'Rice', 'ricelandirrigated': 'Irrigated Rice', 'ricelandrainfed': 'Rainfed Rice'})
# BSWM Map Guidebook, Table 5 (Land Use). Keep the source abbreviation
# in lu_original and use its documented irrigation type for crop selection.
BSWM_RICE_CODES = {'pri': 'Irrigated Rice', 'prn': 'Rainfed Rice',
                   'tri': 'Irrigated Rice', 'trn': 'Rainfed Rice'}
RICE_TYPES = {'Irrigated Rice', 'Rainfed Rice', 'Upland Rice'}
NON_CROP = {'buildup', 'builtup', 'forest', 'grassland', 'shrubland', 'shrubs', 'water', 'fishpond',
            'mangrove', 'river', 'riverwash', 'reservoir', 'lake', 'pasture', 'quarry', 'barrenland'}
NON_CROP.update(canonical(label) for code, label in LAND_USE.items() if code not in CROP_BY_CODE)
ZONE_COLUMNS = {'safdz': [str(i) for i in range(1, 11)] + ['BU', 'Others'],
                'npaaad': list('ABCDEFGH') + ['BU', 'Others']}
ZONE_LABELS = {
    'safdz': {
        '1': 'Strategic Crop Sub-Development Zone',
        '2': 'Strategic Livestock Sub-Development Zone',
        '3': 'Strategic Fishery Sub-Development Zone',
        '4': 'Strategic Integrated Crop/Livestock Sub-Development Zone',
        '5': 'Strategic Integrated Crop/Fishery Sub-Development Zone',
        '6': 'Strategic Integrated Crop/Livestock/Fishery Sub-Development Zone',
        '7': 'Strategic Integrated Fishery/Livestock Sub-Development Zone',
        '8': 'Remaining NPAAAD',
        '9': 'Agro-forestry',
        '10': 'Watershed Areas/Forest areas (critical watershed including mangroves)',
        'BU': 'Built-up areas, urban land, airport, roads and bridges',
        'Others': 'Quarry, mine pit, barren land, rock land, river wash, beach sand, sand dunes and landfill',
    },
    'npaaad': {
        'A': 'All irrigated land/areas',
        'B': 'All irrigable lands already covered by irrigation projects with firm funding commitments',
        'C': 'All alluvial plain lands highly suitable for agriculture whether irrigated or not',
        'D': 'Agro-industrial crop-lands or lands presently planted to industrial crops that support the viability of existing agricultural infrastructure and agro-based enterprises',
        'E': 'Highlands or areas located at an elevation of five hundred (500) meters or above and have the potentials for growing semi-temperate and high value crops',
        'F': 'All agricultural lands that are ecologically fragile, the conversion of which will result in serious environmental degradation, and mangrove areas and fish sanctuaries',
        'G': 'All fishery areas as defined pursuant to Fisheries Code of 1998',
        'H': 'Forest/Watershed areas (critical watersheds including mangroves)',
        'BU': 'Built-up areas, urban land, airport, roads and bridges',
        'Others': 'Quarry, mine pit, barren land, rock land, river wash, beach sand, sand dunes and landfill',
    },
}
EXPORTS = {'csv', 'npaaad.csv', 'safdz.csv', 'xlsx', 'pdf', 'geojson', 'gpkg', 'shp.zip', 'map.png', 'map.jpg', 'map.pdf', 'qa.json'}


def crop_name(raw, aliases, code=None):
    key = canonical(raw)
    if key in aliases:
        return aliases[key]
    if key in BSWM_RICE_CODES:
        return BSWM_RICE_CODES[key]
    if key in CROP_MAP:
        return CROP_MAP[key]
    simplified = re.sub(r'\s*\([^()]*\)\s*$', '', raw.strip())
    if canonical(simplified) in CROP_MAP:
        return CROP_MAP[canonical(simplified)]
    if canonical(simplified) in NON_CROP:
        return None
    # Numeric values from GIS fields can be represented as integral floats.
    code = re.sub(r'(?<=\d)\.0+$', '', str(code if code is not None else raw).strip().lower())
    return CROP_BY_CODE.get(code)


def land_use_code(attrs):
    return next((value for field, value in attrs.items()
                 if field.upper() in {'LU_CODE', 'LANDUSE_CODE'} and str(value).strip()), None)


def geom_polygon(geom):
    if geom is None or geom.is_empty:
        return Polygon()
    if not geom.is_valid:
        geom = sh.make_valid(geom)
    if geom.geom_type in ('Polygon', 'MultiPolygon'):
        return geom
    if geom.geom_type == 'GeometryCollection':
        return sh.union_all([geom_polygon(part) for part in geom.geoms])
    return Polygon()


def source_schema(path, role):
    layer, info, field = None, None, None
    override = os.getenv('PCLAM_' + role.upper() + '_FIELD')
    fields = {'landuse': ['LANDUSE', 'LAND_USE', 'LU_DESC', 'DESCRIPTION', 'CLASS', 'LU_CODE', 'LANDUSE_CODE'],
              'npaaad': ['NPAAAD', 'NPAAAD_CL', 'NPAAAD_CLASS', 'CLASS'],
              'safdz': ['SAFDZ', 'SAFDZ_CL', 'SAFDZ_CLASS', 'CLASS']}[role]
    for candidate, geometry_type in pyogrio.list_layers(path):
        if geometry_type is None:
            continue
        details = pyogrio.read_info(path, layer=candidate)
        lookup = {f.upper(): f for f in details['fields']}
        field = next((lookup[f] for f in ([override.upper()] if override else fields) if f in lookup), None)
        if field:
            layer, info = candidate, details
            break
    if layer is None or not info['crs']:
        raise DataError(f'The {role.upper()} source lacks an identifiable classification field or CRS.')
    return layer, info, field


def source_batches(path, layer, columns, bbox, batch_size, check):
    offset = 0
    while True:
        check()
        batch = pyogrio.read_dataframe(path, layer=layer, columns=columns, bbox=bbox,
                                        skip_features=offset, max_features=batch_size, force_2d=True)
        if batch.empty:
            return
        yield batch
        if len(batch) < batch_size:
            return
        offset += len(batch)


def projected_bounds(province, crs, cache):
    if crs not in cache:
        cache[crs] = tuple(province.to_crs(crs).total_bounds)
    return cache[crs]


def bounds_overlap(left, right):
    return left[0] <= right[2] and right[0] <= left[2] and left[1] <= right[3] and right[1] <= left[3]


def source_rows(path, role, province, qa, check, wanted=None, *, schema=None,
                bbox=None, bound=None, prepared=False):
    layer, info, field = schema if schema is not None else source_schema(path, role)
    if bbox is None:
        bbox = tuple(province.to_crs(info['crs']).total_bounds)
    if bound is None:
        bound = province.geometry.union_all()
    # A source polygon can span the whole province even for a municipal query.
    # Keep a margin around the requested extent before expensive reprojection
    # and topology repair. The final overlay still uses the exact boundary.
    xmin, ymin, xmax, ymax = bbox
    margin = max(xmax - xmin, ymax - ymin) * .25
    clip_bounds = (xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    extra = [f for f in info['fields'] if f != field and f.upper() in {'MUN_NAME', 'ORIG_FID', 'LU_CODE', 'LANDUSE_CODE'}]
    batch_size = 24 if role == 'landuse' else 2
    for batch in source_batches(path, layer, [field, *extra], bbox, batch_size, check):
        if wanted is not None:
            batch = batch[[wanted(str(record[field] or '').strip(), land_use_code({name: record[name] for name in extra}))
                           for record in batch[[field, *extra]].to_dict('records')]]
        if role != 'landuse':
            batch.geometry = batch.geometry.map(
                lambda geom: sh.clip_by_rect(geom, *clip_bounds)
                if sh.get_num_coordinates(geom) > 100_000 else geom
            )
        batch = batch.to_crs(CRS_AREA)
        for record in batch.to_dict('records'):
            geom = record.pop('geometry')
            if geom is not None and not geom.is_valid:
                qa['repaired_geometries'] += 1
            geom = geom_polygon(geom)
            if geom.is_empty or not geom.intersects(bound):
                continue
            if not prepared or not bound.covers(geom):
                geom = geom_polygon(geom.intersection(bound))
            if geom.area > 0:
                raw = str(record.pop(field) or '').strip()
                yield raw, geom, record


def zone_masks(records, role, qa, check, measure=True):
    raw_groups = defaultdict(list)
    for raw, geom, _ in records:
        raw_groups[raw.upper()].append(geom)
    # Stop an internal source overlap from being counted twice. Conflicting
    # codes are unknown until the data owner reconciles the source.
    assigned = Polygon()
    conflicts = Polygon()
    codes = {}
    for label, shapes in sorted(raw_groups.items()):
        check()
        dissolved = sh.union_all(shapes)
        conflicts = sh.union(conflicts, sh.intersection(assigned, dissolved))
        assigned = sh.union(assigned, dissolved)
        codes[label] = dissolved
    qa['source_overlap_ha'][role] = conflicts.area / 10000
    if conflicts.area:
        codes = {label: geom_polygon(geom.difference(conflicts)) for label, geom in codes.items()}
    allowed = NP if role == 'npaaad' else SA
    agricultural = sh.union_all([geom for code, geom in codes.items() if code in allowed]) if measure else None
    return codes, agricultural



def local_zone_index(path, role, boundary, qa, check):
    records = []
    coordinates = 0
    for record in source_rows(path, role, boundary, qa, check):
        coordinates += sh.get_num_coordinates(record[1])
        if coordinates > 120_000 or len(records) >= 4000:
            return None
        records.append(record)
    return records, sh.STRtree([geom for _, geom, _ in records])


def local_zone_rows(index, clip):
    records, tree = index
    # Match the input feature order used when dissolving overlapping classes.
    for number in sorted(tree.query(clip, predicate='intersects')):
        label, geom, attributes = records[number]
        clipped = geom_polygon(geom.intersection(clip))
        if clipped.area > 0:
            yield label, clipped, attributes

def zone_partitions(shape, codes):
    remaining = shape
    for label, coverage in codes.items():
        if coverage.is_empty or not remaining.intersects(coverage):
            continue
        portion = geom_polygon(remaining.intersection(coverage))
        if not portion.is_empty:
            yield label, portion
            remaining = geom_polygon(remaining.difference(portion))
    if not remaining.is_empty:
        yield '', remaining


def class_name(np, sa, mode):
    if mode == 'npaaad':
        return 'Missing coverage' if np not in VALID['npaaad'] else 'Within NPAAAD' if np in NP else 'Outside NPAAAD'
    if mode == 'safdz':
        return 'Missing coverage' if sa not in VALID['safdz'] else 'Within SAFDZ' if sa in SA else 'Outside SAFDZ'
    if np not in VALID['npaaad'] or sa not in VALID['safdz']:
        return 'Missing coverage'
    if np in NP and sa in SA:
        return 'Both'
    if np in NP:
        return 'NPAAAD only'
    if sa in SA:
        return 'SAFDZ only'
    return 'Outside both'



def zone_tables(areas, crops):
    tables = []
    for role, crop_areas in areas.items():
        columns = ZONE_COLUMNS[role]
        rows = [[crop, *[crop_areas.get(crop, {}).get(code, 0.) for code in columns]] for crop in sorted(crops)]
        totals = [sum(row[index] for row in rows) for index in range(1, len(columns) + 1)]
        labels = [ZONE_LABELS[role].get(code, '') for code in columns]
        tables.append({'zone': role, 'columns': columns, 'labels': labels,
                       'rows': rows, 'total': ['Total', *totals]})
    return tables

def assessments(sources, row, mode, selected, out, progress, check, output=None, intermediate=False, focused=False, archive_bounds=None):
    required = ('npaaad', 'safdz') if mode == 'combined' else (mode,)
    missing = [role.upper() for role in required if role not in row['zones']]
    if missing or not row['land']:
        raise DataError('Missing dataset: ' + ', '.join(missing + ([] if row['land'] else ['Land Use 2020–2022'])))
    aliases = {canonical(k): v for k, v in json.loads(os.getenv('PCLAM_CROP_MAPPING', '{}')).items()}
    if any(v not in CROPS and v is not None for v in aliases.values()):
        raise DataError('A configured crop mapping uses an unsupported standard crop name.')
    qa = {'repaired_geometries': 0, 'source_overlap_ha': {}, 'land_overlap_ha': 0.,
          'unmapped_land_classes_ha': {}, 'source_crop_labels': [], 'land_polygons': 0, 'slivers_under_1m2': 0, 'land_objects': [], 'zone_objects': {}}
    progress(3, 'Reading provincial boundary')
    province = (sources.boundary(row) if row.get('municipality') else sources.province(row['id'])).to_crs(CRS_AREA)
    province.geometry = province.geometry.map(geom_polygon)
    boundary = province.geometry.union_all()
    if boundary.is_empty:
        raise DataError('The selected boundary has no valid polygon.')
    regional_table = archive_bounds is not None
    if regional_table:
        sh.prepare(boundary)
    zone_files = {}
    zone_indices = {}
    zone_schemas = {}
    for index, role in enumerate(required):
        meta = row['zones'][role]
        progress(10 + 12 * index, f'Retrieving {role.upper()} source')
        check()
        qa['zone_objects'][role] = {key: meta[key] for key in ('Key', 'ETag')}
        zone_files[role] = sources.file(ZONES, meta['Key'], meta)
        if focused and row.get('municipality'):
            progress(12 + 12 * index, f'Indexing {role.upper()} within the selected boundary')
            index_qa = {'repaired_geometries': 0}
            zone_index = local_zone_index(zone_files[role], role, province, index_qa, check)
            if zone_index is not None:
                zone_indices[role] = zone_index
                qa['repaired_geometries'] += index_qa['repaired_geometries']

    cell_size = 3500
    measured = {}
    def cells(shape):
        xmin, ymin, xmax, ymax = shape.bounds
        for x in range(floor(xmin / cell_size), floor(xmax / cell_size) + 1):
            for y in range(floor(ymin / cell_size), floor(ymax / cell_size) + 1):
                cell = box(x * cell_size, y * cell_size, (x + 1) * cell_size, (y + 1) * cell_size)
                if shape.intersects(cell):
                    yield (x, y), cell

    mask_cache = OrderedDict()
    mask_coordinates = 0

    def tile_masks(tile, cell):
        nonlocal mask_coordinates
        if tile in mask_cache:
            mask_cache.move_to_end(tile)
            return mask_cache[tile][0]
        local = geom_polygon(boundary.intersection(cell))
        if local.is_empty:
            return {}
        tile_province = gpd.GeoDataFrame(geometry=[local], crs=CRS_AREA)
        masks = {}
        tile_qa = {'repaired_geometries': 0, 'source_overlap_ha': {}}
        areas = {}
        for role, path in zone_files.items():
            if role in zone_indices:
                records = local_zone_rows(zone_indices[role], local)
            else:
                if role not in zone_schemas:
                    zone_schemas[role] = source_schema(path, role)
                records = source_rows(path, role, tile_province, tile_qa, check, schema=zone_schemas[role],
                                      bound=local)
            masks[role], ag = zone_masks(records, role, tile_qa, check, measure=not focused)
            if ag is not None:
                areas[role] = ag.area
        if tile not in measured:
            measured[tile] = areas
            qa['repaired_geometries'] += tile_qa['repaired_geometries']
            for role, area in tile_qa['source_overlap_ha'].items():
                qa['source_overlap_ha'][role] = qa['source_overlap_ha'].get(role, 0) + area
        weight = sum(sh.get_num_coordinates(shape) for codes in masks.values() for shape in codes.values())
        while mask_cache and (len(mask_cache) >= 64 or mask_coordinates + weight > 200_000):
            _, (_, discarded) = mask_cache.popitem(last=False)
            mask_coordinates -= discarded
        mask_cache[tile] = masks, weight
        mask_coordinates += weight
        return masks

    stats = defaultdict(lambda: defaultdict(float))
    observed_crops = set()
    pending = []
    occupied = {}
    export_path = out / 'assessment.gpkg'
    feature_count = 0
    matrix_areas = {role: defaultdict(lambda: defaultdict(float)) for role in required} if output == 'table' else {}
    store_geometry = not (focused and intermediate and output == 'table')
    total = len(row['land'])
    bbox_cache = {}
    for index, meta in enumerate(sorted(row['land'], key=lambda m: m['Key'])):
        check()
        progress(35 + int(51 * index / total), f'Reading regional land use archive {index + 1} of {total}')
        qa['land_objects'].append({key: meta[key] for key in ('Key', 'ETag')})
        archive_key = meta['Key'], meta['ETag']
        known_bounds = archive_bounds.get(archive_key) if archive_bounds is not None else None
        if known_bounds and all(not bounds_overlap(bounds, projected_bounds(province, crs, bbox_cache))
                                for crs, bounds in known_bounds):
            continue
        extents = []
        for path in sources.land_files(meta, out):
            schema = source_schema(path, 'landuse')
            info = schema[1]
            crs, extent = info['crs'], info['total_bounds']
            bbox = projected_bounds(province, crs, bbox_cache)
            extent = tuple(extent) if extent is not None and all(isfinite(value) for value in extent) else None
            extents.append((crs, extent))
            if archive_bounds is not None and extent is not None and not bounds_overlap(extent, bbox):
                continue
            for raw, geom, attrs in source_rows(path, 'landuse', province, qa, check, schema=schema,
                                               bbox=bbox, bound=boundary, prepared=regional_table):
                qa['land_polygons'] += 1
                crop = crop_name(raw, aliases, land_use_code(attrs))
                if not crop:
                    if canonical(raw) not in NON_CROP:
                        unmapped = qa['unmapped_land_classes_ha']
                        unmapped[raw or '(blank)'] = unmapped.get(raw or '(blank)', 0) + geom.area / 10000
                    continue
                observed_crops.add(crop)
                if selected and crop not in selected and not ('Rice' in selected and crop in RICE_TYPES):
                    continue
                for tile, cell in cells(geom):
                    portion = geom if regional_table and cell.covers(geom) else geom_polygon(geom.intersection(cell))
                    if not portion.area:
                        continue
                    previous = occupied.get(tile)
                    remaining = (geom_polygon(portion.difference(previous))
                                 if previous is not None and previous.intersects(portion) else portion)
                    qa['land_overlap_ha'] += max(0., portion.area - remaining.area) / 10000
                    if remaining.is_empty:
                        continue
                    occupied[tile] = remaining if previous is None else sh.union(previous, remaining)
                    masks = tile_masks(tile, cell)
                    s = stats[crop]
                    for role, crop_areas in matrix_areas.items():
                        for code, coverage in masks.get(role, {}).items():
                            if not code or coverage.is_empty or not remaining.intersects(coverage):
                                continue
                            hectares = remaining.intersection(coverage).area / 10000
                            if hectares:
                                column = code if code in ZONE_COLUMNS[role] else 'Others'
                                crop_areas[crop][column] += hectares
                    np_zones = masks.get('npaaad', {})
                    sa_zones = masks.get('safdz', {})
                    if focused:
                        s['Total mapped'] += remaining.area / 10000
                        np_zones = {code: geom for code, geom in np_zones.items() if code in NP}
                        sa_zones = {code: geom for code, geom in sa_zones.items() if code in SA}
                    for np, piece in zone_partitions(remaining, np_zones):
                        for sa, result in zone_partitions(piece, sa_zones):
                            if focused and not (np or sa):
                                continue
                            area = result.area / 10000
                            category = class_name(np, sa, mode)
                            if focused:
                                s['Within selected zone(s)'] += area
                                if mode == 'combined':
                                    category = 'Both' if np and sa else 'NPAAAD only' if np else 'SAFDZ only'
                            else:
                                s['Total mapped'] += area
                            s[category] += area
                            if np in NP: s['Agricultural NPAAAD'] += area
                            if sa in SA: s['Agricultural SAFDZ'] += area
                            if np == 'F': s['Fragile agricultural land'] += area
                            if np == 'H': s['Forest / watershed'] += area
                            if np in VALID['npaaad'] - NP: s['Outside agricultural NPAAAD'] += area
                            if sa in VALID['safdz'] - SA: s['Outside agricultural SAFDZ'] += area
                            if area < .0001: qa['slivers_under_1m2'] += 1
                            feature_count += 1
                            if not store_geometry:
                                continue
                            pending.append({'region': row['region'], 'province': row['name'], 'psgc': row['id'],
                                            'municipality': row.get('municipality_name') or attrs.get('Mun_Name'),
                                            'barangay': row.get('barangay_name'), 'lu_original': raw,
                                            'lu_code': str(land_use_code(attrs) or ''),
                                            'source_fid': str(attrs.get('ORIG_FID', '')), 'crop_name': crop,
                                            'npaaad_code': np, 'safdz_code': sa, 'assessment': category,
                                            'area_ha': area, 'data_year': '2020–2022', 'geometry': result})
                            if len(pending) >= 250:
                                flush(pending, export_path)
        # ZIP archives can contain several layers. Cache only a complete set of extents.
        if archive_bounds is not None and all(extent is not None for _, extent in extents):
            archive_bounds[archive_key] = extents
    flush(pending, export_path)
    qa['source_crop_labels'] = sorted(observed_crops)
    if store_geometry and not export_path.exists():
        frame = gpd.GeoDataFrame({'crop_name': [], 'assessment': [], 'area_ha': []}, geometry=[], crs=CRS_AREA)
        pyogrio.write_dataframe(frame, export_path, layer='assessment', geometry_type='MultiPolygon')
    progress(87, 'Summarizing crop hectares' if focused else 'Calculating area of the protection layers')
    qa['assessment_area_ha'] = boundary.area / 10000
    qa['province_area_ha'] = qa['assessment_area_ha']
    occupied_area = sum(g.area for g in occupied.values()) / 10000
    occupied.clear()
    mask_cache.clear()
    mask_coordinates = 0
    if focused:
        zone_indices.clear()
        qa['agricultural_zone_area_ha'] = {}
    else:
        for tile, cell in cells(boundary):
            if tile not in measured:
                check()
                tile_masks(tile, cell)
                mask_cache.clear()
                mask_coordinates = 0
        qa['agricultural_zone_area_ha'] = {role: sum(v.get(role, 0) for v in measured.values()) / 10000
                                          for role in required}
    qa['area_reconciliation_ha'] = abs(sum(s['Total mapped'] for s in stats.values()) -
                                      occupied_area)
    for totals in stats.values():
        if focused:
            totals.setdefault('Within selected zone(s)', 0.)
        for role in required:
            key = f'Agricultural {role.upper()}'
            totals[f'{role.upper()} coverage (%)'] = 100 * totals.get(key, 0) / totals['Total mapped']
        if mode == 'combined':
            totals['Both coverage (%)'] = 100 * totals.get('Both', 0) / totals['Total mapped']
            if not focused:
                totals['Outside both (%)'] = 100 * totals.get('Outside both', 0) / totals['Total mapped']
        safdz_ha = qa['agricultural_zone_area_ha'].get('safdz', 0)
        if safdz_ha:
            totals['Share of assessed SAFDZ (%)' if row.get('municipality') else 'Share of provincial SAFDZ (%)'] = 100 * totals.get('Agricultural SAFDZ', 0) / safdz_ha
    bounds = list(province.to_crs('EPSG:4326').total_bounds)
    result = {'province': row['name'], 'region': row['region'], 'mode': mode, 'bounds': bounds,
              'stats': stats, 'qa': qa, 'features': feature_count, 'colors': COLORS, 'focused': focused,
              'area': row.get('area', row['name']), 'level': row.get('level', 'Province')}
    if intermediate:
        result['matrix_areas'] = matrix_areas
        return result
    if matrix_areas:
        result['zone_tables'] = zone_tables(matrix_areas, stats)
    progress(95, 'Preparing selected output')
    write_products(out, result, pyogrio.read_dataframe(export_path), province, output)
    return result


def flush(pending, path):
    if pending:
        frame = gpd.GeoDataFrame(pending, geometry='geometry', crs=CRS_AREA)
        pyogrio.write_dataframe(frame, path, layer='assessment', append=path.exists(),
                                geometry_type='MultiPolygon', promote_to_multi=True)
        pending.clear()


def write_products(out, result, frame, province, output=None):
    import csv
    from openpyxl import Workbook
    from PIL import Image, ImageDraw
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Image as PdfImage, Spacer
    from xml.sax.saxutils import escape

    qa = result['qa']
    (out / 'assessment.qa.json').write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    fields = list(dict.fromkeys(['Total mapped', *(k for row in result['stats'].values() for k in row)]))
    matrix = [['Crop', *[key if key.endswith('(%)') else f'{key} (ha)' for key in fields]]]
    if result.get('focused') and 'Within selected zone(s)' not in fields:
        fields.append('Within selected zone(s)')
        matrix[0].append('Within selected zone(s) (ha)')
    for crop, stats in sorted(result['stats'].items()):
        matrix.append([crop, *[round(stats.get(key, 0), 6) for key in fields]])
    if len(matrix) > 1:
        totals = [sum(row[i] for row in matrix[1:]) for i in range(1, len(fields) + 1)]
        weights = [row[1] for row in matrix[1:]]
        for i, key in enumerate(fields):
            if key.endswith('(%)') and not key.startswith('Share of ') and weights and sum(weights):
                totals[i] = sum(row[i + 1] * row[1] for row in matrix[1:]) / sum(weights)
        matrix.append(['Total', *[round(value, 6) for value in totals]])
    if output in (None, 'table'):
        workbook = Workbook()
        tables = result.get('zone_tables', [])
        csv_exports = []
        if tables:
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter

            for index, table in enumerate(tables):
                role = table['zone']
                sheet = workbook.active if index == 0 else workbook.create_sheet()
                sheet.title = role.upper()
                table_rows = [['Area (ha)', *table['labels']], ['Crops', *table['columns']],
                              *table['rows'], table['total']]
                filename = 'csv' if len(tables) == 1 else role + '.csv'
                with (out / ('assessment.' + filename)).open('w', newline='', encoding='utf-8-sig') as file:
                    csv.writer(file).writerows([
                        [round(v, 6) if isinstance(v, float) else v for v in row] for row in table_rows
                    ])
                csv_exports.append(filename)
                for row in table_rows:
                    sheet.append(row)
                sheet.freeze_panes = 'B3'
                sheet.row_dimensions[1].height = 96 if role == 'safdz' else 122
                for number, column in enumerate(sheet.columns, start=1):
                    sheet.column_dimensions[get_column_letter(number)].width = 24 if number == 1 else 22 if role == 'safdz' else 32
                    for cell in column:
                        cell.border = Border(bottom=Side(style='thin', color='BCCAC5'),
                                             right=Side(style='thin', color='BCCAC5'))
                        if cell.row <= 2 or cell.row == len(table_rows):
                            cell.font = Font(bold=True, color='163F32')
                            cell.fill = PatternFill('solid', fgColor='E4F0EA')
                            cell.alignment = Alignment(wrap_text=True, vertical='center')
                        if number > 1 and cell.row > 2:
                            cell.number_format = '#,##0.00'
        else:
            with (out / 'assessment.csv').open('w', newline='', encoding='utf-8-sig') as file:
                csv.writer(file).writerows(matrix)
            csv_exports.append('csv')
            sheet = workbook.active
            sheet.title = 'Crop assessment'
            for line in matrix:
                sheet.append(line)
            sheet.freeze_panes = 'B2'
            sheet.auto_filter.ref = sheet.dimensions
        profile = workbook.create_sheet('Coverage')
        profile.append(['Assessment area', result['area']])
        profile.append(['Geographic level', result['level']])
        if tables:
            profile.append(['Others', 'Source zone classes without their own column. Areas without an assigned zone are excluded.'])
            if len(tables) > 1:
                profile.append(['Combined', 'Both sheets summarize the same crop polygons independently. Do not add the sheet totals.'])
        for name in result.get('missing_provinces', []):
            profile.append(['Excluded province', name])
        diagnostics = workbook.create_sheet('Source diagnostics')
        diagnostics.append(['Intersecting source polygons', qa.get('land_polygons', 0)])
        diagnostics.append(['Recognized crops in area', ', '.join(qa.get('source_crop_labels', []))])
        diagnostics.append(['Unconfirmed source label', 'Source polygon area (ha, may overlap)'])
        for label, area in sorted(qa.get('unmapped_land_classes_ha', {}).items()):
            diagnostics.append([label, round(area, 6)])
            diagnostics.cell(diagnostics.max_row, 1).data_type = 's'
        workbook.save(out / 'assessment.xlsx')
        if output == 'table' and result['level'] == 'Nationwide':
            result['exports'] = ['xlsx', *csv_exports, 'qa.json']
            return
    if output == 'shp' or (output is None and len(frame)):
        with tempfile_directory(out) as directory:
            path = Path(directory)
            labels = dict(zip(frame.columns, frame.columns))
            labels.update({'municipality': 'municipal', 'npaaad_code': 'np_code',
                           'safdz_code': 'sa_code', 'data_year': 'year', 'lu_original': 'lu_orig'})
            pyogrio.write_dataframe(frame.rename(columns=labels), path / 'assessment.shp', encoding='UTF-8', geometry_type='MultiPolygon', promote_to_multi=True)
            coverage = f"{result['level']}: {result['area']}\n"
            if result.get('missing_provinces'):
                coverage += 'Excluded provinces: ' + ', '.join(result['missing_provinces']) + '\n'
            (path / 'fields.txt').write_text(coverage + 'Shapefile field names and GeoPackage equivalents\n' +
                                              '\n'.join(f'{value} = {key}' for key, value in labels.items()))
            shutil_make_zip(out / 'assessment.shp.zip', path)
    view = frame.copy()
    view.geometry = view.geometry.simplify(2 if result['level'] == 'Barangay' else 12, preserve_topology=True)
    if len(view):
        view = view.dissolve(by='assessment', as_index=False)
        view = view[['assessment', 'geometry']]
    boundary_geojson = province[['geometry']].to_crs('EPSG:4326').to_json(drop_id=True)
    (out / 'boundary.geojson').write_text(boundary_geojson)
    (out / 'map.geojson').write_text(view.to_crs('EPSG:4326').to_json(drop_id=True))
    if output is None and len(frame) <= 15000:
        frame.to_crs('EPSG:4326').to_file(out / 'assessment.geojson', driver='GeoJSON', engine='pyogrio')
        if (out / 'assessment.geojson').stat().st_size > 30_000_000:
            (out / 'assessment.geojson').unlink()
    if output == 'jpeg':
        from map_layout import draw_jpeg
        draw_jpeg(out / 'assessment.map.jpg', result, view, province)
    elif output is None:
        map_crs = CRS_AREA
        map_area = province.to_crs(map_crs)
        map_view = view.to_crs(map_crs)
        width, height = 1560, 1100
        image = Image.new('RGB', (width, height), '#e1eee9')
        draw = ImageDraw.Draw(image)
        xmin, ymin, xmax, ymax = map_area.total_bounds
        scale = min((width - 520) / (xmax - xmin), (height - 180) / (ymax - ymin))
        def xy(coords):
            return [(65 + (x - xmin) * scale, height - 75 - (y - ymin) * scale) for x, y, *_ in coords]
    
        def paint(geometry, fill, draw_on, outline=None):
            for poly in sh.get_parts(geom_polygon(geometry)):
                draw_on.polygon(xy(poly.exterior.coords), fill=fill, outline=outline)
                for hole in poly.interiors:
                    draw_on.polygon(xy(hole.coords), fill=0 if isinstance(fill, int) else '#e1eee9')
    
        land = map_area.geometry.union_all()
        paint(land, '#f5f5e8', draw, '#496158')
        active = []
        for feature in map_view.itertuples():
            mask = Image.new('1', image.size)
            paint(feature.geometry, 1, ImageDraw.Draw(mask))
            image.paste(COLORS[feature.assessment], mask=mask)
            active.append(feature.assessment)
        draw.text((52, 39), f"{result['area']} | Provincial Crop-Based Land Use Assessment Map",
                  fill='#14392f', font_size=27)
        draw.text((52, 76), 'Land Use 2020–2022 | Equal-area analysis · EPSG:6933', fill='#30594c', font_size=16)
        draw.text((1160, 180), 'ASSESSMENT CLASSES', fill='#14392f', font_size=21)
        for index, label in enumerate(active):
            y = 225 + index * 38
            draw.rectangle((1160, y, 1180, y + 20), fill=COLORS[label])
            draw.text((1190, y), label, fill='#14392f', font_size=17)
        ratio = 1
        bar_m = max(1, round(140 * ratio / scale, -int(log10(max(140 * ratio / scale, 1)))))
        bar_px = bar_m * scale / ratio
        bar_label = f'{bar_m / 1000:g} km' if bar_m >= 1000 else f'{bar_m:g} m'
        draw.line((1160, 515, 1160 + bar_px, 515), fill='#14392f', width=4)
        draw.text((1160, 526), f'{bar_label} (approx.)', fill='#14392f', font_size=16)
        if result.get('missing_provinces'):
            draw.text((1160, 565), 'Partial regional coverage', fill='#8d351f', font_size=20)
        image.save(out / 'assessment.map.png')
        image.save(out / 'assessment.map.pdf', resolution=150)
    if output is not None:
        choices = {'jpeg': ['map.jpg'], 'shp': ['shp.zip'], 'table': ['xlsx', *csv_exports] if output == 'table' else []}
        result['exports'] = choices[output] + ['qa.json']
        return
    styles = getSampleStyleSheet()
    title = escape(result['area']) + ' Crop-Based Land Use Assessment'
    total = sum(r['Total mapped'] for r in result['stats'].values())
    lead = max(result['stats'], key=lambda k: result['stats'][k]['Total mapped'], default=None)
    summary = f'The selected classification covers {total:,.2f} hectares of mapped crop area.'
    if lead:
        summary += f" {lead} is the largest mapped crop group at {result['stats'][lead]['Total mapped']:,.2f} hectares."
    report = [Paragraph(title, styles['Title'])]
    sections = [
        ('Executive summary', summary),
        ('Provincial profile', f"{result['region']} | Administrative area {qa['province_area_ha']:,.2f} ha"),
        ('Data sources', 'Philippine Land Use 2020–2022, shared provincial administrative boundaries, and the nationwide NPAAAD and SAFDZ collections for the selected assessment.'),
        ('Methodology', 'Input polygons were checked, projected to EPSG:6933 and clipped to the provincial boundary. Coverage was overlaid using the supplied agricultural class definitions. Areas in square metres were divided by 10,000.'),
        ('Crop distribution and assessment', 'The assessment matrix contains the complete breakdown by crop and protection category.'),
    ]
    for heading, body in sections:
        report.extend([Paragraph(heading, styles['Heading2']), Paragraph(escape(body), styles['BodyText'])])
    top = sorted(result['stats'].items(), key=lambda x: -x[1]['Total mapped'])[:10]
    if top:
        data = [['Crop (top 10)', 'Area (ha)', 'Missing (ha)']] + [
            [name, f"{r['Total mapped']:,.2f}", f"{r.get('Missing coverage',0):,.2f}"] for name, r in top
        ]
        report.extend([Spacer(1, 8), Table(data, repeatRows=1, style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeae3')),
            ('LINEBELOW', (0, 0), (-1, 0), .5, colors.HexColor('#207458')),
        ]))])
    report.extend([Paragraph('Assessment map', styles['Heading2']),
                   PdfImage(str(out / 'assessment.map.png'), width=475, height=335),
                   Paragraph('Data limitations', styles['Heading2']),
                   Paragraph('Missing protection coverage is a separate category. Crop labels without a confirmed mapping are excluded from crop totals and recorded in the QA file.', styles['BodyText']),
                   Paragraph('Quality assurance', styles['Heading2']),
                   Paragraph(f"{qa['repaired_geometries']} geometry repairs. {qa['slivers_under_1m2']} overlay pieces under 1 m². "
                             f"Area reconciliation difference {qa['area_reconciliation_ha']:.6f} ha.", styles['BodyText'])])
    SimpleDocTemplate(str(out / 'assessment.pdf')).build(report)
    result['exports'] = sorted(ext for ext in EXPORTS if (out / ('assessment.' + ext)).exists())


def tempfile_directory(out):
    import tempfile
    return tempfile.TemporaryDirectory(dir=out)


def shutil_make_zip(output, folder):
    from zipfile import ZipFile, ZIP_DEFLATED
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for file in folder.iterdir():
            archive.write(file, file.name)


def regional_assessment(sources, rows, region, mode, selected, out, progress, check, output, focused=False):
    nationwide_table = region == 'Nationwide' and focused and output == 'table'
    areas = []
    feature_count = 0
    stats = defaultdict(lambda: defaultdict(float))
    zones = defaultdict(float)
    summaries = []
    required = {'npaaad', 'safdz'} if mode == 'combined' else {mode}
    matrix_areas = {role: defaultdict(lambda: defaultdict(float))
                    for role in ('npaaad', 'safdz') if role in required} if output == 'table' else {}
    eligible = [row for row in rows if row['land'] and required.issubset(row['zones'])]
    missing = [row['name'] for row in rows if row not in eligible]
    if not eligible:
        raise DataError('Required GIS layers are missing for this region.')
    target = out / 'assessment.gpkg'
    archive_bounds = {} if focused and output == 'table' else None
    for index, row in enumerate(eligible):
        check()
        def stage(value, message):
            progress(int(92 * (index + value / 100) / len(eligible)),
                     f"{row['name']} ({index + 1}/{len(eligible)}) · {message}")

        with tempfile_directory(out) as temp:
            folder = Path(temp)
            result = assessments(sources, row, mode, selected, folder, stage, check, output, intermediate=True, focused=focused,
                                 archive_bounds=archive_bounds)
            feature_count += result['features']
            if result['features'] and not (focused and output == 'table'):
                frame = pyogrio.read_dataframe(folder / 'assessment.gpkg')
                pyogrio.write_dataframe(frame, target, layer='assessment', append=target.exists(),
                                        geometry_type='MultiPolygon', promote_to_multi=True)
        for role, crop_areas in result['matrix_areas'].items():
            for crop, values in crop_areas.items():
                for code, area in values.items():
                    matrix_areas[role][crop][code] += area
        if not nationwide_table:
            areas.append(sources.province(row['id']).to_crs(CRS_AREA).geometry.map(geom_polygon).union_all().simplify(80, preserve_topology=True))
        summaries.append({'province': row['name'], **result['qa']})
        for key, value in result['qa']['agricultural_zone_area_ha'].items():
            zones[key] += value
        for crop, values in result['stats'].items():
            for key, value in values.items():
                if not key.endswith('(%)'):
                    stats[crop][key] += value
                elif not key.startswith('Share of '):
                    stats[crop][key] += value * values['Total mapped']

    for values in stats.values():
        for key in list(values):
            if key.endswith('(%)'):
                values[key] /= values['Total mapped']
        if zones['safdz']:
            values['Share of assessed SAFDZ (%)'] = 100 * values.get('Agricultural SAFDZ', 0) / zones['safdz']

    frame = outline = None
    bounds = []
    if not nationwide_table:
        coverage = gpd.GeoDataFrame(geometry=[sh.union_all(areas)], crs=CRS_AREA)
        outline = gpd.GeoDataFrame(geometry=[sh.union_all([
            sources.province(row['id']).to_crs(CRS_AREA).geometry.map(geom_polygon).union_all().simplify(80, preserve_topology=True)
            for row in rows
        ])], crs=CRS_AREA) if missing else coverage
        frame = pyogrio.read_dataframe(target) if target.exists() else gpd.GeoDataFrame(
            {'crop_name': [], 'assessment': [], 'area_ha': []}, geometry=[], crs=CRS_AREA)
        if not target.exists():
            pyogrio.write_dataframe(frame, target, layer='assessment', geometry_type='MultiPolygon')
        bounds = list(outline.to_crs('EPSG:4326').total_bounds)
    unmapped = defaultdict(float)
    for summary in summaries:
        for label, area in summary['unmapped_land_classes_ha'].items():
            unmapped[label] += area
    area_ha = sum(i['assessment_area_ha'] for i in summaries) if focused else coverage.area.iloc[0] / 10000
    qa = {'land_polygons': sum(i['land_polygons'] for i in summaries),
          'source_crop_labels': sorted({crop for i in summaries for crop in i['source_crop_labels']}),
          'unmapped_land_classes_ha': dict(unmapped),
          'assessment_area_ha': area_ha, 'missing_provinces': missing,
          'province_results': summaries, 'agricultural_zone_area_ha': dict(zones),
          'province_area_ha': area_ha,
          'repaired_geometries': sum(i['repaired_geometries'] for i in summaries),
          'slivers_under_1m2': sum(i['slivers_under_1m2'] for i in summaries),
          'area_reconciliation_ha': sum(i['area_reconciliation_ha'] for i in summaries)}
    result = {'province': region, 'region': region, 'area': region, 'level': 'Nationwide' if region == 'Nationwide' else 'Region',
              'mode': mode, 'stats': stats, 'qa': qa, 'missing_provinces': missing,
              'bounds': bounds, 'features': feature_count, 'colors': COLORS, 'focused': focused}
    progress(95, 'Preparing selected output')
    if matrix_areas:
        result['zone_tables'] = zone_tables(matrix_areas, stats)
    write_products(out, result, frame, outline, output)
    return result


def landuse_crop_options(sources, rows, out, progress, check):
    aliases = {canonical(key): value for key, value in json.loads(os.getenv('PCLAM_CROP_MAPPING', '{}')).items()}
    archives = {}
    for index, row in enumerate(rows):
        check()
        for meta in row['land']:
            entry = archives.setdefault(meta['Key'], (meta, []))
            entry[1].append(index)
    if not archives:
        raise DataError('Land-use data is unavailable for this area.')
    boundaries = {}
    options = defaultdict(set)
    qa = {'repaired_geometries': 0}
    seen = set()

    def wanted(raw, code):
        return (raw, code) not in seen and crop_name(raw, aliases, code) in CROPS

    for index, (meta, indices) in enumerate(archives.values()):
        check()
        progress(int(95 * index / len(archives)), f'Reading land-use archive {index + 1} of {len(archives)}')
        for path in sources.land_files(meta, out):
            pending = frozenset(indices)
            if pending not in boundaries:
                shapes = []
                for row_index in sorted(pending):
                    row = rows[row_index]
                    frame = sources.boundary(row) if row.get('municipality') else sources.province(row['id'])
                    shapes.extend(frame.to_crs(CRS_AREA).geometry.map(geom_polygon))
                boundaries[pending] = gpd.GeoDataFrame(geometry=[sh.union_all(shapes)], crs=CRS_AREA)
            boundary = boundaries[pending]
            if boundary.geometry.iloc[0].is_empty:
                continue
            for raw, _, attrs in source_rows(path, 'landuse', boundary, qa, check, wanted=wanted):
                crop = crop_name(raw, aliases, land_use_code(attrs))
                if crop in CROPS:
                    labels = options[crop]
                    if raw and raw.lower() != 'nan':
                        labels.add(raw)
                    seen.add((raw, land_use_code(attrs)))
    return {'crops': [{'name': crop, 'labels': sorted(labels)} for crop, labels in sorted(options.items())]}
