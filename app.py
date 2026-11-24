import logging
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import os

from assessment import CROPS, EXPORTS, assessments, regional_assessment
from sources import DataError, Sources

app = FastAPI(title='Provincial Crop-Based Land Use Assessment Map')
if os.getenv('PCLAM_WEB_ORIGIN'):
    app.add_middleware(CORSMiddleware, allow_origins=[os.environ['PCLAM_WEB_ORIGIN']],
                       allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])
sources = Sources()
catalog_lock = Lock()
job_lock = Lock()
jobs = {}
executor = ThreadPoolExecutor(max_workers=1)
logger = logging.getLogger('pclam')


class Request(BaseModel):
    region: str | None = Field(default=None, max_length=100)
    province: str | None = Field(default=None, max_length=32)
    municipality: str | None = Field(default=None, max_length=32)
    barangay: str | None = Field(default=None, max_length=32)
    output: Literal['jpeg', 'shp', 'table'] | None = None
    focused: bool = False
    mode: Literal['npaaad', 'safdz', 'combined']
    crops: list[str] = Field(default_factory=list, max_length=len(CROPS))


class Cancelled(Exception):
    pass


@app.get('/health')
def health():
    return {'status': 'ok', 'app': 'PCLAM'}


@app.get('/api/catalog')
def catalog():
    try:
        with catalog_lock:
            rows = sources.catalog
    except DataError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception('Catalog read failed')
        raise HTTPException(503, 'The shared GIS catalog is temporarily unavailable.') from exc
    return {'provinces': [
        dict(id=row['id'], name=row['name'], region=row['region'],
             land_available=bool(row['land']), coverage=sorted(row['zones']))
        for row in sorted(rows.values(), key=lambda r: (r['region'], r['name']))
    ], 'crops': CROPS}



def selected_area(province, municipality=None, barangay=None):
    if province not in sources.catalog:
        raise HTTPException(404, 'Province not found.')
    row = dict(sources.catalog[province], level='Province')
    if barangay and not municipality:
        raise HTTPException(422, 'Select the parent municipality.')
    if municipality:
        match = next((m for m in sources.municipal_options(province) if m['id'] == municipality), None)
        if not match:
            raise HTTPException(404, 'Municipality not found in this province.')
        row.update(municipality=municipality, municipality_name=match['name'],
                   area=f"{match['name']}, {row['name']}", level='Municipality')
    if barangay:
        match = next((b for b in sources.barangay_options(municipality) if b['id'] == barangay), None)
        if not match:
            raise HTTPException(404, 'Barangay not found in this municipality.')
        row.update(barangay=barangay, barangay_name=match['name'],
                   area=f"{match['name']}, {row['area']}", level='Barangay')
    return row


@app.get('/api/areas/{province}/municipalities')
def municipalities(province: str):
    selected_area(province)
    return sources.municipal_options(province)


@app.get('/api/areas/{province}/municipalities/{municipality}/barangays')
def barangays(province: str, municipality: str):
    selected_area(province, municipality)
    return sources.barangay_options(municipality)


@app.get('/api/boundary')
def boundary(region: str, province: str | None = None, municipality: str | None = None,
             barangay: str | None = None):
    import geopandas as gpd
    from assessment import CRS_AREA, geom_polygon
    from fastapi.responses import Response

    catalog()
    if province:
        row = selected_area(province, municipality, barangay)
        if row['region'] != region:
            raise HTTPException(422, 'The selected province does not belong to this region.')
        frame = sources.boundary(row)
    else:
        rows = [row for row in sources.catalog.values() if row['region'] == region]
        if not rows or municipality or barangay:
            raise HTTPException(404, 'Area not found.')
        frame = gpd.GeoDataFrame(geometry=[
            sources.province(row['id']).to_crs(CRS_AREA).geometry.map(geom_polygon).union_all().simplify(80, preserve_topology=True)
            for row in rows
        ], crs=CRS_AREA)
    if frame.empty:
        raise HTTPException(404, 'Boundary unavailable.')
    tolerance = 3 if barangay else 12 if municipality else 45 if province else 80
    shape = frame.to_crs(CRS_AREA).geometry.map(geom_polygon).union_all().simplify(tolerance, preserve_topology=True)
    view = gpd.GeoDataFrame(geometry=[shape], crs=CRS_AREA).to_crs('EPSG:4326')
    return Response(view.to_json(drop_id=True), media_type='application/geo+json',
                    headers={'Cache-Control': 'public, max-age=300'})


def run(job, request, row):
    def check():
        if job['cancel'].is_set():
            raise Cancelled()

    def progress(value, stage):
        check()
        job['state'].update(progress=value, stage=stage)

    try:
        progress(1, 'Starting assessment')
        job['state']['status'] = 'running'
        args = (request.mode, set(request.crops), job['folder'], progress, check, request.output)
        if row.get('level') in ('Region', 'Nationwide'):
            result = regional_assessment(sources, row['rows'], row['name'], *args, focused=request.focused)
        else:
            result = assessments(sources, row, *args, focused=request.focused)
        check()
        check_status = {'progress': 100, 'stage': 'Assessment ready', 'status': 'complete', 'result': result}
        job['state'].update(check_status)
    except Cancelled:
        shutil.rmtree(job['folder'], ignore_errors=True)
        job['state'].update(status='cancelled', stage='Cancelled')
    except Exception as exc:
        logger.exception('Assessment failed for %s', row['id'])
        shutil.rmtree(job['folder'], ignore_errors=True)
        job['state'].update(status='failed', stage=str(exc) if isinstance(exc, DataError) else 'GIS processing failed. Check the source dataset or retry.')
    finally:
        job['finished'] = time.monotonic()


@app.post('/api/jobs', status_code=202)
def start(request: Request):
    if not set(request.crops).issubset(CROPS):
        raise HTTPException(422, 'Unknown crop classification.')
    catalog()
    required = {'npaaad', 'safdz'} if request.mode == 'combined' else {request.mode}
    if request.province:
        row = selected_area(request.province, request.municipality, request.barangay)
        if request.region and request.region != row['region']:
            raise HTTPException(422, 'The selected province does not belong to this region.')
        if not row['land'] or not required.issubset(row['zones']):
            raise HTTPException(422, 'Required GIS layers are missing for this selection.')
    else:
        if not request.region or request.municipality or request.barangay:
            raise HTTPException(422, 'Select a region and its parent areas as needed.')
        nationwide = request.region == 'Nationwide'
        if nationwide and (request.output != 'table' or not request.focused):
            raise HTTPException(422, 'Nationwide assessment is available as a table.')
        rows = [item for item in sources.catalog.values() if nationwide or item['region'] == request.region]
        if not any(item['land'] and required.issubset(item['zones']) for item in rows):
            raise HTTPException(422, 'Required GIS layers are missing for this region.')
        row = {'id': request.region, 'name': request.region, 'level': 'Nationwide' if nationwide else 'Region', 'rows': rows}
    with job_lock:
        now = time.monotonic()
        for key, old in list(jobs.items()):
            if old.get('finished') and now - old['finished'] > 3600:
                shutil.rmtree(old['folder'], ignore_errors=True)
                del jobs[key]
        if sum(not item.get('finished') for item in jobs.values()) >= 3:
            raise HTTPException(429, 'The assessment queue is full. Try again after a job completes.')
        if len(jobs) >= 12:
            raise HTTPException(429, 'Temporary results storage is full. Try again later.')
        job_id = uuid.uuid4().hex
        job = {'folder': Path(tempfile.mkdtemp(prefix='pclam-result-')), 'cancel': Event(),
               'created': now, 'state': {'status': 'queued', 'progress': 0, 'stage': 'Queued'}}
        jobs[job_id] = job
        executor.submit(run, job, request, row)
    return {'id': job_id}


def get_job(job_id):
    with job_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, 'This assessment has expired or was restarted.')
    return job


@app.get('/api/jobs/{job_id}')
def status(job_id: str):
    job = get_job(job_id)
    return {**job['state'], 'elapsed': int((job.get('finished') or time.monotonic()) - job['created'])}


@app.delete('/api/jobs/{job_id}', status_code=202)
def cancel(job_id: str):
    get_job(job_id)['cancel'].set()
    return {'requested': True}


@app.get('/api/jobs/{job_id}/files/{format}')
def file(job_id: str, format: str):
    job = get_job(job_id)
    if job['state']['status'] != 'complete':
        raise HTTPException(404, 'Assessment files are unavailable.')
    if format in {'boundary.geojson', 'map.geojson'}:
        return FileResponse(job['folder'] / format, media_type='application/geo+json')
    path = job['folder'] / ('assessment.' + format)
    if format not in EXPORTS or not path.is_file():
        raise HTTPException(404, 'This file is unavailable for the assessment.')
    result = job['state']['result']
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', result['area'].lower()).strip('-')
    return FileResponse(path, filename=f"pclam-{slug}-{result['mode']}-{job_id[:8]}.{format}")


app.mount('/', StaticFiles(directory=Path(__file__).with_name('web'), html=True), name='web')
