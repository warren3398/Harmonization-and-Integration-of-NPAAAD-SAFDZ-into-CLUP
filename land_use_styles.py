"""The user's land-use palette, portable color attributes, and GIS styles."""
import csv
import re
import unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET

PALETTE = [
    ('Residential','Yellow','#FFFF00'),
    ('Commercial','Red','#FF0000'),
    ('Industrial','Violet','#8C00C8'),
    ('Agricultural','Green','#009600'),
    ('Forest','Dark Green','#006400'),
    ('Parks / Recreation','Light Green','#64E164'),
    ('Mining / Quarrying','Brown','#993300'),
    ('Grassland / Pasture','Olive Green','#5A7D28'),
    ('Agro-industrial','Light Violet','#C896FF'),
    ('Tourism','Orange','#FF6600'),
    ('Water Uses','Light Blue','#AFD7E6'),
]
FALLBACK = ('Gray','Gray','#D9D9D9')
RULES = {
    'Residential':r'\b(residential|res|r[123])\b',
    'Commercial':r'\b(commercial|com|c[123])\b',
    'Industrial':r'\b(industrial|industry|ind|i[123])\b',
    'Agricultural':r'\b(agricultural|agriculture|agri|agr|cropland|croplands)\b',
    'Forest':r'\b(forest|forestry|forestland|forestlands)\b',
    'Parks / Recreation':r'\b(park|parks|recreation|recreational)\b',
    'Mining / Quarrying':r'\b(mining|mine|quarry|quarries|quarrying)\b|\bmineral\s+land\b',
    'Grassland / Pasture':r'\b(grassland|grasslands|pasture|pastureland)\b',
    'Tourism':r'\b(tourism|tourist|resort|resorts)\b',
    'Water Uses':r'\b(water|waterbody|waterbodies|river|rivers|lake|lakes|reservoir|reservoirs|wb)\b',
}


def category(value):
    text=unicodedata.normalize('NFKC',str(value or '')).lower()
    text=re.sub(r'[^a-z0-9]+',' ',text).strip()
    # The compound is its own class, not the Industrial class it contains.
    agro=bool(re.search(r'\bagr[oi]\s*(?:industrial|industry)\b',text))
    rest=re.sub(r'\bagr[oi]\s*(?:industrial|industry)\b',' ',text)
    matches=['Agro-industrial'] if agro else []
    matches.extend(name for name,pattern in RULES.items() if re.search(pattern,rest))
    # Broad BU, unknown codes and mixed descriptions must not silently become
    # Residential or another specific class merely to acquire a color.
    return matches[0] if len(matches)==1 else FALLBACK[0]


def rgb(hex_color):
    return tuple(int(hex_color[index:index+2],16) for index in (1,3,5))


def apply_palette(frame, source_field='ELU_CLS'):
    if source_field not in frame.columns:
        raise ValueError('The land-use style source field is missing: '+source_field)
    result=frame.copy()
    colors={name:color for name,_,color in PALETTE+[FALLBACK]}
    values=result[source_field].fillna('').astype(str).str.strip()
    result['LU_STYLE']=values.map(category)
    result['LU_HEX']=result['LU_STYLE'].map(colors)
    for index,channel in enumerate(('LU_R','LU_G','LU_B')):
        result[channel]=result['LU_HEX'].map(lambda color:rgb(color)[index]).astype('int32')
    unknown=sorted(set(values[result['LU_STYLE'].eq(FALLBACK[0])]))
    return result,{'source_field':source_field,'style_field':'LU_STYLE','unmapped_values':unknown,
                   'unmapped_features':int(result['LU_STYLE'].eq(FALLBACK[0]).sum()),
                   'fallback_color':FALLBACK[2],'palette':dict((name,color) for name,_,color in PALETTE)}


def write_styles(shapefile):
    path=Path(shapefile)
    classes=PALETTE+[FALLBACK]
    root=ET.Element('qgis',version='3.34.0',styleCategories='Symbology')
    renderer=ET.SubElement(root,'renderer-v2',type='categorizedSymbol',attr='LU_STYLE',symbollevels='0',enableorderby='0')
    categories=ET.SubElement(renderer,'categories')
    symbols=ET.SubElement(renderer,'symbols')
    for index,(name,_,color) in enumerate(classes):
        ET.SubElement(categories,'category',value=name,label=name,symbol=str(index),render='true')
        symbol=ET.SubElement(symbols,'symbol',name=str(index),type='fill',alpha='1',clip_to_extent='1')
        layer=ET.SubElement(symbol,'layer',{'class':'SimpleFill','enabled':'1','pass':'0','locked':'0'})
        options=ET.SubElement(layer,'Option',type='Map')
        values={'color':','.join(map(str,(*rgb(color),255))),'outline_color':'85,85,85,255',
                'outline_style':'solid','outline_width':'0.15','outline_width_unit':'MM','style':'solid','joinstyle':'bevel'}
        for key,value in values.items():ET.SubElement(options,'Option',name=key,type='QString',value=value)
    ET.ElementTree(root).write(path.with_suffix('.qml'),encoding='utf-8',xml_declaration=True)
    sld='http://www.opengis.net/sld';ogc='http://www.opengis.net/ogc'
    ET.register_namespace('sld',sld);ET.register_namespace('ogc',ogc)
    root=ET.Element('{'+sld+'}StyledLayerDescriptor',version='1.0.0')
    named=ET.SubElement(root,'{'+sld+'}NamedLayer');ET.SubElement(named,'{'+sld+'}Name').text=path.stem
    user=ET.SubElement(named,'{'+sld+'}UserStyle');ET.SubElement(user,'{'+sld+'}Title').text='Land Use Classification'
    feature=ET.SubElement(user,'{'+sld+'}FeatureTypeStyle')
    for name,_,color in classes:
        rule=ET.SubElement(feature,'{'+sld+'}Rule');ET.SubElement(rule,'{'+sld+'}Name').text=name
        filter_=ET.SubElement(rule,'{'+ogc+'}Filter');equal=ET.SubElement(filter_,'{'+ogc+'}PropertyIsEqualTo')
        ET.SubElement(equal,'{'+ogc+'}PropertyName').text='LU_STYLE';ET.SubElement(equal,'{'+ogc+'}Literal').text=name
        symbol=ET.SubElement(rule,'{'+sld+'}PolygonSymbolizer');fill=ET.SubElement(symbol,'{'+sld+'}Fill')
        ET.SubElement(fill,'{'+sld+'}CssParameter',name='fill').text=color
        ET.SubElement(fill,'{'+sld+'}CssParameter',name='fill-opacity').text='1'
        stroke=ET.SubElement(symbol,'{'+sld+'}Stroke');ET.SubElement(stroke,'{'+sld+'}CssParameter',name='stroke').text='#555555'
        ET.SubElement(stroke,'{'+sld+'}CssParameter',name='stroke-width').text='0.3'
    ET.ElementTree(root).write(path.with_suffix('.sld'),encoding='utf-8',xml_declaration=True)
    with (path.parent/'LAND_USE_PALETTE.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['Land Use Classification','Color','R','G','B','HEX'])
        for name,label,color in PALETTE:writer.writerow([name,label,*rgb(color),color])
    (path.parent/'STYLE_README.txt').write_text(
        'LAND USE CLASSIFICATION COLORS\n\n'
        'Extract the entire Shapefile ZIP into one folder. Keep the .qml beside the .shp with the same filename.\n'
        'QGIS: the matching .qml is the default style. If needed, use Layer Properties > Symbology > Style > Load Style.\n'
        'Other GIS software: import the SLD if supported, or symbolize LU_STYLE using LAND_USE_PALETTE.csv.\n'
        'LU_HEX and LU_R/LU_G/LU_B also record the exact requested colors in each feature.\n\n'
        'Default colors use the original ELU classification (ELU_CLS). Original classes and Decision values are retained.\n'
        'Unknown, mixed, or broad classes such as BU are shown in neutral gray (Gray); they are not guessed as Residential.\n'
        'The .shp format itself does not embed display styles; keep the accompanying style files.\n',encoding='utf-8')
