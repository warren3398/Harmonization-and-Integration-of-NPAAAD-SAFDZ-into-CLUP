import geopandas as gpd
import pytest
from shapely.geometry import box
import workspace_ux as ux

def test_names_are_safe_and_preserve_municipality():
 assert ux.download_name('San José')=='San_José_NPAAAD_SAFDZ_Harmonize_Agricultural_Zones.zip'
 assert ux.download_name('A/B\\C:*?').startswith('A_B_C_')
 assert ux.download_name('\n.. ')=='Selected_Area_NPAAAD_SAFDZ_Harmonize_Agricultural_Zones.zip'
 assert len(ux.download_name('x'*500))<200

def test_municipality_requires_almost_complete_coverage(monkeypatch):
 index=gpd.GeoDataFrame({'municipality':['Alpha','Beta']},geometry=[box(120,10,121,11),box(121,10,122,11)],crs=4326)
 monkeypatch.setattr(ux,'municipality_index',lambda:index)
 elu=gpd.GeoDataFrame(geometry=[box(120.1,10.1,120.9,10.9)],crs=4326)
 assert ux.suggest_municipality(elu.to_crs(32651))=='Alpha'
 elu.geometry=[box(120.5,10.1,121.5,10.9)]
 assert ux.suggest_municipality(elu)==''
 monkeypatch.setattr(ux,'municipality_index',lambda:(_ for _ in ()).throw(OSError('offline')))
 assert ux.suggest_municipality(elu)==''

def test_without_land_cover_mapping_uses_existing_broad_rules_only():
 assert ux.default_mapping(['Agriculture','BU','Built-up','Cemetery','Agri-Industrial'])=={'Agriculture':'AGR','BU':'BU','Built-up':'BU','Cemetery':'OTHER','Agri-Industrial':'OTHER'}
