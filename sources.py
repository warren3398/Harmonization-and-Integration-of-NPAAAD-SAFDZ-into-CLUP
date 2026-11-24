"""Read-only access to the shared GIS catalogs."""
import hashlib
import json
import time
import os
import re
import shutil
import tempfile
import unicodedata
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from zipfile import ZipFile

import boto3
import pyogrio
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

ADMIN = 'slopemap-gis-data'
ZONES = 'npaaad-safdz-data'
LAND = 'land-use-database'
PREFIX = 'landuse/2020-2022/'
CACHE = Path(os.getenv('PCLAM_CACHE', '/tmp/pclam-cache'))
ROMAN = dict(zip('I II III IV V VI VII VIII IX X XI XII XIII'.split(), range(1, 14)))


class DataError(Exception):
    pass


def canonical(value):
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return re.sub('[^a-z0-9]', '', value.lower())


def region_key(name):
    name = str(name).upper()
    for key, aliases in {
        'CAR': ('CORDILLERA', 'CAR'), 'NCR': ('NATIONAL CAPITAL', 'NCR'),
        'BARMM': ('BANGSAMORO', 'BARMM', 'ARMM'), 'NIR': ('NEGROS ISLAND', 'NIR'),
        'MIMAROPA': ('MIMAROPA', 'IV-B', 'IVB'), '4A': ('CALABARZON', 'IV-A', 'IVA'),
        '13': ('CARAGA',),
    }.items():
        if any(re.search(r'(?<![A-Z])' + x + r'(?![A-Z])', name) for x in aliases):
            return key
    match = re.search(r'REGION[\s_-]*([IVX]+|\d+)(?:\b|_)', name)
    if match:
        return str(ROMAN.get(match[1], match[1]))
    return canonical(name)


class Sources:
    @cached_property
    def client(self):
        keys = ('R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY')
        if not all(os.getenv(key) for key in keys):
            raise DataError('The read-only GIS connection is not configured.')
        account, access, secret = [os.environ[key] for key in keys]
        return boto3.client(
            's3', endpoint_url=f'https://{account}.r2.cloudflarestorage.com',
            region_name='auto', aws_access_key_id=access, aws_secret_access_key=secret,
            config=Config(signature_version='s3v4', connect_timeout=8, read_timeout=60),
        )

    def items(self, bucket, prefix):
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
            yield from (item for item in page.get('Contents', []) if item['Size'])

    def file(self, bucket, key, meta=None):
        meta = meta or self.client.head_object(Bucket=bucket, Key=key)
        signature = f'{bucket}/{key}:{meta["ETag"]}'
        CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha256(signature.encode()).hexdigest() + Path(key).suffix)
        if path.exists():
            path.touch()
            return path
        size = meta.get('Size', meta.get('ContentLength', 0))
        limit = 2 * 1024**3
        if size > limit:
            raise DataError(f'Source object exceeds the local processing limit: {Path(key).name}')
        files = sorted((p for p in CACHE.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        used = sum(p.stat().st_size for p in files)
        for old in files:
            if used + size <= limit:
                break
            # Keep recently used inputs available for concurrent metadata requests.
            if old.stat().st_mtime < time.time() - 600:
                used -= old.stat().st_size
                old.unlink(missing_ok=True)
        if shutil.disk_usage(CACHE).free < size + 128 * 1024**2:
            raise DataError('Temporary GIS storage is full. Retry after another assessment finishes.')
        fd, name = tempfile.mkstemp(dir=CACHE)
        os.close(fd)
        try:
            self.client.download_file(
                bucket, key, name, Config=TransferConfig(max_concurrency=1, use_threads=False)
            )
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        return path

    @cached_property
    def catalog(self):
        provinces = pyogrio.read_dataframe(
            self.file(ADMIN, 'boundaries/provinces.gpkg'), read_geometry=False,
        )
        land = defaultdict(list)
        objects = {item['Key']: item for item in self.items(LAND, PREFIX)}
        manifest_key = PREFIX + 'manifest.json'
        if manifest_key in objects:
            with self.client.get_object(Bucket=LAND, Key=manifest_key)['Body'] as body:
                parts_manifest = json.load(body)['parts']
            listed = {PREFIX + part['file']: part for part in parts_manifest}
        else:
            listed = {key: {} for key in objects}
        missing_land = set()
        for key, entry in listed.items():
            parts = key[len(PREFIX):].split('/')
            if len(parts) < 3 or not key.lower().endswith(('.zip', '.gpkg')):
                continue
            item = objects.get(key)
            if not item or item['Size'] != entry.get('bytes', item['Size']):
                missing_land.add(region_key(parts[1]))
                continue
            parts = item['Key'][len(PREFIX):].split('/')
            if len(parts) >= 3 and item['Key'].lower().endswith(('.zip', '.gpkg')):
                land[region_key(parts[1])].append(item)
        zones = defaultdict(dict)
        for item in self.items(ZONES, 'nationwide/'):
            parts = item['Key'].split('/')
            if len(parts) == 4 and parts[3] in ('npaaad.gpkg', 'safdz.gpkg'):
                slot = zones[canonical(parts[2])]; role = parts[3][:-5]
                if role not in slot or ('-' in parts[2] and '-' not in slot[role]['Key'].split('/')[2]):
                    slot[role] = item
        rows = {}
        for row in provinces.itertuples():
            region = str(row.ADM1_EN)
            # Administrative and storage labels can differ by a historical province name.
            slug = canonical(row.ADM2_EN)
            slug = {'cotabatonorthcotabato': 'northcotabato', 'samarwesternsamar': 'westernsamar',
                    'dinagatislands': 'dinagatisland', 'maguindanaodelnorte': 'maguindanao',
                    'maguindanaodelsur': 'maguindanao',
                    'davaodeorocompostelavalley': 'davaodeoro' if 'davaodeoro' in zones else 'compostelavalley'}.get(slug, slug)
            land_region = region_key(region)
            if slug in {'negrosoccidental', 'negrosoriental', 'siquijor'}:
                region = 'Negros Island Region (NIR)'
                # The 2020–2022 archives predate the current regional grouping.
                # The Region VII archive contains the verified Siquijor features.
                if slug != 'siquijor' and land.get('NIR'):
                    land_region = 'NIR'
            keys = [] if land_region in missing_land else land.get(land_region, [])
            matches = zones.get(slug, {})
            rows[str(row.ADM2_PCODE)] = {
                'id': str(row.ADM2_PCODE), 'name': str(row.ADM2_EN), 'region': region,
                'land': keys, 'zones': matches,
            }
        return rows

    def province(self, code):
        if code not in self.catalog:
            raise DataError('The selected province is unavailable.')
        literal = code.replace("'", "''")
        return pyogrio.read_dataframe(
            self.file(ADMIN, 'boundaries/provinces.gpkg'), where=f"ADM2_PCODE = '{literal}'",
        )

    def land_files(self, meta, folder):
        path = self.file(LAND, meta['Key'], meta)
        if path.suffix.lower() == '.gpkg':
            yield path
            return
        with ZipFile(path) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith('.gpkg')]
            if not members:
                raise DataError(f'No GeoPackage in {Path(meta["Key"]).name}.')
            for member in members:
                if member.file_size > 2 * 1024**3 or member.file_size + 128 * 1024**2 > shutil.disk_usage(folder).free:
                    raise DataError('Insufficient temporary storage for the selected regional archive.')
                dst = folder / 'land-piece.gpkg'
                try:
                    with archive.open(member) as src, dst.open('wb') as target:
                        shutil.copyfileobj(src, target, length=1024**2)
                    yield dst
                finally:
                    dst.unlink(missing_ok=True)
