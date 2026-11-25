const mode = document.getElementById('national-mode');
const crop = document.getElementById('national-crop');
const status = document.getElementById('national-status');
const retry = document.getElementById('national-retry');
const result = document.getElementById('national-result');
const total = document.getElementById('national-total');
const coverage = document.getElementById('national-coverage');
const hectares = new Intl.NumberFormat('en-PH', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const modeNames = {npaaad: 'NPAAAD', safdz: 'SAFDZ', combined: 'Both'};
const storageKey = 'pclam-nationwide-selection';
let workbook;

function render() {
  result.hidden = true;
  if (!workbook) return;
  if (!crop.value) {
    status.textContent = 'Select a crop to see its area.';
    return;
  }
  const area = workbook.totals[crop.value][mode.value];
  const row = document.createElement('tr');
  for (const [index, text] of [crop.value, modeNames[mode.value], hectares.format(area)].entries()) {
    const cell = document.createElement(index === 0 ? 'th' : 'td');
    if (index === 0) cell.scope = 'row';
    cell.textContent = text;
    row.append(cell);
  }
  total.replaceChildren(row);
  const selected = workbook.coverage[mode.value];
  const notes = [workbook.source + '.', 'Coverage: ' + selected.included + ' of '
    + (selected.included + selected.excluded.length) + ' areas.'];
  if (mode.value === 'combined') notes.push('Areas shared by NPAAAD and SAFDZ are counted once.');
  if (selected.excluded.length) notes.push('Excluded: ' + selected.excluded.join(', ') + '.');
  if (mode.value !== 'npaaad' && workbook.sourceExclusions) {
    notes.push('See Source notes in the Excel file for exclusions of damaged source features.');
  }
  coverage.textContent = notes.join(' ');
  result.hidden = false;
  status.textContent = '';
  try { sessionStorage.setItem(storageKey, JSON.stringify({mode: mode.value, crop: crop.value})); } catch {}
}

async function loadWorkbook() {
  retry.hidden = true;
  status.textContent = 'Loading Nationwide totals…';
  try {
    const response = await fetch('/api/nationwide-totals');
    if (!response.ok) throw new Error('The Nationwide workbook is temporarily unavailable.');
    workbook = await response.json();
    crop.replaceChildren(new Option('Choose a crop', ''));
    for (const name of Object.keys(workbook.totals)) crop.add(new Option(name, name));
    crop.disabled = false;
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (saved && Object.hasOwn(modeNames, saved.mode)) mode.value = saved.mode;
      if (saved && Object.hasOwn(workbook.totals, saved.crop)) crop.value = saved.crop;
    } catch {}
    render();
  } catch (error) {
    status.textContent = error.message;
    retry.hidden = false;
  }
}

retry.onclick = loadWorkbook;
mode.onchange = crop.onchange = render;
loadWorkbook();
