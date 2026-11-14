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
 for value in ['BU','Institutional','Cemetery','Infrastructure/Utilities, Transportation and Services','Residential / Commercial']:
  assert category(value)==value
 assert category('')==FALLBACK[0]

def test_shapefile_and_sidecar_styles_preserve_exact_palette(tmp_path):
 data=gpd.GeoDataFrame({'ELU_CLS':list(EXPECTED)+['BU']},geometry=[box(i,0,i+1,1) for i in range(12)],crs=32651)
 styled,report=apply_palette(data)
 path=tmp_path/'HARMONIZED_UNION.shp';styled.to_file(path,index=False,encoding='UTF-8');write_styles(path)
 loaded=gpd.read_file(path)
 assert list(loaded.LU_HEX)==list(EXPECTED.values())+[report['palette']['BU']]
 assert loaded.iloc[-1].LU_STYLE=='BU'
 assert report['palette']['BU'] not in set(EXPECTED.values())|{FALLBACK[2]}
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
  assert color==report['palette'][name]
 with (tmp_path/'LAND_USE_PALETTE.csv').open() as f:
  rows=list(csv.DictReader(f));assert {r['Land Use Classification']:r['HEX'] for r in rows}==report['palette']
 assert report['unmapped_values']==[]
 assert set(report['additional_classes'])=={'BU'}


def test_additional_classes_keep_exact_names_distinct_colors_and_order_stability():
 names=['Cemetery','Institutional','Infrastructure/Utilities Transportation and Services','Unlisted zone α','Residential / Commercial','BU']
 original=gpd.GeoDataFrame({'ELU_CLS':names+names,'Decision':['Ask LGU']*(2*len(names))},geometry=[box(i,0,i+1,1) for i in range(2*len(names))],crs=32651)
 styled,report=apply_palette(original)
 assert styled.LU_STYLE.tolist()==original.ELU_CLS.tolist()
 assert styled.Decision.equals(original.Decision) and styled.geometry.equals(original.geometry)
 colors=report['additional_classes']
 assert len(set(colors.values()))==len(names)
 assert not set(colors.values()) & (set(EXPECTED.values())|{FALLBACK[2]})
 assert styled.groupby('ELU_CLS').LU_HEX.nunique().eq(1).all()
 _,reverse=apply_palette(original.iloc[::-1])
 assert colors==reverse['additional_classes']
 assert report['additional_features']==len(original) and report['unmapped_features']==0


def test_extra_classes_are_in_every_exported_style_and_palette(tmp_path):
 names=['Cemetery','Institutional','Infrastructure/Utilities Transportation and Services','New & Special <Zone>']
 frame=gpd.GeoDataFrame({'ELU_CLS':names},geometry=[box(i,0,i+1,1) for i in range(len(names))],crs=32651)
 styled,report=apply_palette(frame);path=tmp_path/'result.shp';styled.to_file(path,index=False)
 write_styles(path,report['palette'])
 qml=ET.parse(path.with_suffix('.qml')).getroot()
 categories={row.attrib['value']:row.attrib['symbol'] for row in qml.findall('.//category')}
 symbols={row.attrib['name']:row for row in qml.findall('.//symbols/symbol')}
 ns={'sld':'http://www.opengis.net/sld','ogc':'http://www.opengis.net/ogc'}
 rules={rule.find('sld:Name',ns).text:rule for rule in ET.parse(path.with_suffix('.sld')).findall('.//sld:Rule',ns)}
 with (tmp_path/'LAND_USE_PALETTE.csv').open() as f:csv_colors={r['Land Use Classification']:r['HEX'] for r in csv.DictReader(f)}
 for name in names:
  color=report['palette'][name]
  options={o.attrib['name']:o.attrib['value'] for o in symbols[categories[name]].find('layer/Option')}
  assert options['color']==','.join(map(str,(*rgb(color),255)))
  assert rules[name].find(".//sld:CssParameter[@name='fill']",ns).text==color
  assert csv_colors[name]==color
 loaded=gpd.read_file(path)
 assert loaded.LU_STYLE.tolist()==names


def test_only_empty_classifications_use_neutral_gray():
 frame=gpd.GeoDataFrame({'ELU_CLS':[None,'','Cemetery']},geometry=[box(i,0,i+1,1) for i in range(3)],crs=32651)
 styled,report=apply_palette(frame)
 assert styled.LU_HEX.tolist()[:2]==[FALLBACK[2]]*2
 assert styled.LU_STYLE.tolist()[2]=='Cemetery'
 assert report['missing_class_features']==2 and report['additional_features']==1
