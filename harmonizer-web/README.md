# NPAAAD/SAFDZ CLUP Harmonizer — Render deployment

The existing v2.8.1 application is a Flask/GeoPandas GIS processing app. The NPAAAD and SAFDZ GeoPackages are stored in Cloudflare R2 bucket `npaaad-safdz-data`. A production deployment must include the complete v2.8.1 `app.py`, `templates/`, and `static/` files, plus server-side R2 dataset selection. Never commit R2 credentials; configure them only as Render environment variables. A web service should use Python with `gunicorn`, and processing requires sufficient memory and durable job storage for large GeoPackages. Do not deploy the preview-only Node map server as the Harmonizer.

Status: deployment preparation only. The original v2.8.1 application files have not yet been committed to this folder, so this is not a runnable service. The existing portal remains available independently.
