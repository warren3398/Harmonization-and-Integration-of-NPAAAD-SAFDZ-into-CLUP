import io,json,time,zipfile
from pathlib import Path
import pytest
import runtime_limits as limits
import app as module


def test_cleanup_keeps_completed_zip_and_removes_abandoned_work(tmp_path,monkeypatch):
 monkeypatch.setattr(limits.tempfile,'gettempdir',lambda:str(tmp_path))
 root=tmp_path/'npaaad_clup_harmonizer';root.mkdir()
 done=root/('a'*32);(done/'outputs').mkdir(parents=True);bundle=done/'outputs'/'HARMONIZATION_RESULTS.zip';bundle.write_bytes(b'archive')
 (done/'uploads').mkdir();(done/'uploads'/'source.zip').write_bytes(b'large original')
 (root/'inspect_stale').mkdir();(root/'inspect_stale'/'large.shp').write_bytes(b'abandoned')
 unfinished=root/('b'*32);unfinished.mkdir();(unfinished/'huge.gpkg').write_bytes(b'partial')
 refs=tmp_path/'harmonizer-auto-references';refs.mkdir();partial=refs/('c'*32);partial.mkdir();(partial/'source.gpkg').write_bytes(b'partial source')
 limits.cleanup()
 assert bundle.read_bytes()==b'archive'
 assert not (done/'uploads').exists() and not (root/'inspect_stale').exists()
 assert not unfinished.exists() and not partial.exists()
 assert json.loads((done/'job_status.json').read_text())['status']=='complete'


def test_zip_capacity_is_checked_before_extraction(tmp_path,monkeypatch):
 monkeypatch.setattr(limits,'used_bytes',lambda:0);monkeypatch.setattr(limits,'SCRATCH_LIMIT',50);monkeypatch.setattr(limits,'RESERVE',0)
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('large.shp',b'x'*1000)
 buffer.seek(0)
 with zipfile.ZipFile(buffer) as archive:
  with pytest.raises(ValueError,match='Temporary GIS storage'):limits.extract_checked(archive,tmp_path)
 assert not (tmp_path/'large.shp').exists()


def test_busy_inspection_is_rejected_before_parsing_files():
 assert limits.GIS_LOCK.acquire(timeout=10)
 try:
  client=module.app.test_client()
  assert client.get('/api/gis-status').json['busy']
  assert client.post('/inspect').status_code==409
  assert client.post('/process_start').status_code==409
 finally:limits.GIS_LOCK.release()


def test_completed_job_survives_worker_state_reset(tmp_path,monkeypatch):
 monkeypatch.setattr(module,'WORK_ROOT',tmp_path)
 job='d'*32;folder=tmp_path/job;folder.mkdir();bundle=folder/'results.zip';bundle.write_bytes(b'valid stored result')
 (folder/'job_status.json').write_text(json.dumps({'status':'complete','percent':100,'bundle':str(bundle)}))
 module.JOBS.pop(job,None)
 client=module.app.test_client()
 assert client.get('/progress/'+job).json['status']=='complete'
 assert client.get('/download/'+job).data==b'valid stored result'


def test_expired_download_is_a_clear_error_not_a_server_crash(tmp_path):
 job='e'*32
 module.JOBS[job]={'status':'complete','percent':100,'bundle':str(tmp_path/'expired.zip')}
 try:
  client=module.app.test_client()
  assert client.get('/progress/'+job).json['status']=='error'
  response=client.get('/download/'+job)
  assert response.status_code==410 and 'expired' in response.json['error']
 finally:module.JOBS.pop(job,None)
