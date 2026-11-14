import pandas as pd
import geopandas as gpd
from shapely.geometry import box
from decision_fallback import fill_missing_decisions
from land_use_styles import apply_palette

def test_nulls_and_blanks_use_exact_elu_description_and_keep_existing_rules():
 values=[None,pd.NA,float('nan'),'','  ','NULL','Ask LGU','BU','do not harmonize']
 classes=['Cemetery','Institutional','Agri-Industrial','Residential','Commercial','Forest','Agricultural','Industrial','Parks and Recreation']
 frame=pd.DataFrame({'ELU_CLS':classes,'Decision':values})
 assert fill_missing_decisions(frame)==6
 assert frame.Decision.tolist()==classes[:6]+['Ask LGU','BU','do not harmonize']

def test_export_keeps_gray_fallback_and_nonempty_decisions(tmp_path):
 frame=gpd.GeoDataFrame({'ELU_CLS':['Cemetery','Institutional','Residential'],'Decision':[None,'','BU']},geometry=[box(i,0,i+1,1) for i in range(3)],crs=32651)
 fill_missing_decisions(frame)
 styled,_=apply_palette(frame)
 assert styled.LU_HEX.tolist()==['#D9D9D9','#D9D9D9','#FFFF00']
 path=tmp_path/'result.shp';styled.to_file(path,index=False)
 result=gpd.read_file(path)
 assert result.Decision.tolist()==['Cemetery','Institutional','BU']
