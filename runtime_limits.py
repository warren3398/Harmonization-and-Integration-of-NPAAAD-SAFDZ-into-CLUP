"""Bounded scratch storage and one heavy GIS operation per small web instance."""
import gc
import json
import re
import shutil
import tempfile
import threading
import time
from functools import wraps
from pathlib import Path
from flask import jsonify

GIS_LOCK=threading.Lock()
ROOTS=('npaaad_clup_harmonizer','harmonizer-auto-references','harmonizer-elu-reviews','harmonizer-elu-analysis')
SCRATCH_LIMIT=1400*1024*1024
RESERVE=100*1024*1024
TTL=3*3600


def release_memory():
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL(None).malloc_trim(0)
    except (AttributeError,OSError):
        pass


def used_bytes():
    total=0
    for name in ROOTS:
        root=Path(tempfile.gettempdir())/name
        for path in root.rglob('*'):
            try:
                if path.is_file():total+=path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def cleanup():
    now=time.time()
    for name in ROOTS:
        root=Path(tempfile.gettempdir())/name
        if not root.exists():continue
        for folder in root.iterdir():
            if not folder.is_dir():continue
            if name=='npaaad_clup_harmonizer' and folder.name.startswith('inspect_'):
                # Called only under GIS_LOCK: no active inspection can own these.
                shutil.rmtree(folder,ignore_errors=True);continue
            if folder.name=='eo79_r2':continue
            if not (re.fullmatch('[a-f0-9]{32}',folder.name)):continue
            # An interrupted worker leaves no usable completion metadata.
            if name in ('harmonizer-auto-references','harmonizer-elu-analysis') and not (folder/'metadata.json').exists():
                shutil.rmtree(folder,ignore_errors=True);continue
            status=folder/'job_status.json'
            if name=='npaaad_clup_harmonizer':
                bundle=folder/'outputs'/'HARMONIZATION_RESULTS.zip'
                if bundle.is_file():
                    trim_job(folder,bundle)
                    if not status.exists():status.write_text(json.dumps({'status':'complete','percent':100,'step':'Results ready','elapsed':0,'bundle':str(bundle)}))
                elif not status.exists():
                    shutil.rmtree(folder,ignore_errors=True);continue
            if name=='npaaad_clup_harmonizer' and status.exists():
                try:
                    state=json.loads(status.read_text())
                    if state.get('status') not in ('complete','error'):
                        shutil.rmtree(folder,ignore_errors=True);continue
                except (OSError,ValueError):pass
            try:
                if now-folder.stat().st_mtime>TTL:shutil.rmtree(folder,ignore_errors=True)
            except FileNotFoundError:pass


def ensure_space(additional=0):
    if additional<0 or used_bytes()+additional+RESERVE>SCRATCH_LIMIT:
        raise ValueError('Temporary GIS storage is busy. Older results expire after 3 hours. Download existing results and retry later, or use a smaller ELU.')
    if shutil.disk_usage(tempfile.gettempdir()).free<additional+RESERVE:
        raise ValueError('Insufficient temporary space for this GIS file. Please retry later.')


def trim_job(folder,bundle=None):
    folder=Path(folder)
    keep={Path(bundle).resolve()} if bundle else set()
    for path in sorted(folder.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        if path.is_file() and path.name!='job_status.json' and path.resolve() not in keep:
            path.unlink(missing_ok=True)
        elif path.is_dir():
            try:path.rmdir()
            except OSError:pass


def inspect_guard(fn):
    @wraps(fn)
    def guarded(*args,**kwargs):
        if not GIS_LOCK.acquire(blocking=False):
            return jsonify({'error':'Another ELU reference detection or GIS generation is running. Waiting for it to finish.','busy':True}),409
        try:
            cleanup();ensure_space()
            return fn(*args,**kwargs)
        except ValueError as error:
            return jsonify({'error':str(error)}),400
        finally:
            release_memory();GIS_LOCK.release()
    return guarded


def extract_checked(archive,folder):
    folder=Path(folder).resolve()
    files=archive.infolist()
    expanded=sum(info.file_size for info in files)
    ensure_space(expanded)
    for info in files:
        destination=(folder/info.filename.replace('\\','/')).resolve()
        if not destination.is_relative_to(folder):raise ValueError('Unsafe file path in uploaded ZIP.')
    archive.extractall(folder)
