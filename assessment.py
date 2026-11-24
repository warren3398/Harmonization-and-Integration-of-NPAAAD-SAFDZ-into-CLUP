"""Provincial overlays in equal-area metres and original-precision vector exports."""
import json
import os
import re
from collections import defaultdict
from functools import lru_cache
from math import floor
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
CROPS = ('Rice', 'Irrigated Rice', 'Rainfed Rice', 'Corn', 'Sweet Corn', 'Coconut', 'Mango', 'Banana',
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
NON_CROP = {'buildup', 'builtup', 'forest', 'grassland', 'shrubland', 'water', 'fishpond', 'mangrove'}
EXPORTS = {'csv', 'xlsx', 'pdf', 'geojson', 'gpkg', 'shp.zip', 'map.png', 'map.pdf', 'qa.json'}


def crop_name(raw, aliases):
    key = canonical(raw)
    if key in aliases:
        return aliases[key]
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


def source_rows(path, role, province, qa, check):
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
    bbox = tuple(province.to_crs(info['crs']).total_bounds)
    bound = province.geometry.union_all()
    extra = [f for f in info['fields'] if f.upper() in {'MUN_NAME', 'ORIG_FID', 'LU_CODE', 'LANDUSE_CODE'}]
    # Use the GeoPackage spatial index before fetching geometry. Seek by FID
    # across batches so a large part cannot make memory grow with feature count.
    offset = 0
    while True:
        check()
        batch = pyogrio.read_dataframe(path, layer=layer, columns=[field, *extra], bbox=bbox,
                                        skip_features=offset, max_features=300, force_2d=True)
        if batch.empty:
            break
        offset += len(batch)
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
        if len(batch) < 300:
            break


def zone_masks(path, role, province, qa, check):
    raw_groups = defaultdict(list)
    for raw, geom, _ in source_rows(path, role, province, qa, check):
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
    agricultural = sh.union_all([geom for code, geom in codes.items() if code in allowed])
    return codes, agricultural


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


def assessments(sources, row, mode, selected, out, progress, check):
    required = ('npaaad', 'safdz') if mode == 'combined' else (mode,)
    missing = [role.upper() for role in required if role not in row['zones']]
    if missing or not row['land']:
        raise DataError('Missing dataset: ' + ', '.join(missing + ([] if row['land'] else ['Land Use 2020–2022'])))
    aliases = {canonical(k): v for k, v in json.loads(os.getenv('PCLAM_CROP_MAPPING', '{}')).items()}
    if any(v not in CROPS and v is not None for v in aliases.values()):
        raise DataError('A configured crop mapping uses an unsupported standard crop name.')
    qa = {'repaired_geometries': 0, 'source_overlap_ha': {}, 'land_overlap_ha': 0.,
          'unmapped_land_classes_ha': {}, 'slivers_under_1m2': 0, 'land_objects': [], 'zone_objects': {}}
    progress(3, 'Reading provincial boundary')
    province = sources.province(row['id']).to_crs(CRS_AREA)
    province.geometry = province.geometry.map(geom_polygon)
    boundary = province.geometry.union_all()
    if boundary.is_empty:
        raise DataError('The selected boundary has no valid polygon.')
    zone_files = {}
    for index, role in enumerate(required):
        meta = row['zones'][role]
        progress(10 + 12 * index, f'Retrieving {role.upper()} source')
        check()
        qa['zone_objects'][role] = {key: meta[key] for key in ('Key', 'ETag')}
        zone_files[role] = sources.file(ZONES, meta['Key'], meta)

    cell_size = 7000
    measured = {}
    def cells(shape):
        xmin, ymin, xmax, ymax = shape.bounds
        for x in range(floor(xmin / cell_size), floor(xmax / cell_size) + 1):
            for y in range(floor(ymin / cell_size), floor(ymax / cell_size) + 1):
                cell = box(x * cell_size, y * cell_size, (x + 1) * cell_size, (y + 1) * cell_size)
                if shape.intersects(cell):
                    yield (x, y), cell

    @lru_cache(maxsize=8)
    def tile_masks(tile, cell):
        local = geom_polygon(boundary.intersection(cell))
        if local.is_empty:
            return {}
        tile_province = gpd.GeoDataFrame(geometry=[local], crs=CRS_AREA)
        masks = {}
        tile_qa = {'repaired_geometries': 0, 'source_overlap_ha': {}}
        areas = {}
        for role, path in zone_files.items():
            masks[role], ag = zone_masks(path, role, tile_province, tile_qa, check)
            areas[role] = ag.area
        if tile not in measured:
            measured[tile] = areas
            qa['repaired_geometries'] += tile_qa['repaired_geometries']
            for role, area in tile_qa['source_overlap_ha'].items():
                qa['source_overlap_ha'][role] = qa['source_overlap_ha'].get(role, 0) + area
        return masks

    stats = defaultdict(lambda: defaultdict(float))
    pending = []
    occupied = {}
    export_path = out / 'assessment.gpkg'
    total = len(row['land'])
    for index, meta in enumerate(sorted(row['land'], key=lambda m: m['Key'])):
        check()
        progress(35 + int(51 * index / total), f'Reading regional land use archive {index + 1} of {total}')
        qa['land_objects'].append({key: meta[key] for key in ('Key', 'ETag')})
        for path in sources.land_files(meta, out):
            for raw, geom, attrs in source_rows(path, 'landuse', province, qa, check):
                crop = crop_name(raw, aliases)
                if not crop:
                    if canonical(raw) not in NON_CROP:
                        unmapped = qa['unmapped_land_classes_ha']
                        unmapped[raw or '(blank)'] = unmapped.get(raw or '(blank)', 0) + geom.area / 10000
                    continue
                if selected and crop not in selected:
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
                    for np, piece in zone_partitions(remaining, masks.get('npaaad', {})):
                        for sa, result in zone_partitions(piece, masks.get('safdz', {})):
                            area = result.area / 10000
                            category = class_name(np, sa, mode)
                            s['Total mapped'] += area
                            s[category] += area
                            if np in NP: s['Agricultural NPAAAD'] += area
                            if sa in SA: s['Agricultural SAFDZ'] += area
                            if np == 'F': s['Fragile agricultural land'] += area
                            if np == 'H': s['Forest / watershed'] += area
                            if np in VALID['npaaad'] - NP: s['Outside agricultural NPAAAD'] += area
                            if sa in VALID['safdz'] - SA: s['Outside agricultural SAFDZ'] += area
                            if area < .0001: qa['slivers_under_1m2'] += 1
                            pending.append({'region': row['region'], 'province': row['name'], 'psgc': row['id'],
                                            'municipality': attrs.get('Mun_Name'), 'lu_original': raw,
                                            'lu_code': str(attrs.get('LU_CODE', attrs.get('LANDUSE_CODE', ''))),
                                            'source_fid': str(attrs.get('ORIG_FID', '')), 'crop_name': crop,
                                            'npaaad_code': np, 'safdz_code': sa, 'assessment': category,
                                            'area_ha': area, 'data_year': '2020–2022', 'geometry': result})
                            if len(pending) >= 250:
                                flush(pending, export_path)
    flush(pending, export_path)
    if not export_path.exists():
        frame = gpd.GeoDataFrame({'crop_name': [], 'assessment': [], 'area_ha': []}, geometry=[], crs=CRS_AREA)
        pyogrio.write_dataframe(frame, export_path, layer='assessment', geometry_type='MultiPolygon')
    progress(90, 'Writing maps and statistical reports')
    qa['province_area_ha'] = boundary.area / 10000
    for tile, cell in cells(boundary):
        if tile not in measured:
            check()
            tile_masks(tile, cell)
    qa['agricultural_zone_area_ha'] = {role: sum(v.get(role, 0) for v in measured.values()) / 10000
                                      for role in required}
    qa['area_reconciliation_ha'] = abs(sum(s['Total mapped'] for s in stats.values()) -
                                      sum(g.area for g in occupied.values()) / 10000)
    frame = pyogrio.read_dataframe(export_path)
    for totals in stats.values():
        for role in required:
            key = f'Agricultural {role.upper()}'
            totals[f'{role.upper()} coverage (%)'] = 100 * totals.get(key, 0) / totals['Total mapped']
        if mode == 'combined':
            totals['Both coverage (%)'] = 100 * totals.get('Both', 0) / totals['Total mapped']
            totals['Outside both (%)'] = 100 * totals.get('Outside both', 0) / totals['Total mapped']
        safdz_ha = qa['agricultural_zone_area_ha'].get('safdz', 0)
        if safdz_ha:
            totals['Share of provincial SAFDZ (%)'] = 100 * totals.get('Agricultural SAFDZ', 0) / safdz_ha
    bounds = list(province.to_crs('EPSG:4326').total_bounds)
    result = {'province': row['name'], 'region': row['region'], 'mode': mode, 'bounds': bounds,
              'stats': stats, 'qa': qa, 'features': len(frame), 'colors': COLORS}
    write_products(out, result, frame, province)
    return result


def flush(pending, path):
    if pending:
        frame = gpd.GeoDataFrame(pending, geometry='geometry', crs=CRS_AREA)
        pyogrio.write_dataframe(frame, path, layer='assessment', append=path.exists(),
                                geometry_type='MultiPolygon', promote_to_multi=True)
        pending.clear()


def write_products(out, result, frame, province):
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
    for crop, stats in sorted(result['stats'].items()):
        matrix.append([crop, *[round(stats.get(key, 0), 6) for key in fields]])
    if len(matrix) > 1:
        totals = [sum(row[i] for row in matrix[1:]) for i in range(1, len(fields) + 1)]
        weights = [row[1] for row in matrix[1:]]
        for i, key in enumerate(fields):
            if key.endswith('(%)') and key != 'Share of provincial SAFDZ (%)' and weights and sum(weights):
                totals[i] = sum(row[i + 1] * row[1] for row in matrix[1:]) / sum(weights)
        matrix.append(['Total', *[round(value, 6) for value in totals]])
    with (out / 'assessment.csv').open('w', newline='', encoding='utf-8-sig') as file:
        csv.writer(file).writerows(matrix)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Crop assessment'
    for line in matrix:
        sheet.append(line)
    sheet.freeze_panes = 'B2'
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(out / 'assessment.xlsx')
    if len(frame):
        with tempfile_directory(out) as directory:
            path = Path(directory)
            labels = dict(zip(frame.columns, frame.columns))
            labels.update({'municipality': 'municipal', 'npaaad_code': 'np_code',
                           'safdz_code': 'sa_code', 'data_year': 'year', 'lu_original': 'lu_orig'})
            pyogrio.write_dataframe(frame.rename(columns=labels), path / 'assessment.shp', encoding='UTF-8')
            (path / 'fields.txt').write_text('Shapefile field names and GeoPackage equivalents\n' +
                                              '\n'.join(f'{value} = {key}' for key, value in labels.items()))
            shutil_make_zip(out / 'assessment.shp.zip', path)
    view = frame.copy()
    view.geometry = view.geometry.simplify(12, preserve_topology=True)
    if len(view):
        view = view.dissolve(by='assessment', as_index=False)
        view = view[['assessment', 'geometry']]
    boundary_geojson = province[['geometry']].to_crs('EPSG:4326').to_json(drop_id=True)
    (out / 'boundary.geojson').write_text(boundary_geojson)
    (out / 'map.geojson').write_text(view.to_crs('EPSG:4326').to_json(drop_id=True))
    if len(frame) <= 15000:
        frame.to_crs('EPSG:4326').to_file(out / 'assessment.geojson', driver='GeoJSON', engine='pyogrio')
        if (out / 'assessment.geojson').stat().st_size > 30_000_000:
            (out / 'assessment.geojson').unlink()
    width, height = 1560, 1100
    image = Image.new('RGB', (width, height), '#e1eee9')
    draw = ImageDraw.Draw(image)
    xmin, ymin, xmax, ymax = province.total_bounds
    scale = min((width - 520) / (xmax - xmin), (height - 180) / (ymax - ymin))
    def xy(coords):
        return [(65 + (x - xmin) * scale, height - 75 - (y - ymin) * scale) for x, y, *_ in coords]

    def paint(geometry, fill, draw_on, outline=None):
        for poly in sh.get_parts(geometry):
            draw_on.polygon(xy(poly.exterior.coords), fill=fill, outline=outline)
            for hole in poly.interiors:
                draw_on.polygon(xy(hole.coords), fill=0 if isinstance(fill, int) else '#e1eee9')

    paint(province.geometry.union_all(), '#f5f5e8', draw, '#496158')
    active = []
    for feature in view.itertuples():
        mask = Image.new('1', image.size)
        paint(feature.geometry, 1, ImageDraw.Draw(mask))
        image.paste(COLORS[feature.assessment], mask=mask)
        active.append(feature.assessment)
    draw.text((52, 39), f"{result['province']} | Provincial Crop-Based Land Use Assessment Map",
              fill='#14392f', font_size=27)
    draw.text((52, 76), 'Land Use 2020–2022 | Equal-area analysis · EPSG:6933', fill='#30594c', font_size=16)
    draw.text((1160, 180), 'ASSESSMENT CLASSES', fill='#14392f', font_size=21)
    for index, label in enumerate(active):
        y = 225 + index * 38
        draw.rectangle((1160, y, 1180, y + 20), fill=COLORS[label])
        draw.text((1190, y), label, fill='#14392f', font_size=17)
    bar_km = max(1, round(140 / scale / 1000))
    bar_px = bar_km * 1000 * scale
    draw.line((1160, 515, 1160 + bar_px, 515), fill='#14392f', width=4)
    draw.text((1160, 526), f'{bar_km} km (projected grid)', fill='#14392f', font_size=16)
    image.save(out / 'assessment.map.png')
    image.save(out / 'assessment.map.pdf', resolution=150)
    styles = getSampleStyleSheet()
    title = escape(result['province']) + ' Crop-Based Land Use Assessment'
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
