"""Provincial overlays in equal-area metres and original-precision vector exports."""
import json
import os
import re
from collections import defaultdict
from functools import lru_cache
from math import cos, floor, log10, radians
from pathlib import Path

import geopandas as gpd
import pyogrio
import shapely as sh
from shapely.geometry import Polygon, box

from sources import ZONES, DataError, canonical

CRS_AREA = 'EPSG:6933'
NP = {'A', 'B', 'C', 'D', 'E', 'G'}
SA = {str(i) for i in range(1, 8)}
VALID = {'npaaad': NP | {'F', 'H', 'BU'}, 'safdz': SA | {'8', '9', '10', 'BU', 'WB'}}
CROPS = ('Rice', 'Irrigated Rice', 'Rainfed Rice', 'Upland Rice', 'Corn', 'Sweet Corn', 'Coconut', 'Mango', 'Banana',
         'Coffee', 'Cacao', 'Onion', 'Garlic', 'Pineapple', 'Sugarcane', 'Abaca', 'Cassava',
         'Sweet Potato', 'Potato', 'Durian', 'Avocado', 'Rubber', 'Peanut', 'Mungbean', 'Soybean',
         'Sorghum', 'Vegetables', 'Strawberry', 'Citrus', 'Tobacco', 'Papaya', 'Oil Palm',
         'Dragon Fruit', 'Rambutan', 'Cut Flowers', 'Diversified Crops', 'Mixed Fruit Trees', 'Other Agricultural Crops')
COLORS = {'Both': '#187354', 'NPAAAD only': '#4e65aa', 'SAFDZ only': '#a763b0',
          'Outside both': '#e6963e', 'Missing coverage': '#7d8799', 'Within NPAAAD': '#187354',
          'Outside NPAAAD': '#e6963e', 'Within SAFDZ': '#187354', 'Outside SAFDZ': '#e6963e'}
CROP_MAP = {canonical(name): name for name in CROPS}
CROP_MAP.update({'mixedvegetables': 'Vegetables', 'diversifiedcrop': 'Diversified Crops', 'abacca': 'Abaca', 'maize': 'Corn', 'sugarcaneplantation': 'Sugarcane', 'coconutplantation': 'Coconut',
                 'palay': 'Rice', 'ricelandirrigated': 'Irrigated Rice', 'ricelandrainfed': 'Rainfed Rice'})
# BSWM Map Guidebook, Table 5 (Land Use). Keep the source abbreviation
# in lu_original and use its documented irrigation type for crop selection.
BSWM_RICE_CODES = {'pri': 'Irrigated Rice', 'prn': 'Rainfed Rice',
                   'tri': 'Irrigated Rice', 'trn': 'Rainfed Rice'}
RICE_TYPES = {'Irrigated Rice', 'Rainfed Rice', 'Upland Rice'}
NON_CROP = {'buildup', 'builtup', 'forest', 'grassland', 'shrubland', 'shrubs', 'water', 'fishpond',
            'mangrove', 'river', 'riverwash', 'reservoir', 'lake', 'pasture', 'quarry', 'barrenland'}
ZONE_COLUMNS = {'safdz': [str(i) for i in range(1, 11)] + ['BU', 'Others'],
                'npaaad': list('ABCDEFGH') + ['BU', 'Others']}
ZONE_LABELS = {'safdz': {'1': 'Strategic Crop Sub-Development Zone',
                        '2': 'Strategic Livestock Sub-Development Zone'}, 'npaaad': {}}
EXPORTS = {'csv', 'npaaad.csv', 'safdz.csv', 'xlsx', 'pdf', 'geojson', 'gpkg', 'shp.zip', 'map.png', 'map.jpg', 'map.pdf', 'qa.json'}


def crop_name(raw, aliases):
    key = canonical(raw)
    if key in aliases:
        return aliases[key]
    if key in BSWM_RICE_CODES:
        return BSWM_RICE_CODES[key]
    if key in CROP_MAP:
        return CROP_MAP[key]
    simplified = re.sub(r'\s*\([^()]*\)\s*$', '', raw.strip())
    # Prefer explicit names; codes alone cannot establish a crop identity.
    return CROP_MAP.get(canonical(simplified))


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
    fields = {'landuse': ['LANDUSE', 'LAND_USE', 'LU_DESC', 'DESCRIPTION', 'CLASS'],
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


def source_rows(path, role, province, qa, check, wanted=None):
    layer, info, field = source_schema(path, role)
    bbox = tuple(province.to_crs(info['crs']).total_bounds)
    bound = province.geometry.union_all()
    extra = [f for f in info['fields'] if f.upper() in {'MUN_NAME', 'ORIG_FID', 'LU_CODE', 'LANDUSE_CODE'}]
    # Use the GeoPackage spatial index before fetching geometry. Seek by FID
    # across batches so a large part cannot make memory grow with feature count.
    batch_size = 64 if role == 'landuse' else 16
    offset = 0
    while True:
        check()
        batch = pyogrio.read_dataframe(path, layer=layer, columns=[field, *extra], bbox=bbox,
                                        skip_features=offset, max_features=batch_size, force_2d=True)
        if batch.empty:
            break
        count = len(batch)
        offset += count
        if wanted is not None:
            batch = batch[batch[field].map(lambda value: wanted(str(value or '').strip()))]
        batch = batch.to_crs(CRS_AREA)
        for record in batch.to_dict('records'):
            geom = record.pop('geometry')
            if geom is not None and not geom.is_valid:
                qa['repaired_geometries'] += 1
            geom = geom_polygon(geom)
            if geom.is_empty or not geom.intersects(bound):
                continue
            geom = geom_polygon(geom.intersection(bound))
            if geom.area > 0:
                raw = str(record.pop(field) or '').strip()
                yield raw, geom, record
        if count < batch_size:
            break


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

def assessments(sources, row, mode, selected, out, progress, check, output=None, intermediate=False, focused=False):
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
    zone_files = {}
    zone_indices = {}
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

    @lru_cache(maxsize=3)
    def tile_masks(tile, cell):
        local = geom_polygon(boundary.intersection(cell))
        if local.is_empty:
            return {}
        tile_province = gpd.GeoDataFrame(geometry=[local], crs=CRS_AREA)
        masks = {}
        tile_qa = {'repaired_geometries': 0, 'source_overlap_ha': {}}
        areas = {}
        for role, path in zone_files.items():
            records = local_zone_rows(zone_indices[role], local) if role in zone_indices else (
                source_rows(path, role, tile_province, tile_qa, check))
            masks[role], ag = zone_masks(records, role, tile_qa, check, measure=not focused)
            if ag is not None:
                areas[role] = ag.area
        if tile not in measured:
            measured[tile] = areas
            qa['repaired_geometries'] += tile_qa['repaired_geometries']
            for role, area in tile_qa['source_overlap_ha'].items():
                qa['source_overlap_ha'][role] = qa['source_overlap_ha'].get(role, 0) + area
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
    for index, meta in enumerate(sorted(row['land'], key=lambda m: m['Key'])):
        check()
        progress(35 + int(51 * index / total), f'Reading regional land use archive {index + 1} of {total}')
        qa['land_objects'].append({key: meta[key] for key in ('Key', 'ETag')})
        for path in sources.land_files(meta, out):
            for raw, geom, attrs in source_rows(path, 'landuse', province, qa, check):
                qa['land_polygons'] += 1
                crop = crop_name(raw, aliases)
                if not crop:
                    if canonical(raw) not in NON_CROP:
                        unmapped = qa['unmapped_land_classes_ha']
                        unmapped[raw or '(blank)'] = unmapped.get(raw or '(blank)', 0) + geom.area / 10000
                    continue
                observed_crops.add(crop)
                if selected and crop not in selected and not ('Rice' in selected and crop in RICE_TYPES):
                    continue
                for tile, cell in cells(geom):
                    portion = geom_polygon(geom.intersection(cell))
                    if not portion.area:
                        continue
                    remaining = geom_polygon(portion.difference(occupied.get(tile, Polygon())))
                    qa['land_overlap_ha'] += max(0., portion.area - remaining.area) / 10000
                    if remaining.is_empty:
                        continue
                    occupied[tile] = sh.union(occupied.get(tile, Polygon()), remaining)
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
                                            'lu_code': str(attrs.get('LU_CODE', attrs.get('LANDUSE_CODE', ''))),
                                            'source_fid': str(attrs.get('ORIG_FID', '')), 'crop_name': crop,
                                            'npaaad_code': np, 'safdz_code': sa, 'assessment': category,
                                            'area_ha': area, 'data_year': '2020–2022', 'geometry': result})
                            if len(pending) >= 250:
                                flush(pending, export_path)
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
    tile_masks.cache_clear()
    if focused:
        qa['agricultural_zone_area_ha'] = {}
    else:
        for tile, cell in cells(boundary):
            if tile not in measured:
                check()
                tile_masks(tile, cell)
                tile_masks.cache_clear()
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
                sheet.row_dimensions[1].height = 65
                for number, column in enumerate(sheet.columns, start=1):
                    sheet.column_dimensions[get_column_letter(number)].width = 24 if number == 1 else 15
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
    if output in (None, 'jpeg'):
        map_crs = 'EPSG:3857' if output == 'jpeg' else CRS_AREA
        map_area = province.to_crs(map_crs)
        map_view = view.to_crs(map_crs)
        width, height = 1560, 1100
        image = Image.new('RGB', (width, height), '#e1eee9')
        draw = ImageDraw.Draw(image)
        xmin, ymin, xmax, ymax = map_area.total_bounds
        scale = min((width - 520) / (xmax - xmin), (height - 180) / (ymax - ymin))
        satellite = False
        if output == 'jpeg':
            # Match the Esri image footprint to the exact map pixel rectangle.
            satellite = add_satellite_background(image, xmin, ymin, scale)
        def xy(coords):
            return [(65 + (x - xmin) * scale, height - 75 - (y - ymin) * scale) for x, y, *_ in coords]
    
        def paint(geometry, fill, draw_on, outline=None):
            for poly in sh.get_parts(geom_polygon(geometry)):
                draw_on.polygon(xy(poly.exterior.coords), fill=fill, outline=outline)
                for hole in poly.interiors:
                    draw_on.polygon(xy(hole.coords), fill=0 if isinstance(fill, int) else '#e1eee9')
    
        land = map_area.geometry.union_all()
        if satellite:
            for part in sh.get_parts(geom_polygon(land)):
                draw.line(xy(part.exterior.coords), fill='#fff0b0', width=3)
        else:
            paint(land, '#f5f5e8', draw, '#496158')
        active = []
        for feature in map_view.itertuples():
            mask = Image.new('1', image.size)
            paint(feature.geometry, 1, ImageDraw.Draw(mask))
            if satellite:
                image.paste(COLORS[feature.assessment], mask=mask.convert('L').point([0] * 255 + [158]))
            else:
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
        latitude = (result['bounds'][1] + result['bounds'][3]) / 2
        ratio = cos(radians(latitude)) if output == 'jpeg' else 1
        bar_m = max(1, round(140 * ratio / scale, -int(log10(max(140 * ratio / scale, 1)))))
        bar_px = bar_m * scale / ratio
        bar_label = f'{bar_m / 1000:g} km' if bar_m >= 1000 else f'{bar_m:g} m'
        draw.line((1160, 515, 1160 + bar_px, 515), fill='#14392f', width=4)
        draw.text((1160, 526), f'{bar_label} (approx.)', fill='#14392f', font_size=16)
        if result.get('missing_provinces'):
            draw.text((1160, 565), 'Partial regional coverage', fill='#8d351f', font_size=20)
        if output == 'jpeg':
            source = ('Imagery: Esri, Maxar, Earthstar Geographics and the GIS User Community'
                      if satellite else 'Satellite imagery unavailable at export time')
            draw.text((65, height - 38), source, fill='#14392f', font_size=14)
            image.save(out / 'assessment.map.jpg', quality=95, subsampling=0, dpi=(180, 180))
        else:
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
    for index, row in enumerate(eligible):
        check()
        def stage(value, message):
            progress(int(92 * (index + value / 100) / len(eligible)),
                     f"{row['name']} ({index + 1}/{len(eligible)}) · {message}")

        with tempfile_directory(out) as temp:
            folder = Path(temp)
            result = assessments(sources, row, mode, selected, folder, stage, check, output, intermediate=True, focused=focused)
            feature_count += result['features']
            if result['features'] and not (focused and output == 'table'):
                frame = pyogrio.read_dataframe(folder / 'assessment.gpkg')
                pyogrio.write_dataframe(frame, target, layer='assessment', append=target.exists(),
                                        geometry_type='MultiPolygon', promote_to_multi=True)
        for role, crop_areas in result['matrix_areas'].items():
            for crop, values in crop_areas.items():
                for code, area in values.items():
                    matrix_areas[role][crop][code] += area
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

    coverage = gpd.GeoDataFrame(geometry=[sh.union_all(areas)], crs=CRS_AREA)
    outline = gpd.GeoDataFrame(geometry=[sh.union_all([
        sources.province(row['id']).to_crs(CRS_AREA).geometry.map(geom_polygon).union_all().simplify(80, preserve_topology=True)
        for row in rows
    ])], crs=CRS_AREA) if missing else coverage
    frame = pyogrio.read_dataframe(target) if target.exists() else gpd.GeoDataFrame(
        {'crop_name': [], 'assessment': [], 'area_ha': []}, geometry=[], crs=CRS_AREA)
    if not target.exists():
        pyogrio.write_dataframe(frame, target, layer='assessment', geometry_type='MultiPolygon')
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
              'bounds': list(outline.to_crs('EPSG:4326').total_bounds), 'features': feature_count, 'colors': COLORS, 'focused': focused}
    progress(95, 'Preparing selected output')
    if matrix_areas:
        result['zone_tables'] = zone_tables(matrix_areas, stats)
    write_products(out, result, frame, outline, output)
    return result


def add_satellite_background(image, xmin, ymin, scale):
    from io import BytesIO
    from urllib.error import URLError
    from urllib.parse import urlencode
    from urllib.request import urlopen
    from PIL import Image

    left, top, right, bottom = 52, 109, 1125, image.height - 63
    width, height = right - left, bottom - top
    origin = image.height - 75
    bounds = (xmin + (left - 65) / scale, ymin + (origin - bottom) / scale,
              xmin + (right - 65) / scale, ymin + (origin - top) / scale)
    params = urlencode({'bbox': ','.join(map(str, bounds)), 'bboxSR': 3857, 'imageSR': 3857,
                        'size': f'{width},{height}', 'format': 'jpg', 'f': 'image'})
    try:
        url = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?' + params
        with urlopen(url, timeout=7) as response:
            data = response.read(5_000_000)
        with Image.open(BytesIO(data)) as imagery:
            image.paste(imagery.convert('RGB').resize((width, height)), (left, top))
        return True
    except (URLError, OSError, ValueError):
        return False


def landuse_crop_options(sources, rows, out, progress, check, nationwide=False):
    aliases = {canonical(key): value for key, value in json.loads(os.getenv('PCLAM_CROP_MAPPING', '{}')).items()}
    archives = {}
    for index, row in enumerate(rows):
        check()
        for meta in row['land']:
            entry = archives.setdefault(meta['Key'], (meta, []))
            if not nationwide:
                entry[1].append(index)
    if not archives:
        raise DataError('Land-use data is unavailable for this area.')
    boundaries = {}
    options = defaultdict(set)
    qa = {'repaired_geometries': 0}
    seen = set()

    def wanted(raw):
        return raw not in seen and crop_name(raw, aliases) in CROPS

    for index, (meta, indices) in enumerate(archives.values()):
        check()
        progress(int(95 * index / len(archives)), f'Reading land-use archive {index + 1} of {len(archives)}')
        for path in sources.land_files(meta, out):
            if nationwide:
                layer, _, field = source_schema(path, 'landuse')
                values = pyogrio.read_dataframe(path, layer=layer, columns=[field], read_geometry=False)[field]
                labels = (str(value).strip() for value in values.dropna().unique())
                for raw in labels:
                    crop = crop_name(raw, aliases)
                    if crop in CROPS:
                        options[crop].add(raw)
            else:
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
                for raw, _, _ in source_rows(path, 'landuse', boundary, qa, check, wanted=wanted):
                    crop = crop_name(raw, aliases)
                    if crop in CROPS:
                        options[crop].add(raw)
                        seen.add(raw)
    return {'crops': [{'name': crop, 'labels': sorted(labels)} for crop, labels in sorted(options.items())]}
