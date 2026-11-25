const mode = document.getElementById('national-mode');
const crop = document.getElementById('national-crop');
const status = document.getElementById('national-status');
const progress = document.getElementById('national-progress');
const retry = document.getElementById('national-retry');
const result = document.getElementById('national-result');
const total = document.getElementById('national-total');
const coverage = document.getElementById('national-coverage');
const exportsList = document.getElementById('exports');
const hectares = new Intl.NumberFormat('en-PH', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const modeNames = {npaaad: 'NPAAAD', safdz: 'SAFDZ', combined: 'Both'};
const fileNames = {xlsx: 'Excel', csv: 'CSV', 'npaaad.csv': 'NPAAAD CSV', 'safdz.csv': 'SAFDZ CSV'};
const storageKey = 'pclam-nationwide-selection';
let saved;
try { saved = JSON.parse(sessionStorage.getItem(storageKey)); } catch {}
let pending = null;
let change = 0;
let timer;

function save(value) {
  try { sessionStorage.setItem(storageKey, JSON.stringify(value)); } catch {}
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : 'The GIS service is temporarily unavailable.');
    error.status = response.status;
    throw error;
  }
  return data;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function read(path) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await api(path);
    } catch (error) {
      if (attempt >= 4 || (error.status && ![502, 503, 504].includes(error.status))) throw error;
      await wait(1500 * (attempt + 1));
    }
  }
}

function render(data, selection, id) {
  const entries = Object.values(data.stats);
  const within = entries.reduce((sum, row) => sum + (row['Within selected zone(s)'] || 0), 0);
  const row = document.createElement('tr');
  for (const [index, text] of [selection.crop, modeNames[selection.mode], hectares.format(within)].entries()) {
    const cell = document.createElement(index === 0 ? 'th' : 'td');
    if (index === 0) cell.scope = 'row';
    cell.textContent = text;
    row.append(cell);
  }
  total.replaceChildren(row);
  const notes = [];
  if (!entries.length) notes.push('No polygons for this crop were found in the assessed areas.');
  if (selection.mode === 'combined') notes.push('Area shared by NPAAAD and SAFDZ is counted once.');
  if (data.missing_provinces?.length) notes.push('Excluded from coverage: ' + data.missing_provinces.join(', ') + '.');
  coverage.textContent = notes.join(' ');
  coverage.hidden = !notes.length;
  exportsList.replaceChildren();
  for (const file of data.exports) {
    if (!fileNames[file]) continue;
    const link = document.createElement('a');
    link.textContent = fileNames[file];
    link.href = '/api/jobs/' + encodeURIComponent(id) + '/files/' + file;
    exportsList.append(link);
  }
  result.hidden = false;
  status.textContent = 'Assessment ready.';
}

function cancel(id) {
  if (!id) return Promise.resolve();
  return api('/api/jobs/' + encodeURIComponent(id), {method: 'DELETE'}).catch(() => {});
}

async function calculate(selection, serial, previous) {
  try {
    let id = previous?.mode === selection.mode && previous.crop === selection.crop ? previous.id : null;
    if (!id) {
      if (pending === previous) pending = null;
      await cancel(previous?.id);
      if (serial !== change) return;
      ({id} = await api('/api/jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({region: 'Nationwide', output: 'table', focused: true, dashboard: true,
          mode: selection.mode, crops: [selection.crop]})
      }));
    }
    if (serial !== change) {
      await cancel(id);
      return;
    }
    pending = {...selection, id};
    save(pending);
    while (serial === change) {
      let state;
      try {
        state = await read('/api/jobs/' + encodeURIComponent(id));
      } catch (error) {
        if (error.status === 404 && previous?.id === id && serial === change) {
          pending = null;
          return await calculate(selection, serial, null);
        }
        throw error;
      }
      if (serial !== change) return;
      if (state.status === 'complete') {
        pending = null;
        save(selection);
        render(state.result, selection, id);
        break;
      }
      if (state.status === 'failed' || state.status === 'cancelled') {
        pending = null;
        save(selection);
        throw new Error(state.stage);
      }
      status.textContent = state.stage;
      progress.value = state.progress;
      await wait(2500);
    }
  } catch (error) {
    if (serial !== change) return;
    status.textContent = error.message;
    retry.hidden = false;
  } finally {
    if (serial === change) progress.hidden = true;
  }
}

function update() {
  const serial = ++change;
  clearTimeout(timer);
  result.hidden = true;
  retry.hidden = true;
  status.textContent = crop.value ? 'Starting nationwide assessment…' : 'Select a crop to see its area.';
  progress.hidden = !crop.value;
  progress.value = 0;
  if (!crop.value) {
    void cancel(pending?.id);
    pending = null;
    save(null);
    return;
  }
  const selection = {mode: mode.value, crop: crop.value};
  timer = setTimeout(() => calculate(selection, serial, pending), 350);
}

async function loadCrops() {
  retry.hidden = true;
  try {
    const state = await read('/api/jobs/nationwide-crop-options');
    crop.replaceChildren(new Option('Choose a crop', ''));
    for (const entry of state.result.crops) crop.add(new Option(entry.name, entry.name));
    crop.disabled = false;
    if (saved && modeNames[saved.mode] && [...crop.options].some(option => option.value === saved.crop)) {
      mode.value = saved.mode;
      crop.value = saved.crop;
      pending = saved;
    }
    update();
  } catch (error) {
    status.textContent = error.message;
    retry.hidden = false;
  }
}

retry.onclick = () => crop.disabled ? loadCrops() : update();
mode.onchange = crop.onchange = update;
loadCrops();
