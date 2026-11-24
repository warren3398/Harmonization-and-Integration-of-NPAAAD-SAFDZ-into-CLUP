const $ = id => document.getElementById(id);
const api = window.PCLAM_API || '';
let provinces = [], current = null, map, overlays = [];
const modes = {combined: ['npaaad', 'safdz'], npaaad: ['npaaad'], safdz: ['safdz']};
const labels = {csv:'CSV statistics',xlsx:'Excel matrix',pdf:'PDF assessment report',gpkg:'GeoPackage',
  'shp.zip':'Shapefile ZIP',geojson:'GeoJSON','qa.json':'QA/QC JSON','map.png':'Map PNG','map.pdf':'Map PDF','map.jpg':'Download JPEG map'};
const number = new Intl.NumberFormat('en-PH', {maximumFractionDigits: 2});
function node(tag, text, parent) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (parent) parent.append(element);
  return element;
}
function option(select, value, title) { const o = node('option', title, select); o.value = value; }
async function fetchData(path, options) {
  const response = await fetch(api + path, options);
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === 'string' ? detail : 'The GIS service is temporarily unavailable.');
  }
  return response.json();
}
function updateAvailability() {
  const candidates = provinces.filter(p => p.region === $('region').value &&
    (!$('province').value || p.id === $('province').value));
  const eligible = candidates.filter(p => p.land_available && modes[$('mode').value].every(key => p.coverage.includes(key)));
  $('availability').textContent = candidates.length && !eligible.length
    ? 'Required source layers are unavailable for this selection.'
    : eligible.length < candidates.length ? 'Partial coverage. Included provinces: ' + eligible.map(p => p.name).join(', ') : '';
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
  $('map-title').textContent = ['barangay','municipality','province','region'].map(key =>
    area[key] ? $(key).selectedOptions[0]?.textContent : null).filter(Boolean).join(' · ');
  try {
    const query = new URLSearchParams(Object.entries(area).filter(([, id]) => id));
    const shape = await fetchData('/api/boundary?' + query);
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
  areaRequest++;
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
    const rows = await fetchData(path);
    if (serial !== areaRequest) return;
    for (const row of rows) option($(id), row.id, row.name);
    $(id).disabled = rows.length === 0;
  } catch (error) {
    if (serial === areaRequest) $('availability').textContent = error.message;
  }
}

$('province').onchange = () => {
  resetArea('municipality', 'Entire province');
  resetArea('barangay', 'Entire municipality or city');
  const {province} = selection();
  updateAvailability();
  previewBoundary();
  loadChildren('municipality', province && '/api/areas/' + encodeURIComponent(province) + '/municipalities');
};
$('municipality').onchange = () => {
  resetArea('barangay', 'Entire municipality or city');
  const {province, municipality} = selection();
  updateAvailability();
  previewBoundary();
  loadChildren('barangay', municipality && '/api/areas/' + encodeURIComponent(province)
    + '/municipalities/' + encodeURIComponent(municipality) + '/barangays');
};
$('barangay').onchange = previewBoundary;
$('mode').onchange = updateAvailability;
async function init() {
  if (window.L) {
    map = L.map('map').setView([12.55, 122.5], 5);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, attribution: 'Tiles © Esri — Maxar, Earthstar Geographics, and the GIS User Community'
    }).addTo(map);
    L.control.scale({imperial: false}).addTo(map);
  }
  try {
    const catalog = await fetchData('/api/catalog');
    provinces = catalog.provinces;
    $('region').replaceChildren();
    option($('region'), '', 'Choose a region');
    [...new Set(provinces.map(p => p.region))].forEach(name => option($('region'), name, name));
    const all = node('label', undefined, $('crops'));
    const allCheckbox = node('input', undefined, all);
    allCheckbox.type = 'checkbox'; allCheckbox.checked = true;
    all.append(' All mapped crops');
    const boxes = catalog.crops.map(crop => {
      const line = node('label', undefined, $('crops'));
      const input = node('input', undefined, line);
      input.type = 'checkbox'; input.value = crop;
      line.append(' ' + crop);
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
  } catch (error) {
    $('availability').textContent = error.message;
    $('region').options[0].textContent = 'GIS catalog unavailable';
  }
}
function renderStats(result, id) {
  $('results').hidden = false;
  $('map-title').textContent = result.area;
  $('badge').textContent = result.level.toUpperCase() + ' · SATELLITE';
  $('result-note').textContent = Object.keys(result.stats).length
    ? (result.missing_provinces?.length ? 'Partial coverage. Excluded: ' + result.missing_provinces.join(', ') : 'Hectares calculated within the selected boundary.')
    : 'No confirmed crop polygons matched the selection. See the QA report for unmapped source labels.';
  const rows = Object.entries(result.stats).sort((a, b) => b[1]['Total mapped'] - a[1]['Total mapped']);
  const fields = [...new Set(['Total mapped', ...rows.flatMap(([, values]) => Object.keys(values))])];
  const totals = Object.fromEntries(fields.map(field => [field, rows.reduce((sum, [, r]) => sum + (r[field] || 0) * (field.endsWith('(%)') && !field.startsWith('Share of ') ? r['Total mapped'] / rows.reduce((total, [, crop]) => total + crop['Total mapped'], 0) : 1), 0)]));
  $('cards').replaceChildren();
  for (const field of ['Total mapped', 'Agricultural NPAAAD', 'Agricultural SAFDZ', 'Missing coverage']) {
    if (!(field in totals)) continue;
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
  $('matrix').replaceChildren();
  const head = node('tr', undefined, node('thead', undefined, $('matrix')));
  ['Crop', ...fields.map(field => field.endsWith('(%)') ? field : field + ' (ha)')].forEach(field => node('th', field, head));
  const body = node('tbody', undefined, $('matrix'));
  [...rows, ['Total', totals]].forEach(([crop, values]) => {
    const line = node('tr', undefined, body);
    node('td', crop, line);
    fields.forEach(field => node('td', number.format(values[field] || 0), line));
  });
  $('exports').replaceChildren();
  result.exports.forEach(ext => node('a', labels[ext], $('exports')).href = api + '/api/jobs/' + id + '/files/' + ext);
}
async function renderMap(result, id) {
  if (!map) return;
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
      body:JSON.stringify({...selection(), mode:$('mode').value,
      output:$('settings').querySelector('input[name=output]:checked').value,
      crops:[...$('crops').querySelectorAll('input[value]:checked')].map(box => box.value)})});
    current = result.id; $('cancel').hidden = false;
    while (true) {
      const state = await fetchData('/api/jobs/' + current);
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
