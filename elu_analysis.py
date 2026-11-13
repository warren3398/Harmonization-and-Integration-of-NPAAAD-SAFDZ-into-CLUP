"""Reuse an ELU analysis only for identical ZIP bytes, class field and settings."""
import hashlib
import json
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import pyogrio
import elu_cleanup
import runtime_limits

ROOT=Path(tempfile.gettempdir())/'harmonizer-elu-analysis'
TTL=3*3600


def prepare_file(shapefile,zip_path,filename,selected,settings,suggest):
    info=pyogrio.read_info(shapefile)
    fields=list(info['fields'])
    field=selected if selected in fields else suggest(fields,'elu')
    digest=hashlib.sha256()
    with Path(zip_path).open('rb') as stream:
        while True:
            block=stream.read(1024*1024)
            if not block:break
            digest.update(block)
    digest.update(json.dumps({'field':field,'settings':settings},sort_keys=True).encode())
    key=digest.hexdigest()[:32]
    ROOT.mkdir(exist_ok=True)
    folder=ROOT/key
    try:
        data=json.loads((folder/'metadata.json').read_text())
        if time.time()-data['created']<TTL:
            elu_cleanup.review_path(data['report']['review_url'].rsplit('/',1)[-1])
            cleaned=gpd.read_file(folder/'analysis.gpkg',layer='elu_cleaned')
            meta=dict(data['meta'],filename=filename)
            return cleaned,meta,data['report'],folder/'analysis.gpkg'
    except (OSError,ValueError,KeyError,RuntimeError):pass
    runtime_limits.ensure_space()
    # Build in a fresh directory and publish metadata last. A killed worker never
    # leaves a valid-looking partial analysis for the next request to reuse.
    import shutil
    if folder.exists():shutil.rmtree(folder)
    folder.mkdir()
    try:
        raw=gpd.read_file(shapefile,columns=[field] if field else [])
        cleaned,report,issues=elu_cleanup.clean(raw,field,settings)
        values=raw[field].dropna().astype(str).str.strip()
        values=values[values!=''].drop_duplicates().sort_values().head(1000).tolist()
        meta={'filename':filename,'crs':str(raw.crs),'epsg':raw.crs.to_epsg() if raw.crs else None,
              'authority':(':'.join(map(str,raw.crs.to_authority())) if raw.crs and raw.crs.to_authority() else None),
              'geometry_types':sorted(str(v) for v in raw.geom_type.dropna().unique()),
              'feature_count':int(info['features']),'fields':fields,'suggested_field':field,
              'sample_values':values[:12],'field_values':{field:values}}
        path=folder/'analysis.gpkg'
        elu_cleanup.write_review(path,cleaned,issues)
        token=elu_cleanup.save_review(cleaned,report,issues,existing_path=path)
        report=dict(report,review_url='/elu-review/'+token)
        (folder/'metadata.json').write_text(json.dumps({'created':time.time(),'meta':meta,'report':report}))
        return cleaned,meta,report,path
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise
