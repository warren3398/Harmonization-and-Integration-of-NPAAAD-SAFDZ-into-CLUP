"""Print layout for PCLAM satellite JPEGs."""
from io import BytesIO
from math import floor, log10
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont
from pyproj import Geod, Transformer
from shapely import get_parts

PAGE = (3840, 2406)
FRAME = (94, 80, 2584, 2270)
GREEN = '#005b3c'
INK = '#202d27'
LINE = '#bdcec3'
PALE = '#e8f1ea'
MAP_CRS = 'EPSG:3857'


def font(size, bold=False):
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size=size)


def satellite_image(bounds, size):
    params = urlencode({'bbox': ','.join(map(str, bounds)), 'bboxSR': 3857, 'imageSR': 3857,
                        'size': f'{size[0]},{size[1]}', 'format': 'jpg', 'f': 'image'})
    url = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?' + params
    try:
        with urlopen(url, timeout=12) as response:
            data = response.read(15_000_001)
        if len(data) > 15_000_000:
            return None
        with Image.open(BytesIO(data)) as image:
            return image.convert('RGB').resize(size, Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def draw_scale(draw, bounds, step):
    to_geo = Transformer.from_crs(MAP_CRS, 'EPSG:4326', always_xy=True)
    x, y = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    start = to_geo.transform(x, y)
    end = to_geo.transform(x + 340 * step, y)
    maximum = Geod(ellps='WGS84').inv(*start, *end)[2]
    if maximum <= 0:
        return
    unit = 10 ** floor(log10(maximum))
    metres = max(factor * unit for factor in (1, 2, 5) if factor * unit <= maximum)
    length = round(340 * metres / maximum)
    x, y = FRAME[0] + 60, FRAME[3] - 68
    draw.rectangle((x - 30, y - 54, x + length + 85, y + 46), fill='white', outline=LINE)
    for index in range(4):
        draw.rectangle((x + length * index / 4, y, x + length * (index + 1) / 4, y + 13),
                       fill=INK if index % 2 == 0 else 'white', outline=INK, width=2)
    label = f'{metres / 1000:g} km' if metres >= 1000 else f'{metres:g} m'
    draw.text((x, y - 7), '0', font=font(22), anchor='ms', fill=INK)
    draw.text((x + length, y - 7), label, font=font(22), anchor='ms', fill=INK)
    draw.text((x, y + 22), 'Scale at map center', font=font(19), fill=INK)


def draw_jpeg(target, result, view, boundary):
    left, top, right, bottom = FRAME
    width, height = right - left, bottom - top
    outline = boundary.to_crs(MAP_CRS).geometry.union_all()
    xmin, ymin, xmax, ymax = outline.bounds
    step = max((xmax - xmin) / (width - 320), (ymax - ymin) / (height - 300))
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    west, north = cx - step * width / 2, cy + step * height / 2
    bounds = west, north - step * height, west + step * width, north
    imagery = satellite_image(bounds, (width, height))
    has_imagery = imagery is not None
    raster = imagery if has_imagery else Image.new('RGB', (width, height), '#edf1ec')

    def points(ring):
        return [((x - west) / step, (north - y) / step) for x, y, *_ in ring.coords]

    if not has_imagery:
        for polygon in get_parts(outline):
            ImageDraw.Draw(raster).polygon(points(polygon.exterior), fill='#dce7da')
    classes = result['colors']
    active = []
    for record in view.to_crs(MAP_CRS).itertuples():
        label = record.assessment
        mask = Image.new('L', raster.size)
        mask_draw = ImageDraw.Draw(mask)
        for polygon in get_parts(record.geometry):
            mask_draw.polygon(points(polygon.exterior), fill=178 if has_imagery else 255)
            for hole in polygon.interiors:
                mask_draw.polygon(points(hole), fill=0)
        raster.paste(classes[label], mask=mask)
        active.append(label)
        del mask_draw, mask

    for polygon in get_parts(outline):
        ImageDraw.Draw(raster).line(points(polygon.exterior), fill='#fff1b2' if has_imagery else GREEN, width=4)
        for hole in polygon.interiors:
            ImageDraw.Draw(raster).line(points(hole), fill='#fff1b2' if has_imagery else GREEN, width=3)

    page = Image.new('RGB', PAGE, 'white')
    page.paste(raster, (left, top))
    del raster, imagery
    draw = ImageDraw.Draw(page)
    draw.rectangle((43, 33, PAGE[0] - 43, PAGE[1] - 33), outline='#59645d', width=4)
    draw.rectangle(FRAME, outline=LINE, width=3)
    draw_scale(draw, bounds, step)
    x, y = right - 102, top + 112
    draw.rectangle((x - 65, y - 79, x + 65, y + 89), fill='white', outline=LINE)
    draw.text((x, y - 52), 'N', font=font(29, True), fill=INK, anchor='mm')
    draw.polygon([(x, y - 19), (x - 41, y + 68), (x, y + 45), (x + 41, y + 68)], fill=INK)
    draw.polygon([(x, y - 7), (x - 32, y + 55), (x, y + 39)], fill='white')

    x0, x1 = 2670, 3750
    center = (x0 + x1) // 2

    def band(y, height, title, size):
        draw.rectangle((x0, y, x1, y + height), fill=GREEN)
        draw.text((center, y + height / 2), title, font=font(size, True), fill='white', anchor='mm')

    band(top, 142, 'CROP-BASED LAND USE', 48)
    draw.text((center, 270), 'ASSESSMENT MAP', font=font(37, True), fill=GREEN, anchor='mm')
    title = result['area'].upper()
    size = 46
    while size > 20 and draw.textlength(title, font=font(size, True)) > x1 - x0 - 50:
        size -= 2
    draw.text((center, 340), title, font=font(size, True), fill=INK, anchor='mm')
    mode = {'combined': 'Combined NPAAAD and SAFDZ', 'npaaad': 'NPAAAD assessment',
            'safdz': 'SAFDZ assessment'}[result['mode']]
    draw.text((center, 405), mode, font=font(29), fill=INK, anchor='mm')
    band(463, 77, 'ASSESSMENT LEGEND', 34)
    row_y = 580
    for label in active:
        draw.rectangle((x0 + 34, row_y + 7, x0 + 91, row_y + 43), fill=classes[label], outline=LINE)
        draw.text((x0 + 115, row_y + 25), label, font=font(31), fill=INK, anchor='lm')
        row_y += 62
    if not active:
        draw.text((x0 + 34, row_y + 25), 'No assessment classes mapped', font=font(29), fill=INK, anchor='lm')
        row_y += 62
    draw.line((x0 + 34, row_y + 25, x0 + 91, row_y + 25), fill='#aa8a37' if has_imagery else GREEN, width=5)
    draw.text((x0 + 115, row_y + 25), 'Selected boundary', font=font(31), fill=INK, anchor='lm')
    row_y += 99

    band(row_y, 77, 'AREA SUMMARY', 34)
    row_y += 77
    draw.rectangle((x0, row_y, x1, row_y + 54), fill=PALE, outline=LINE)
    draw.text((x0 + 32, row_y + 27), 'Class', font=font(29, True), fill=INK, anchor='lm')
    value_x = x1 - 216
    draw.text((value_x, row_y + 27), 'Area (ha)', font=font(29, True), fill=INK, anchor='rm')
    draw.text((x1 - 32, row_y + 27), 'Share', font=font(29, True), fill=INK, anchor='rm')
    row_y += 54
    areas = {label: sum(row.get(label, 0.) for row in result['stats'].values()) for label in active}
    total = sum(areas.values())
    for label, hectares in areas.items():
        draw.rectangle((x0, row_y, x1, row_y + 52), outline=LINE)
        draw.text((x0 + 32, row_y + 26), label, font=font(28), fill=INK, anchor='lm')
        draw.text((value_x, row_y + 26), f'{hectares:,.2f}', font=font(28), fill=INK, anchor='rm')
        draw.text((x1 - 32, row_y + 26), f'{hectares / total:.1%}' if total else '0.0%',
                  font=font(28), fill=INK, anchor='rm')
        row_y += 52
    draw.rectangle((x0, row_y, x1, row_y + 56), fill=PALE, outline=LINE)
    draw.text((x0 + 32, row_y + 28), 'Total displayed', font=font(29, True), fill=INK, anchor='lm')
    draw.text((value_x, row_y + 28), f'{total:,.2f}', font=font(29, True), fill=INK, anchor='rm')
    draw.text((x1 - 32, row_y + 28), '100%' if total else '0%', font=font(29, True), fill=INK, anchor='rm')
    if result.get('missing_provinces'):
        draw.text((x0, row_y + 85), 'Partial regional coverage', font=font(28, True), fill='#8d351f')

    crops = sorted(result['stats'])
    if crops:
        label = ', '.join(crops[:3]) + (f' +{len(crops) - 3} more' if len(crops) > 3 else '')
        y = max(1750, row_y + 155)
        draw.text((x0, y), 'CROPS ASSESSED', font=font(25, True), fill=GREEN)
        size = 28
        while size > 18 and draw.textlength(label, font=font(size)) > x1 - x0:
            size -= 1
        draw.text((x0, y + 43), label, font=font(size), fill=INK)

    draw.line((x0, 2130, x1, 2130), fill=LINE, width=2)
    draw.text((x0, 2155), 'Philippine Land Use 2020-2022', font=font(28, True), fill=INK)
    draw.text((x0, 2203), 'Area calculations: EPSG:6933   |   Map: EPSG:3857', font=font(23), fill=INK)
    credit = ('Imagery: Esri, Maxar, Earthstar Geographics and the GIS User Community'
              if has_imagery else 'Satellite imagery unavailable at export time')
    draw.text((left, 2303), credit, font=font(24), fill=INK)
    page.save(target, quality=93, subsampling=0, dpi=(200, 200))
