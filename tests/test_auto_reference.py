import io
import json
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
import auto_reference
import app as module


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_reference, 'ROOT', tmp_path/'references')
    monkeypatch.setattr(module, 'WORK_ROOT', tmp_path/'jobs')
    module.WORK_ROOT.mkdir()
    shapes = {
        'alpha':box(121,15,121.02,15.02),
        'beta':box(121.02,15,121.04,15.02),
        'gamma':box(124,15,124.02,15.02),
    }
    monkeypatch.setattr(auto_reference, 'regions', lambda:shapes)
    rows = []
    sources = {}
    fetched = []
    for province,geom in shapes.items():
        for role in ('npaaad','safdz'):
            path = tmp_path/(province+'-'+role+'.gpkg')
            frame = gpd.GeoDataFrame({'CLASS':['A' if role=='npaaad' else '2']},geometry=[geom],crs=4326)
            if role=='safdz': frame=frame.to_crs(32651)
            frame.to_file(path,driver='GPKG',index=False)
            key=f'nationwide/luzon/{province}/{role}.gpkg'
            sources[key]=path
            rows.append({'island':'luzon','province':province,'layer':role,'bytes':path.stat().st_size})
    def download(key,dest):
        fetched.append(key)
        shutil.copyfile(sources[key],dest)
        return dest
    monkeypatch.setattr(module,'r2_dataset_rows',lambda:rows)
    monkeypatch.setattr(module,'r2_download',download)
    outer=box(121.01,15.003,121.03,15.017)
    hole=box(121.017,15.008,121.023,15.012)
    geometry=Polygon(outer.exterior.coords,[hole.exterior.coords])
    elu=gpd.GeoDataFrame({'LAND_USE':['Agriculture']},geometry=[geometry],crs=4326).to_crs(32651)
    folder=tmp_path/'elu';folder.mkdir()
    elu.to_file(folder/'elu.shp',index=False)
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as archive:
        for path in folder.iterdir(): archive.write(path,path.name)
    return {'elu':elu,'raw':content.getvalue(),'rows':rows,'download':download,'fetched':fetched,'root':tmp_path}


def upload(raw):
    return (io.BytesIO(raw),'elu.zip')


def test_real_search_index_download():
    masks=auto_reference.regions()
    assert len(masks)>=80
    assert masks['camiguin'].intersects(box(124.6,9.1,124.9,9.3))
    assert 'davaooccidental' in masks
    assert 'cotabatocity' in masks


def test_cross_province_crop_and_holes(setup):
    folder,meta=auto_reference.prepare(setup['elu'],setup['rows'],setup['download'],module.suggest_field,module.normalize_polygon_gdf)
    assert {row['province'] for row in meta['sources']}=={'alpha','beta'}
    assert len(setup['fetched'])==4
    assert meta['coverage_percent']=={'npaaad':100.0,'safdz':100.0}
    for role in ('npaaad','safdz'):
        frame=gpd.read_file(folder/(role+'.gpkg'))
        mask=gpd.GeoSeries([auto_reference.boundary(setup['elu'])],crs=4326).to_crs(frame.crs).iloc[0]
        assert unary_union(frame.geometry).difference(mask).area<0.01
        assert set(frame.columns)=={role.upper(),'geometry'}
        assert unary_union(frame.geometry).symmetric_difference(mask).area<0.1
    loaded,stored=auto_reference.load(meta['token'],setup['elu'])
    assert loaded==folder and stored['boundary_hash']==meta['boundary_hash']


def test_reject_stale_and_missing_crs(setup):
    folder,meta=auto_reference.prepare(setup['elu'],setup['rows'],setup['download'],module.suggest_field,module.normalize_polygon_gdf)
    changed=setup['elu'].copy()
    changed.geometry=changed.geometry.translate(xoff=1000)
    with pytest.raises(ValueError,match='boundary changed'):auto_reference.load(meta['token'],changed)
    with pytest.raises(ValueError):auto_reference.load('../invalid',setup['elu'])
    missing=setup['elu'].set_crs(None,allow_override=True)
    with pytest.raises(ValueError,match='no readable CRS'):auto_reference.boundary(missing)
    expired=dict(meta,created=time.time()-auto_reference.TTL-1)
    (folder/'metadata.json').write_text(json.dumps(expired))
    with pytest.raises(ValueError,match='expired'):auto_reference.load(meta['token'],setup['elu'])


def test_no_coverage_and_missing_pair(setup):
    with pytest.raises(ValueError,match='No available'):
        auto_reference.prepare(setup['elu'],[r for r in setup['rows'] if r['province']=='gamma'],setup['download'],module.suggest_field,module.normalize_polygon_gdf)
    rows=[r for r in setup['rows'] if not (r['province']=='beta' and r['layer']=='safdz')]
    with pytest.raises(ValueError,match='both NPAAAD and SAFDZ'):
        auto_reference.prepare(setup['elu'],rows,setup['download'],module.suggest_field,module.normalize_polygon_gdf)


def test_inspect_reuse_and_real_job(setup):
    client=module.app.test_client()
    response=client.post('/inspect',data={'reference_mode':'auto','elu_zip':upload(setup['raw'])})
    assert response.status_code==200,response.json
    data=response.json
    assert data['_crs_summary']['master_ok']
    assert data['_auto_reference']['cropped_to_elu']
    token=data['_auto_reference']['token']
    fetched=len(setup['fetched'])
    again=client.post('/inspect',data={'reference_mode':'auto','auto_reference_token':token,'elu_zip':upload(setup['raw'])})
    assert again.status_code==200,again.json
    assert again.json['_auto_reference']['token']==token
    assert len(setup['fetched'])==fetched
    response=client.post('/process_start',data={
        'reference_mode':'auto','auto_reference_token':token,'elu_zip':upload(setup['raw']),
        'elu_reviewed':'yes','elu_mapping':json.dumps({'Agriculture':'AGR'}),
        'elu_field':'LAND_USE','npaaad_field':'NPAAAD','safdz_field':'SAFDZ'})
    assert response.status_code==200,response.json
    for _ in range(150):
        progress=client.get('/progress/'+response.json['job_id']).json
        if progress['status'] in ('complete','error'): break
        time.sleep(.1)
    assert progress['status']=='complete',progress
    assert len(setup['fetched'])==fetched, 'Generation must reuse prepared crops.'
    bundle=client.get('/download/'+response.json['job_id'])
    assert bundle.status_code==200
    with zipfile.ZipFile(io.BytesIO(bundle.data)) as archive:
        qa=json.loads(archive.read('QA_QC.json'))
        assert qa['Reference_detection']['cropped_to_elu']
        assert qa['Reference_detection']['coverage_percent']['npaaad']==100
        assert 'HARMONIZED_UNION.zip' in archive.namelist()


def test_browser_auto_upload_and_responsive_layout(setup):
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright
    server=make_server('127.0.0.1',0,module.app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    screenshots=Path('test-artifacts');screenshots.mkdir(exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/harmonizer')
            assert page.locator('#referenceMode').input_value()=='auto'
            assert page.locator('#r2_province').count()==0
            assert page.locator('[name=npaaad_zip]').is_disabled()
            page.locator('[name=elu_zip]').set_input_files({'name':'elu.zip','mimeType':'application/zip','buffer':setup['raw']})
            page.locator('#eluMapRows select').first.wait_for(timeout=15000)
            assert page.locator('#autoReferenceToken').input_value()
            assert 'cropped to your ELU' in page.locator('#autoReferenceHeading').inner_text()
            assert page.locator('#runBtn').is_disabled()
            page.locator('#eluMapRows select').first.select_option('AGR')
            page.locator('#eluReviewed').check()
            assert page.locator('#runBtn').is_enabled()
            for width in (1440,768,390,320):
                page.set_viewport_size({'width':width,'height':1000})
                dimensions=page.evaluate('''() => ({width:innerWidth,page:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('.elu-map-row')].some(e=>e.scrollWidth>e.clientWidth+1)})''')
                assert dimensions['page']<=width,dimensions
                assert not dimensions['overflow']
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(screenshots/f'auto-reference-{width}.png'),full_page=True)
            page.locator('#runBtn').click()
            page.locator('#downloadBtn').wait_for(state='visible',timeout=20000)
            assert page.locator('[role=progressbar]').get_attribute('aria-valuenow')=='100'
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown()
