"""Read the published Nationwide totals workbook for the dashboard."""
from functools import lru_cache
from math import isfinite
from pathlib import Path

from openpyxl import load_workbook

WORKBOOK_PATH = Path(__file__).with_name('data') / 'nationwide-totals.xlsx'
MODES = ('npaaad', 'safdz', 'combined')


@lru_cache(maxsize=2)
def read_totals(path, modified):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook['Nationwide totals'].values
        if next(rows) != ('Crop', 'NPAAAD (ha)', 'SAFDZ (ha)', 'Both (ha)'):
            raise ValueError('Invalid Nationwide totals headers.')
        totals = {}
        for crop, *values in rows:
            if not isinstance(crop, str) or crop in totals:
                raise ValueError('Invalid or duplicate crop.')
            if len(values) != len(MODES) or any(
                type(value) not in (int, float) or not isfinite(value) or value < 0 for value in values
            ):
                raise ValueError('Invalid area total.')
            totals[crop] = dict(zip(MODES, values))

        coverage_rows = workbook['Coverage'].values
        if next(coverage_rows) != ('Area ID', 'Area', 'NPAAAD', 'SAFDZ', 'Both'):
            raise ValueError('Invalid Nationwide coverage headers.')
        included = dict.fromkeys(MODES, 0)
        excluded = {mode: [] for mode in MODES}
        for _, area, *flags in coverage_rows:
            for mode, flag in zip(MODES, flags):
                if flag is True:
                    included[mode] += 1
                elif flag is False:
                    excluded[mode].append(area)
                else:
                    raise ValueError('Invalid coverage flag.')
        if not totals or not all(included.values()):
            raise ValueError('The Nationwide workbook is incomplete.')
        facts = dict(list(workbook['Source notes'].values)[1:])
        return {'totals':totals, 'coverage':{
            mode: {'included': included[mode], 'excluded': excluded[mode]} for mode in MODES
        }, 'source': facts['Land use'], 'snapshot': facts['Snapshot'],
        'sourceExclusions': facts.get('Source exclusions', '')}
    finally:
        workbook.close()
