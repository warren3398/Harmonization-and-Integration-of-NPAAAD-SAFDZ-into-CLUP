const $ = id => document.getElementById(id);
const api = window.PCLAM_API || '';
let provinces = [], current = null, map, overlays = [];
const modes = {combined: ['npaaad', 'safdz'], npaaad: ['npaaad'], safdz: ['safdz']};
const labels = {csv:'CSV table', 'npaaad.csv':'NPAAAD CSV', 'safdz.csv':'SAFDZ CSV',xlsx:'Excel matrix',pdf:'PDF assessment report',gpkg:'GeoPackage',
  'shp.zip':'Shapefile ZIP',geojson:'GeoJSON','qa.json':'QA/QC JSON','map.png':'Map PNG','map.pdf':'Map PDF','map.jpg':'Download JPEG map'};
const number = new Intl.NumberFormat('en-PH', {maximumFractionDigits: 2});
function node(tag, text, parent) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (parent) parent.append(element);
  return element;
}
function option(select, value, title) { const o = node('option', title, select); o.value = value; }
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fetchData(path, options, retries = 0) {
  let response;
  for (let attempt = 0; ; attempt++) {
    try {
      response = await fetch(api + path, options);
      if (![502, 503, 504].includes(response.status) || attempt === retries) break;
    } catch (error) {
      if (attempt === retries) throw error;
    }
    await wait(1500 * 2 ** attempt);
  }
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === 'string' ? detail : 'The GIS service is temporarily unavailable.');
  }
  return response.json();
}
function updateAvailability() {
  const candidates = provinces.filter(p => ($('region').value === 'Nationwide' || p.region === $('region').value) &&
    (!$('province').value || p.id === $('province').value));
  const eligible = candidates.filter(p => p.land_available && modes[$('mode').value].every(key => p.coverage.includes(key)));
  $('availability').textContent = candidates.length && !eligible.length
    ? 'Required source layers are unavailable for this selection.'
    : eligible.length < candidates.length ? 'Partial coverage. ' + eligible.length + ' of ' + candidates.length + ' areas have the required layers.' : '';
  $('run').disabled = eligible.length === 0 || Boolean(current);
}

function resetArea(id, text) {
  $(id).replaceChildren();
  option($(id), '', text);
  $(id).disabled = true;
}

function selection() {
  return Object.fromEntries(['region','province','municipality','barangay'].map(key => [key, $(key).value || null]));
}

let areaRequest = 0;
let previewRequest = 0;
let previewLayer;
async function previewBoundary() {
  const serial = ++previewRequest;
  const area = selection();
  if (previewLayer) previewLayer.remove();
  if (!map || !area.region) return;
  if (area.region === 'Nationwide') {
    overlays.forEach(layer => layer.remove());
    overlays = [];
    $('legend').replaceChildren();
    return;
  }
  map.invalidateSize({pan: false});
  $('map-title').textContent = ['barangay','municipality','province','region'].map(key =>
    area[key] ? $(key).selectedOptions[0]?.textContent : null).filter(Boolean).join(' · ');
  try {
    const query = new URLSearchParams(Object.entries(area).filter(([, id]) => id));
    const shape = await fetchData('/api/boundary?' + query, undefined, 3);
    if (serial !== previewRequest || current) return;
    overlays.forEach(layer => layer.remove());
    overlays = [];
    previewLayer = L.geoJSON(shape, {style:{color:'#e9f89c',weight:2,fillOpacity:0}}).addTo(map);
    if (previewLayer.getBounds().isValid()) map.fitBounds(previewLayer.getBounds(), {padding:[18,18],maxZoom:17});
  } catch (error) {
    if (serial === previewRequest && !current) $('availability').textContent = error.message;
  }
}

$('region').onchange = () => {
  resetCropOptions();
  areaRequest++;
  const nationwide = $('region').value === 'Nationwide';
  if ($('map-panel').hidden !== nationwide) $('results').hidden = true;
  $('map-panel').hidden = nationwide;
  $('workspace').classList.toggle('table-only', nationwide);
  for (const format of $('settings').querySelectorAll('input[name=output]')) {
    format.disabled = nationwide && format.value !== 'table';
    format.closest('label').hidden = format.disabled;
    if (nationwide && format.value === 'table') format.checked = true;
  }
  resetArea('province', 'Entire region');
  resetArea('municipality', 'Entire province');
  resetArea('barangay', 'Entire municipality or city');
  const items = provinces.filter(p => p.region === $('region').value);
  $('province').disabled = items.length === 0;
  items.forEach(row => option($('province'), row.id, row.name));
  updateAvailability();
  previewBoundary();
};

async function loadChildren(id, path) {
  const serial = ++areaRequest;
  if (!path) return;
  try {
    const rows = await fetchData(path, undefined, 3);
    if (serial !== areaRequest) return;
    for (const row of rows) option($(id), row.id, row.name);
    $(id).disabled = rows.length === 0;
  } catch (error) {
    if (serial === areaRequest) $('availability').textContent = error.message;
  }
}

$('province').onchange = () => {
  resetCropOptions();
  resetArea('municipality', 'Entire province');
  resetArea('barangay', 'Entire municipality or city');
  const {province} = selection();
  updateAvailability();
  previewBoundary();
  loadChildren('municipality', province && '/api/areas/' + encodeURIComponent(province) + '/municipalities');
};
$('municipality').onchange = () => {
  resetCropOptions();
  resetArea('barangay', 'Entire municipality or city');
  const {province, municipality} = selection();
  updateAvailability();
  previewBoundary();
  loadChildren('barangay', municipality && '/api/areas/' + encodeURIComponent(province)
    + '/municipalities/' + encodeURIComponent(municipality) + '/barangays');
};
$('barangay').onchange = () => {
  resetCropOptions();
  previewBoundary();
};
$('mode').onchange = updateAvailability;
let cropRequest = 0;
let loadedCropArea = null;
let loadingCropArea = null;

function resetCropOptions() {
  cropRequest++;
  loadedCropArea = null;
  loadingCropArea = null;
  $('crop-picker').open = false;
  $('crop-label').textContent = 'All mapped crops';
  $('crops').replaceChildren();
}

function renderCropOptions(crops) {
  $('crops').replaceChildren();
  if (!crops.length) {
    node('p', 'No mapped crops were found in the land-use data for this area.', $('crops'));
    return;
  }
    const all = node('label', undefined, $('crops'));
    const allCheckbox = node('input', undefined, all);
    allCheckbox.type = 'checkbox'; allCheckbox.checked = true;
    all.append(' All mapped crops');
    const boxes = crops.map(({name, labels}) => {
      const line = node('label', undefined, $('crops'));
      const input = node('input', undefined, line);
      input.type = 'checkbox'; input.value = name;
      const sourceLabels = labels.filter(label => label.toLowerCase() !== name.toLowerCase());
      line.append(' ' + name + (sourceLabels.length ? ' (' + sourceLabels.join(', ') + ')' : ''));
      input.onchange = () => {
        const count = boxes.filter(b => b.checked).length;
        allCheckbox.checked = count === 0;
        $('crop-label').textContent = count ? count + ' selected' : 'All mapped crops';
      };
      return input;
    });
    allCheckbox.onchange = () => {
      allCheckbox.checked = true;
      boxes.forEach(b => b.checked = false);
      $('crop-label').textContent = 'All mapped crops';
    };
}

async function loadCropOptions() {
  const area = selection();
  const key = JSON.stringify(area);
  if (!area.region) {
    $('crops').replaceChildren();
    node('p', 'Select an assessment area to see its mapped crops.', $('crops'));
    return;
  }
  if (loadedCropArea === key || loadingCropArea === key) return;
  const serial = ++cropRequest;
  loadingCropArea = key;
  $('crops').replaceChildren();
  const message = node('p', 'Reading crop labels from the land-use data…', $('crops'));
  message.setAttribute('role', 'status');
  try {
    const job = await fetchData('/api/crop-options', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: key
    });
    while (serial === cropRequest) {
      const state = await fetchData('/api/jobs/' + job.id, undefined, 3);
      if (serial !== cropRequest) return;
      if (state.status === 'complete') {
        loadedCropArea = key;
        renderCropOptions(state.result.crops);
        return;
      }
      if (['failed', 'cancelled'].includes(state.status)) throw new Error(state.stage);
      message.textContent = state.stage;
      await wait(2500);
    }
  } catch (error) {
    if (serial === cropRequest) message.textContent = error.message + ' Close and reopen this list to retry.';
  } finally {
    if (serial === cropRequest) loadingCropArea = null;
  }
}

$('crop-picker').addEventListener('toggle', () => {
  if ($('crop-picker').open) loadCropOptions();
});

async function init() {
  if (window.L) {
    map = L.map('map').setView([12.55, 122.5], 5);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, attribution: 'Tiles © Esri — Maxar, Earthstar Geographics, and the GIS User Community'
    }).addTo(map);
    L.control.scale({imperial: false}).addTo(map);
  }
  try {
    const catalog = await Promise.any([
      fetchData('/api/catalog', {signal: AbortSignal.timeout(12000)}),
      wait(700).then(async () => {
        const response = await fetch('./catalog.json');
        if (!response.ok) throw new Error('The saved GIS catalog is unavailable.');
        return response.json();
      })
    ]);
    provinces = catalog.provinces;
    $('region').replaceChildren();
    option($('region'), '', 'Choose a region');
    option($('region'), 'Nationwide', 'Nationwide (table)');
    [...new Set(provinces.map(p => p.region))].forEach(name => option($('region'), name, name));
  } catch (error) {
    $('availability').textContent = 'The GIS catalog could not be loaded. Refresh the page to retry.';
    $('region').options[0].textContent = 'GIS catalog unavailable';
  }
}
function renderZoneRow(parent, values) {
  const row = node('tr', undefined, parent);
  node('th', values[0], row).scope = 'row';
  for (const value of values.slice(1)) node('td', number.format(value), row);
}

function renderStats(result, id) {
  $('results').hidden = false;
  $('map-title').textContent = result.area;
  $('badge').textContent = result.level.toUpperCase() + ' · SATELLITE';
  const qa = result.qa || {};
  const available = qa.source_crop_labels || [];
  const unknown = Object.keys(qa.unmapped_land_classes_ha || {});
  let note = result.focused ? 'Crop hectares inside the selected agricultural zones. Combined mode counts shared areas once.'
    : 'Hectares calculated within the selected boundary.';
  if (result.focused && result.features === 0 && Object.keys(result.stats).length) {
    note = 'Crops were found, but none intersected the selected agricultural zones.';
  }
  if (!Object.keys(result.stats).length) {
    if (qa.land_polygons === 0) note = 'No land-use polygons intersect the selected boundary.';
    else if (available.length) note = 'No polygons matched the selected crops. Crops found in this area: ' + available.join(', ') + '.';
    else if (unknown.length) note = 'Land-use polygons were found, but none had a confirmed crop label.';
    else note = 'No crop polygons were identified in the land-use data for this area.';
  }
  if (unknown.length) note += ' Unconfirmed source labels are listed in the QA/QC report.';
  if (result.missing_provinces?.length) note += ' Partial coverage. Excluded: ' + result.missing_provinces.join(', ') + '.';
  $('result-note').textContent = note;
  const rows = Object.entries(result.stats).sort((a, b) => b[1]['Total mapped'] - a[1]['Total mapped']);
  const fields = [...new Set(['Total mapped', ...rows.flatMap(([, values]) => Object.keys(values))])];
  const totals = Object.fromEntries(fields.map(field => [field, rows.reduce((sum, [, r]) => sum + (r[field] || 0) * (field.endsWith('(%)') && !field.startsWith('Share of ') ? r['Total mapped'] / rows.reduce((total, [, crop]) => total + crop['Total mapped'], 0) : 1), 0)]));
  $('cards').hidden = result.level === 'Nationwide';
  $('chart').hidden = $('cards').hidden;
  $('cards').replaceChildren();
  for (const field of ['Within selected zone(s)', 'Total mapped', 'Agricultural NPAAAD', 'Agricultural SAFDZ', 'Missing coverage']) {
    if (!rows.length || !(field in totals)) continue;
    const card = node('div', field, $('cards'));
    card.className = 'card';
    node('strong', number.format(totals[field]) + ' ha', card);
  }
  $('chart').replaceChildren();
  rows.slice(0, 10).forEach(([name, values]) => {
    const bar = node('div', undefined, $('chart')); bar.className = 'bar';
    node('span', name, bar);
    const track = node('div', undefined, bar); track.className = 'track';
    node('span', undefined, track).style.width = 100 * values['Total mapped'] / rows[0][1]['Total mapped'] + '%';
    node('span', number.format(values['Total mapped']) + ' ha', bar);
  });
  const matrix = $('matrix');
  matrix.replaceChildren();
  if (result.zone_tables?.length) {
    for (const sheet of result.zone_tables) {
      const section = node('section', undefined, matrix);
      section.className = 'zone-matrix';
      node('h3', sheet.zone.toUpperCase() + ' crop area (ha)', section);
      const scroll = node('div', undefined, section);
      scroll.className = 'matrix';
      const table = node('table', undefined, scroll);
      const thead = node('thead', undefined, table);
      const codes = node('tr', undefined, thead);
      ['Crops', ...sheet.columns].forEach(code => node('th', code, codes).scope = 'col');
      const body = node('tbody', undefined, table);
      sheet.rows.forEach(row => renderZoneRow(body, row));
      renderZoneRow(node('tfoot', undefined, table), sheet.total);
      if (sheet.labels.some(Boolean)) {
        const legend = node('details', undefined, section);
        legend.className = 'zone-legend';
        node('summary', 'Zone legend', legend);
        const definitions = node('dl', undefined, legend);
        sheet.columns.forEach((code, index) => {
          const description = sheet.labels[index];
          if (!description) return;
          const entry = node('div', undefined, definitions);
          node('dt', code, entry);
          node('dd', description, entry);
        });
      }
    }
    if (result.zone_tables.length > 1) {
      node('p', 'Each table summarizes the same crop polygons by a different zoning layer. Adding the totals would count some areas twice.', matrix).className = 'matrix-note';
    }
  } else if (rows.length) {
    const scroll = node('div', undefined, matrix);
    scroll.className = 'matrix';
    const table = node('table', undefined, scroll);
    const head = node('tr', undefined, node('thead', undefined, table));
    ['Crop', ...fields.map(field => field.endsWith('(%)') ? field : field + ' (ha)')].forEach(field => node('th', field, head));
    const body = node('tbody', undefined, table);
    [...rows, ['Total', totals]].forEach(([crop, values]) =>
      renderZoneRow(body, [crop, ...fields.map(field => values[field] || 0)]));
  }
  $('exports').replaceChildren();
  result.exports.forEach(ext => node('a', labels[ext], $('exports')).href = api + '/api/jobs/' + id + '/files/' + ext);
}
async function renderMap(result, id) {
  if (!map || result.level === 'Nationwide') return;
  const paths = ['boundary.geojson', 'map.geojson'].map(file => '/api/jobs/' + id + '/files/' + file);
  const [boundary, classified] = await Promise.all(paths.map(path => fetchData(path)));
  if (previewLayer) previewLayer.remove();
  overlays.forEach(layer => layer.remove());
  overlays = [
    L.geoJSON(boundary, {style: {color: '#fff2bc', weight: 2, fillOpacity: 0}}).addTo(map),
    L.geoJSON(classified, {
      style: f => ({color: result.colors[f.properties.assessment], weight: 1, fillOpacity: .7}),
      onEachFeature: (feature, layer) => layer.bindTooltip(node('span', feature.properties.assessment))
    }).addTo(map)
  ];
  map.fitBounds(overlays[0].getBounds(), {padding: [16, 16]});
  $('legend').replaceChildren();
  classified.features.forEach(feature => {
    const label = feature.properties.assessment;
    const item = node('span', undefined, $('legend'));
    const swatch = node('i', undefined, item);
    swatch.className = 'swatch'; swatch.style.background = result.colors[label];
    item.append(label);
  });
}
$('settings').onsubmit = async event => {
  event.preventDefault();
  if (current) return;
  current = 'starting'; updateAvailability();
  $('settings').classList.add('busy');
  $('settings').inert = true;
  $('job').hidden = false; $('cancel').hidden = true;
  $('status').textContent = 'Starting assessment…';
  $('job').querySelector('progress').value = 0;
  try {
    const result = await fetchData('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...selection(), focused:true, mode:$('mode').value,
      output:$('settings').querySelector('input[name=output]:checked').value,
      crops:[...$('crops').querySelectorAll('input[value]:checked')].map(box => box.value)})});
    current = result.id; $('cancel').hidden = false;
    while (true) {
      const state = await fetchData('/api/jobs/' + current, undefined, 3);
      $('status').textContent = state.stage + ' · ' + state.elapsed + 's';
      $('job').querySelector('progress').value = state.progress;
      if (state.status === 'complete') {
        renderStats(state.result, current);
        await renderMap(state.result, current);
        break;
      }
      if (['failed','cancelled'].includes(state.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1300));
    }
  } catch (error) {
    $('status').textContent = error.message;
  } finally {
    current = null; $('settings').inert = false; $('settings').classList.remove('busy'); $('cancel').hidden = true; updateAvailability();
  }
};
$('cancel').onclick = async () => {
  if (!current || current === 'starting') return;
  try { await fetchData('/api/jobs/' + current, {method:'DELETE'}); }
  catch (error) { $('status').textContent = error.message; }
};
init();
