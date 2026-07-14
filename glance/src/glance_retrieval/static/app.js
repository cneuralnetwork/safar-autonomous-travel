const form = document.querySelector('#search-form');
const input = document.querySelector('#query');
const gallery = document.querySelector('#gallery');
const status = document.querySelector('#status');
const title = document.querySelector('#result-title');
const intentElement = document.querySelector('#intent');
const latencyElement = document.querySelector('#latency');
const corpusElement = document.querySelector('#corpus-label');
const runtimePill = document.querySelector('#runtime-pill');
const template = document.querySelector('#result-card-template');
const resultsSection = document.querySelector('.results');
const submit = document.querySelector('#submit');
const dialog = document.querySelector('#result-dialog');

let activeRequest = null;
let hasSearched = false;

const scoreParts = [
  ['scene', 'generic_similarity'],
  ['fashion', 'fashion_similarity'],
  ['garments', 'garment_satisfaction'],
  ['metadata', 'metadata_match'],
];

function checked(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((item) => item.value);
}

function selectedK() {
  return Number(document.querySelector('input[name="k"]:checked')?.value || 8);
}

function label(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function clamp(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function percent(value) {
  return `${Math.round(clamp(value) * 100)}%`;
}

function addTag(container, text, matched = false) {
  if (!text) return;
  const tag = document.createElement('span');
  tag.textContent = text;
  tag.classList.toggle('is-match', matched);
  container.append(tag);
}

function renderScores(container, breakdown, dialogMode = false) {
  container.replaceChildren();
  for (const [partLabel, key] of scoreParts) {
    const value = clamp(breakdown?.[key]);
    const row = document.createElement('div');
    row.className = dialogMode ? 'dialog-score' : 'score-line';

    const name = document.createElement('span');
    name.textContent = partLabel;
    const track = document.createElement('i');
    const fill = document.createElement('b');
    fill.style.setProperty('--value', percent(value));
    track.append(fill);
    const reading = document.createElement('em');
    reading.textContent = percent(value);
    row.append(name, track, reading);
    container.append(row);
  }
}

function resultTitle(result) {
  if (result.scene) return label(result.scene);
  if (result.styles?.length) return `${label(result.styles[0])} look`;
  return 'Fashion look';
}

function openInspector(result) {
  const image = dialog.querySelector('.dialog-image img');
  image.src = result.image_url;
  image.alt = result.caption || `Fashion retrieval result ${result.rank}`;
  document.querySelector('#dialog-rank').textContent = `Result ${String(result.rank).padStart(2, '0')} · ${percent(result.score)} fit`;
  document.querySelector('#dialog-title').textContent = resultTitle(result);
  document.querySelector('#dialog-caption').textContent = result.caption || 'No caption is available for this image.';

  const tags = document.querySelector('#dialog-tags');
  tags.replaceChildren();
  result.matched_attributes.forEach((item) => addTag(tags, label(item), true));
  result.garments.slice(0, 8).forEach((garment) => addTag(tags, label(`${garment.color || ''} ${garment.category}`.trim())));
  renderScores(document.querySelector('#dialog-scores'), result.score_breakdown, true);

  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function makeCard(result, index) {
  const fragment = template.content.cloneNode(true);
  const card = fragment.querySelector('.result-card');
  card.style.animationDelay = `${Math.min(index * 45, 280)}ms`;

  const imageButton = fragment.querySelector('.image-wrap');
  const image = fragment.querySelector('img');
  image.src = result.image_url;
  image.alt = result.caption || `Fashion retrieval result ${result.rank}`;
  image.addEventListener('error', () => {
    image.alt = 'The indexed source image is unavailable.';
    imageButton.classList.add('image-missing');
  });
  imageButton.setAttribute('aria-label', `Inspect result ${result.rank}: ${resultTitle(result)}`);
  imageButton.addEventListener('click', () => openInspector(result));

  fragment.querySelector('.rank').textContent = `#${String(result.rank).padStart(2, '0')}`;
  fragment.querySelector('.score').textContent = `${percent(result.score)} fit`;
  fragment.querySelector('h3').textContent = resultTitle(result);
  fragment.querySelector('.caption').textContent = result.caption || 'No caption available.';

  const tags = fragment.querySelector('.tags');
  result.matched_attributes.slice(0, 5).forEach((item) => addTag(tags, label(item), true));
  if (!result.matched_attributes.length) addTag(tags, 'Semantic match', true);
  const existing = new Set(result.matched_attributes.map((item) => item.toLowerCase()));
  result.garments.slice(0, 4).forEach((garment) => {
    const garmentLabel = `${garment.color || ''} ${garment.category}`.trim();
    if (!existing.has(garmentLabel.toLowerCase())) addTag(tags, label(garmentLabel));
  });

  renderScores(fragment.querySelector('.score-grid'), result.score_breakdown);
  return fragment;
}

function render(results) {
  gallery.replaceChildren();
  if (!results.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const copy = document.createElement('div');
    const heading = document.createElement('strong');
    heading.textContent = 'No evidence passed the filters.';
    const explanation = document.createElement('p');
    explanation.textContent = 'Try removing a scene or style filter, or describe the same look with a broader phrase.';
    copy.append(heading, explanation);
    empty.append(copy);
    gallery.append(empty);
    return;
  }
  results.forEach((result, index) => gallery.append(makeCard(result, index)));
}

function renderSkeletons(count) {
  gallery.replaceChildren();
  for (let index = 0; index < Math.min(count, 8); index += 1) {
    const card = document.createElement('div');
    card.className = 'skeleton';
    card.setAttribute('aria-hidden', 'true');
    const image = document.createElement('div');
    image.className = 'skeleton-image';
    const copy = document.createElement('div');
    copy.className = 'skeleton-copy';
    card.append(image, copy);
    gallery.append(card);
  }
}

function renderIntent(intent) {
  intentElement.replaceChildren();
  addTag(intentElement, `${intent.parser} parser`, true);
  addTag(intentElement, intent.scene ? `scene · ${label(intent.scene)}` : null);
  addTag(intentElement, intent.style ? `style · ${label(intent.style)}` : null);
  addTag(intentElement, intent.activity ? `activity · ${label(intent.activity)}` : null);
  intent.garments.forEach((garment) => addTag(intentElement, `garment · ${label(`${garment.color || ''} ${garment.category}`.trim())}`));
  if (intentElement.childElementCount === 1) addTag(intentElement, 'Open semantic search');
}

function syncQueryUrl(query, k, scenes, styles) {
  const parameters = new URLSearchParams();
  parameters.set('q', query);
  parameters.set('k', String(k));
  scenes.forEach((scene) => parameters.append('scene', scene));
  styles.forEach((style) => parameters.append('style', style));
  history.replaceState(null, '', `${location.pathname}?${parameters.toString()}#results`);
}

async function refreshHealth() {
  try {
    const response = await fetch('/api/health');
    if (!response.ok) throw new Error('Health endpoint unavailable');
    const health = await response.json();
    runtimePill.className = `runtime-pill ${health.index_loaded ? 'is-ready' : 'is-standby'}`;
    runtimePill.querySelector('span').textContent = health.index_loaded ? 'Index ready' : 'Warms on first search';
    if (health.corpus_size) corpusElement.textContent = `${health.corpus_size.toLocaleString()} images · ${health.backend.replace('-', ' ')}`;
    runtimePill.title = `${health.model_profile} · ${health.device}`;
  } catch (error) {
    runtimePill.className = 'runtime-pill is-error';
    runtimePill.querySelector('span').textContent = 'Service unavailable';
    runtimePill.title = error.message;
  }
}

async function search(event) {
  if (event) event.preventDefault();
  const query = input.value.trim();
  if (query.length < 2) {
    input.focus();
    return;
  }

  activeRequest?.abort();
  activeRequest = new AbortController();
  const k = selectedK();
  const scenes = checked('scene');
  const styles = checked('style');
  const browserStarted = performance.now();

  hasSearched = true;
  submit.disabled = true;
  resultsSection.setAttribute('aria-busy', 'true');
  status.classList.remove('is-error');
  status.textContent = 'Tracing scene, style, and localized garment evidence…';
  title.textContent = 'Reading the look…';
  latencyElement.textContent = 'Inference running';
  renderSkeletons(k);

  try {
    const response = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, k, scenes, styles }),
      signal: activeRequest.signal,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Search could not be completed.');

    render(body.results);
    renderIntent(body.intent);
    title.textContent = body.results.length === 1 ? 'One look, ranked.' : `${body.results.length} looks, ranked.`;
    const roundTrip = performance.now() - browserStarted;
    latencyElement.textContent = `${Math.round(body.elapsed_ms)} ms model · ${Math.round(roundTrip)} ms round trip`;
    corpusElement.textContent = `${body.corpus_size.toLocaleString()} images · ${body.model_profile}`;
    status.textContent = body.results.length
      ? 'Every card exposes the four signals behind its final score. Select an image to inspect it.'
      : 'The semantic candidates were found, but none survived the selected filters.';
    syncQueryUrl(query, k, scenes, styles);
    await refreshHealth();
  } catch (error) {
    if (error.name === 'AbortError') return;
    gallery.replaceChildren();
    title.textContent = 'The archive is not ready.';
    intentElement.replaceChildren();
    addTag(intentElement, 'Search unavailable', true);
    status.classList.add('is-error');
    status.textContent = error.message;
    latencyElement.textContent = 'Request failed';
  } finally {
    submit.disabled = false;
    resultsSection.setAttribute('aria-busy', 'false');
  }
}

function hydrateFromUrl() {
  const parameters = new URLSearchParams(location.search);
  const query = parameters.get('q');
  const k = parameters.get('k');
  if (query) input.value = query;
  if (k) document.querySelector(`input[name="k"][value="${k}"]`)?.click();
  for (const scene of parameters.getAll('scene')) document.querySelector(`input[name="scene"][value="${scene}"]`)?.click();
  for (const style of parameters.getAll('style')) document.querySelector(`input[name="style"][value="${style}"]`)?.click();
  return Boolean(query);
}

form.addEventListener('submit', search);
document.querySelectorAll('[data-query]').forEach((button) => button.addEventListener('click', () => {
  input.value = button.dataset.query;
  search();
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}));

document.querySelector('#clear-filters').addEventListener('click', () => {
  document.querySelectorAll('input[name="scene"], input[name="style"]').forEach((control) => { control.checked = false; });
  if (hasSearched) search();
});

document.querySelectorAll('input[name="k"]').forEach((control) => control.addEventListener('change', () => {
  if (hasSearched) search();
}));

document.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', (event) => {
  if (event.target === dialog) dialog.close();
});

document.addEventListener('keydown', (event) => {
  if (event.key === '/' && document.activeElement?.tagName !== 'INPUT') {
    event.preventDefault();
    input.focus();
    input.select();
  }
});

const hasSharedQuery = hydrateFromUrl();
refreshHealth().then(() => {
  if (hasSharedQuery) search();
});
