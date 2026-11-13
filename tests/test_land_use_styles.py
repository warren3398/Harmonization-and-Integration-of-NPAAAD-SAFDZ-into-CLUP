import csv
import xml.etree.ElementTree as ET
import geopandas as gpd
from shapely.geometry import box
from land_use_styles import PALETTE,FALLBACK,category,apply_palette,write_styles,rgb

EXPECTED={'Residential':'#FFFF00','Commercial':'#FF0000','Industrial':'#8C00C8','Agricultural':'#009600','Forest':'#006400','Parks / Recreation':'#64E164','Mining / Quarrying':'#993300','Grassland / Pasture':'#5A7D28','Agro-industrial':'#C896FF','Tourism':'#FF6600','Water Uses':'#AFD7E6'}

def test_exact_requested_colors_and_unmodified_original_data():
 assert {name:color for name,_,color in PALETTE}==EXPECTED
 original=gpd.GeoDataFrame({'ELU_CLS':list(EXPECTED),'Decision':['Keep']*11},geometry=[box(i,0,i+1,1) for i in range(11)],crs=32651)
 styled,report=apply_palette(original)
 assert list(styled.LU_HEX)==list(EXPECTED.values())
 assert list(styled.ELU_CLS)==list(original.ELU_CLS)
 assert list(styled.Decision)==list(original.Decision)
 assert styled.geometry.equals(original.geometry)
 assert 'LU_STYLE' not in original
 assert report['unmapped_features']==0
 assert list(zip(styled.LU_R,styled.LU_G,styled.LU_B))==[rgb(color) for color in EXPECTED.values()]

def test_aliases_and_unknown_or_mixed_classes():
 assert category('Agri-Industrial')=='Agro-industrial'
 assert category('Agroindustrial')=='Agro-industrial'
 assert category('Production Agriculture')=='Agricultural'
 assert category('Mineral Land')=='Mining / Quarrying'
 assert category('Protection Forest')=='Forest'
 assert category('Parks and Recreation')=='Parks / Recreation'
 for value in ['BU','Institutional','Cemetery','Infrastructure/Utilities, Transportation and Services','Residential / Commercial','']:
  assert category(value)=='Unmapped'

def test_shapefile_and_sidecar_styles_preserve_exact_palette(tmp_path):
 data=gpd.GeoDataFrame({'ELU_CLS':list(EXPECTED)+['BU']},geometry=[box(i,0,i+1,1) for i in range(12)],crs=32651)
 styled,report=apply_palette(data)
 path=tmp_path/'HARMONIZED_UNION.shp';styled.to_file(path,index=False,encoding='UTF-8');write_styles(path)
 loaded=gpd.read_file(path)
 assert list(loaded.LU_HEX)==list(EXPECTED.values())+[FALLBACK[2]]
 assert loaded.iloc[-1].LU_STYLE=='Unmapped'
 qml=ET.parse(path.with_suffix('.qml')).getroot()
 renderer=qml.find('renderer-v2');assert renderer.attrib['attr']=='LU_STYLE'
 categories={row.attrib['value']:row.attrib['symbol'] for row in renderer.find('categories')}
 symbols={row.attrib['name']:row for row in renderer.find('symbols')}
 for name,color in EXPECTED.items():
  options={o.attrib['name']:o.attrib['value'] for o in symbols[categories[name]].find('layer/Option')}
  assert options['color']==','.join(map(str,(*rgb(color),255)))
 ns={'sld':'http://www.opengis.net/sld','ogc':'http://www.opengis.net/ogc'}
 sld=ET.parse(path.with_suffix('.sld'))
 for rule in sld.findall('.//sld:Rule',ns):
  name=rule.find('sld:Name',ns).text
  color=rule.find(".//sld:CssParameter[@name='fill']",ns).text
  assert color==dict(EXPECTED,Unmapped=FALLBACK[2])[name]
 with (tmp_path/'LAND_USE_PALETTE.csv').open() as f:
  rows=list(csv.DictReader(f));assert {r['Land Use Classification']:r['HEX'] for r in rows}==EXPECTED
 assert report['unmapped_values']==['BU']
