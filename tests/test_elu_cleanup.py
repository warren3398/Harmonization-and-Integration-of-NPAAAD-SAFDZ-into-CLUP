import io,json,sys,zipfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import geopandas as gpd
import pytest
from shapely.geometry import box,Polygon
from shapely.ops import unary_union
import elu_cleanup
import app as module


def settings(**changes):
    result={'fill_gaps':False,'gap_max_m2':10.0,'overlap_policy':'review','priorities':{}}
    result.update(changes)
    return result


def frame(names,geometries):
    return gpd.GeoDataFrame({'LAND_USE':names},geometry=geometries,crs=32651)


def zipped(tmp_path,data,name='elu'):
    folder=tmp_path/name;folder.mkdir()
    data.to_file(folder/'elu.shp',index=False)
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        for p in folder.iterdir():z.write(p,p.name)
    return raw.getvalue()


def test_repair_and_same_class_overlaps_without_inflated_area():
    bowtie=Polygon([(20,0),(24,4),(20,4),(24,0),(20,0)])
    data=frame(['AGR','AGR','AGR'],[box(0,0,10,10),box(5,0,15,10),bowtie])
    cleaned,report,issues=elu_cleanup.clean(data,'LAND_USE',settings())
    assert cleaned.geometry.is_valid.all()
    assert report['invalid_features_repaired']==1
    assert report['same_class_overlap_removed_m2']==50
    assert not report['requires_review']
    assert abs(cleaned.geometry.area.sum()-158)<1e-8


def test_conflicts_require_review_and_explicit_unique_priorities():
    data=frame(['AGR','BU'],[box(0,0,10,10),box(5,0,15,10)])
    clean,report,issues=elu_cleanup.clean(data,'LAND_USE',settings())
    assert report['requires_review'] and report['overlap_area_m2']==50
    assert report['conflicting_classes']==['AGR','BU']
    assert 'overlaps_review' in issues
    for priorities in ({'AGR':1},{'AGR':1,'BU':1}):
        _,r,_=elu_cleanup.clean(data,'LAND_USE',settings(overlap_policy='priority',priorities=priorities))
        assert r['requires_review']
    fixed,r,_=elu_cleanup.clean(data,'LAND_USE',settings(overlap_policy='priority',priorities={'BU':1,'AGR':2}))
    assert not r['requires_review'] and r['requires_change_confirmation']
    assert r['overlaps_resolved']
    areas=dict(zip(fixed.LAND_USE,fixed.geometry.area))
    assert areas=={'BU':100,'AGR':50}
    assert fixed.geometry.area.sum()==unary_union(data.geometry).area


def test_small_gaps_opt_in_large_and_ambiguous_gaps_preserved():
    outer=box(0,0,20,20)
    small=box(2,2,4,4);large=box(6,6,12,12)
    polygon=Polygon(outer.exterior.coords,[small.exterior.coords,large.exterior.coords])
    data=frame(['AGR'],[polygon])
    untouched,r,_=elu_cleanup.clean(data,'LAND_USE',settings())
    assert r['enclosed_gaps_detected']==2 and r['gaps_filled']==0
    assert untouched.geometry.area.sum()==360
    filled,r,issues=elu_cleanup.clean(data,'LAND_USE',settings(fill_gaps=True))
    assert r['gaps_filled']==1 and r['gaps_preserved']==1
    assert r['gap_area_filled_m2']==4
    assert filled.geometry.area.sum()==364
    assert issues['gaps_review']['filled'].sum()==1
    # A hole touching AGR and BU is ambiguous even below the size threshold.
    hole=box(9,9,11,11)
    ambiguous=frame(['AGR','BU'],[box(0,0,10,20).difference(hole),box(10,0,20,20).difference(hole)])
    result,r,issues=elu_cleanup.clean(ambiguous,'LAND_USE',settings(fill_gaps=True))
    assert r['gaps_filled']==0 and r['gaps_preserved']==1
    assert result.geometry.area.sum()==396
    assert issues['gaps_review'].iloc[0].review_reason=='multiple adjacent classes'


def test_geographic_inputs_measure_tolerance_in_square_metres():
    data=frame(['AGR'],[Polygon(box(500000,1500000,500020,1500020).exterior.coords,[box(500002,1500002,500004,1500004).exterior.coords])]).to_crs(4326)
    result,report,_=elu_cleanup.clean(data,'LAND_USE',settings(fill_gaps=True,gap_max_m2=5))
    assert report['gaps_filled']==1
    assert report['gap_area_filled_m2']==pytest.approx(4,abs=.01)
    assert result.crs.to_epsg()==4326
    with pytest.raises(ValueError):elu_cleanup.options({'elu_gap_max_m2':'nan'})


def test_server_blocks_unreviewed_conflicts_and_publishes_review(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'WORK_ROOT',tmp_path/'jobs');module.WORK_ROOT.mkdir()
    data=frame(['AGR','BU'],[box(500000,1500000,500010,1500010),box(500005,1500000,500015,1500010)])
    raw=zipped(tmp_path,data)
    client=module.app.test_client()
    inspected=client.post('/inspect',data={'reference_mode':'auto','elu_zip':(io.BytesIO(raw),'elu.zip')})
    assert inspected.status_code==200,inspected.json
    report=inspected.json['_elu_cleanup']
    assert report['requires_review']
    assert '_auto_reference' not in inspected.json
    download=client.get(report['review_url']);assert download.status_code==200
    with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
        assert set(archive.namelist())=={'ELU_GEOMETRY_REVIEW.gpkg','ELU_CLEANUP_REPORT.json'}
    attempted=client.post('/process_start',data={'reference_mode':'auto','elu_zip':(io.BytesIO(raw),'elu.zip'),
        'elu_reviewed':'yes','elu_field':'LAND_USE','elu_mapping':json.dumps({'AGR':'AGR','BU':'BU'})})
    assert attempted.status_code==400 and 'Resolve ELU overlaps' in attempted.json['error']


def test_change_confirmation_is_enforced_on_server(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'WORK_ROOT',tmp_path/'jobs');module.WORK_ROOT.mkdir()
    outer=box(500000,1500000,500020,1500020);hole=box(500002,1500002,500004,1500004)
    raw=zipped(tmp_path,frame(['AGR'],[Polygon(outer.exterior.coords,[hole.exterior.coords])]))
    response=module.app.test_client().post('/process_start',data={'reference_mode':'auto','elu_zip':(io.BytesIO(raw),'elu.zip'),
       'elu_reviewed':'yes','elu_field':'LAND_USE','elu_mapping':json.dumps({'AGR':'AGR'}),'elu_fill_gaps':'yes','elu_gap_max_m2':'10'})
    assert response.status_code==400 and 'Confirm the gap filling' in response.json['error']
