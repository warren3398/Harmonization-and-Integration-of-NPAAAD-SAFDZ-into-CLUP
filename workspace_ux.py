"""Presentation metadata; never changes clipping or harmonization rules."""
import io,json,re,unicodedata,urllib.request,zipfile
from functools import lru_cache
import geopandas as gpd
from shapely.geometry import shape

@lru_cache(maxsize=1)
def municipality_index():
    # GADM names are suggestions for filenames only; never clipping boundaries.
    request=urllib.request.Request('https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_PHL_2.json.zip',headers={'User-Agent':'CLUP-Harmonizer/2.8'})
    with urllib.request.urlopen(request,timeout=12) as response:raw=response.read(3*1024*1024+1)
    if len(raw)>3*1024*1024:raise ValueError('Municipality naming index is too large.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entry=next(i for i in archive.infolist() if i.filename.endswith('.json'))
        if entry.file_size>30*1024*1024:raise ValueError('Municipality naming index is too large.')
        features=json.loads(archive.read(entry))['features']
    return gpd.GeoDataFrame({'municipality':[f['properties']['NAME_2'] for f in features]},geometry=[shape(f['geometry']).simplify(.0001,preserve_topology=True) for f in features],crs=4326)

def suggest_municipality(elu):
    try:
        frame=municipality_index();boundary=elu.to_crs(4326).geometry.union_all()
        candidates=frame.iloc[frame.sindex.query(boundary,predicate='intersects')]
        if boundary.is_empty or not boundary.area:return ''
        ranked=sorted(((boundary.intersection(row.geometry).area/boundary.area,row.municipality) for row in candidates.itertuples()),reverse=True)
        # A filename must not mislabel an ELU spanning several municipalities.
        return str(ranked[0][1]) if ranked and ranked[0][0]>=.98 else ''
    except Exception:
        return '' # naming must never block generation when the index is offline

def download_name(municipality):
    value=unicodedata.normalize('NFC',str(municipality or '')).strip()
    value=re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+','_',value)
    value=re.sub(r'\s+','_',value).strip('._ ')[:100] or 'Selected_Area'
    return value+'_NPAAAD_SAFDZ_Harmonize_Agricultural_Zones.zip'

def default_mapping(values):
    result={}
    for value in values:
        if value is None:continue
        text=str(value).strip()
        if not text:continue
        normalized=text.upper().replace('_',' ').replace('-',' ').strip()
        result[text]='BU' if normalized in {'BU','BUILT UP','BUILTUP'} or 'BUILT UP' in normalized else 'AGR' if normalized in {'AGR','AGRICULTURE','AGRICULTURAL'} or normalized.startswith('AGR ') else 'OTHER'
    return result
