(() => {
  'use strict';
  const $ = id => document.getElementById('ss-' + id);
  let files = [], urls = [], selected = new URLSearchParams(location.search).get('batch') || '', snapshot = null, busy = false, timer, generation = 0;
  const labels = {processing:'Identifying',ready:'Ready for review',needs_review:'Needs review',applied:'Draft prepared',published:'Published',failed:'Retry needed'};
  function tab(name) {
    $('capture').hidden = name !== 'capture'; $('batches').hidden = name !== 'batches';
    document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tab === name)));
  }
  document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => tab(b.dataset.tab)));
  function previews() {
    urls.forEach(URL.revokeObjectURL); urls = []; $('previews').replaceChildren();
    files.forEach((f, i) => {
      const fig = document.createElement('figure'), img = document.createElement('img'), cap = document.createElement('figcaption');
      img.src = URL.createObjectURL(f); urls.push(img.src); img.alt = 'Selected photo ' + (i + 1);
      cap.textContent = $('mode').value === 'yes' ? `Card ${Math.floor(i / 2) + 1} · ${i % 2 ? 'back' : 'front'}` : `Card ${i + 1} · front`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove'; remove.disabled = busy;
      remove.addEventListener('click', () => { if (!busy) { files.splice(i,1); previews(); } });
      fig.append(img, cap, remove); $('previews').append(fig);
    });
    $('count').textContent = `${files.length} photos selected · up to 40 per batch`;
  }
  ['files','camera'].forEach(id => $(id).addEventListener('change', e => {
    if (busy) return;
    const added = Array.from(e.target.files);
    if (files.length + added.length > 40) { $('error').textContent = 'Choose no more than 40 photos per batch.'; e.target.value = ''; return; }
    files.push(...added); e.target.value = ''; $('error').textContent = ''; previews();
  }));
  let stream = null, capturing = false;
  function stopCamera() {
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null; $('video').srcObject = null; $('camera-view').hidden = true;
    $('camera-start').hidden = false;
  }
  $('camera-start').addEventListener('click', async () => {
    if (busy || stream) return;
    if (!navigator.mediaDevices?.getUserMedia) { $('camera-message').textContent = 'Camera unavailable here. Use Take photo or Choose photos.'; return; }
    $('camera-start').disabled = true;
    try {
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
      if (document.hidden) { stopCamera(); return; }
      $('video').srcObject = stream; await $('video').play();
      $('camera-view').hidden = false; $('camera-start').hidden = true;
      $('camera-message').textContent = 'Frame one card, capture, then place the next card. Photos stay here until you press Identify photos.';
    } catch (_) { stopCamera(); $('camera-message').textContent = 'Camera could not open. Allow camera access or use Take photo.'; }
    finally { $('camera-start').disabled = false; }
  });
  $('camera-stop').addEventListener('click', stopCamera);
  $('shutter').addEventListener('click', async () => {
    if (busy || capturing || !stream) return;
    if (files.length >= 40) { $('camera-message').textContent = 'Batch full: identify these 40 photos before taking more.'; return; }
    const video = $('video'); if (!video.videoWidth) return;
    capturing = true; $('shutter').disabled = true;
    try {
      const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video,0,0);
      const blob = await new Promise(resolve => canvas.toBlob(resolve,'image/jpeg',0.95));
      if (!blob) throw new Error('Capture failed');
      files.push(new File([blob],`card-${Date.now()}.jpg`,{type:'image/jpeg'})); previews();
      $('camera-message').textContent = `Captured photo ${files.length}. Ready for the next ${$('mode').value === 'yes' ? (files.length % 2 ? 'back' : 'front') : 'card'}.`;
    } catch (_) { $('camera-message').textContent = 'Could not capture. Try again or use Take photo.'; }
    finally { capturing = false; $('shutter').disabled = false; }
  });
  $('mode').addEventListener('change', previews);
  $('clear').addEventListener('click', () => { if (!busy) { files = []; previews(); } });
  function element(tag, text, cls) { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; }
  function renderCards() {
    if (!snapshot) return;
    const frag = document.createDocumentFragment(), filter = $('filter').value;
    snapshot.cards.filter(c => filter === 'all' || (filter === 'attention' && ['needs_review','failed'].includes(c.status)) || (filter === 'ready' && c.status === 'ready') || (filter === 'stored' && c.stored)).forEach(c => {
      const card = element('article', '', 'ss-card'), img = element('img'); img.src = c.image; img.alt = c.name; img.loading = 'lazy';
      card.append(img, element('h3', `${c.position}. ${c.name}${c.number ? ' #' + c.number : ''}`), element('span', labels[c.status] || c.status, 'ss-status'));
      if (c.issue) card.append(element('p', c.issue));
      if (c.receipt) { const receipt = element('a', c.receipt.added ? `This batch: +${c.receipt.added} copies →` : 'Photo update · stock unchanged →'); receipt.href = c.receipt.url; card.append(receipt); }
      if (c.slot) card.append(element('p', `${c.stored ? 'Stored' : 'Reserved'}: ${c.slot}`));
      if (c.status !== 'processing') {
        const a = element('a', c.listing_url && ['applied','published'].includes(c.status) ? 'Open listing →' : 'Review card →');
        a.href = c.listing_url && ['applied','published'].includes(c.status) ? c.listing_url : c.review_url; card.append(a);
      }
      frag.append(card);
    });
    if (!frag.childNodes.length) frag.append(element('p', 'No cards in this view.'));
    $('cards').replaceChildren(frag);
  }
  $('filter').addEventListener('change', renderCards);
  $('batch').addEventListener('change', () => { selected = $('batch').value; snapshot = null; load(); });
  async function load() {
    const current = ++generation;
    clearTimeout(timer);
    if (document.hidden) return;
    try {
      const r = await fetch('/scan-studio/data' + (selected ? '?batch_id=' + encodeURIComponent(selected) : ''), {cache:'no-store',headers:{Accept:'application/json'}});
      if (r.status === 401 || r.redirected) throw new Error('Session expired. Reload to sign in.');
      if (!r.ok) throw new Error('Connection interrupted. Retrying…');
      const data = await r.json(); if (current !== generation) return; selected = data.selected || '';
      const sig = JSON.stringify(data);
      if (sig !== JSON.stringify(snapshot)) {
        const batchSelect = $('batch'); batchSelect.replaceChildren();
        data.batches.forEach(b => { const o = element('option', `${new Date(b.date + (/[Z+]/.test(b.date) ? '' : 'Z')).toLocaleString()} · ${b.total} cards`); o.value = b.id; batchSelect.append(o); });
        batchSelect.value = selected;
        const b = data.batches.find(b => b.id === selected);
        const modeNames = {new_stock:'New stock',additional_copies:'Additional copies',photo_update:'Photo update',legacy:'Earlier workflow'};
        $('summary').textContent = b ? `${modeNames[b.intake_mode] || "Batch"} · ${b.processed}/${b.total} identified · ${b.review} need review · ${b.failed} failed` : 'Your photo batches will appear here.';
        $('pricing').hidden = !b || b.intake_mode === 'legacy';
        $('pricing').href = '/scan-studio/batches/' + selected + '/pricing';
        $('review').hidden = !selected;
        $('storage').hidden = !data.cards.some(c => c.slot);
        const next = data.cards.find(c => !c.receipt && ['ready','needs_review'].includes(c.status));
        $('review').href = b && b.intake_mode !== 'legacy' && next ? next.review_url : '/ebay-listing-bulk-photos/' + selected;
        $('storage').href = '/ebay-listing-bulk-photos/' + selected + '/storage-labels';
        snapshot = data; renderCards();
      }
      $('connection').textContent = 'Connected';
    } catch (e) { if (current !== generation) return; $('connection').textContent = e.message || 'Reconnecting…'; }
    timer = setTimeout(load, snapshot && snapshot.cards.some(c => c.status === 'processing') ? 2500 : 12000);
  }
  $('form').addEventListener('submit', e => {
    e.preventDefault(); if (busy || capturing) return;
    if (!files.length || ($('mode').value === 'yes' && files.length % 2)) { $('error').textContent = files.length ? 'Front/back mode needs an even number of photos.' : 'Choose at least one photo.'; return; }
    stopCamera();
    const body = new FormData($('form')); files.forEach(f => body.append('photos', f));
    busy = true; $('error').textContent = ''; $('upload').hidden = false; $('progress').value = 0;
    $('form').querySelectorAll('button,input,select').forEach(x => x.disabled = true);
    const xhr = new XMLHttpRequest(); xhr.open('POST','/ebay-listing-bulk-photos'); xhr.setRequestHeader('Accept','application/json');
    xhr.upload.addEventListener('progress', ev => { if (ev.lengthComputable) { const p = Math.round(ev.loaded / ev.total * 100); $('progress').value = p; $('upload-text').textContent = p < 100 ? `Uploading ${p}%` : 'Upload received. Preparing recognition…'; } });
    const done = () => { busy = false; $('form').querySelectorAll('button,input,select').forEach(x => x.disabled = false); };
    xhr.addEventListener('load', () => {
      done(); let result; try { result = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300 && result && result.batch_id) {
        selected = result.batch_id; files = []; previews(); $('upload').hidden = true; tab('batches');
        history.replaceState(null,'','/scan-studio?batch=' + encodeURIComponent(selected)); load();
      } else {
        let err = ''; try { err = new URL(xhr.responseURL).searchParams.get('error') || ''; } catch (_) {}
        $('error').textContent = err || (result && typeof result.detail === 'string' ? result.detail : 'Upload could not be confirmed. Check My batches before retrying.');
        $('upload-text').textContent = 'Upload not confirmed.';
      }
    });
    xhr.addEventListener('error', () => { done(); $('error').textContent = 'Connection lost. Check My batches before retrying to avoid uploading twice.'; });
    xhr.send(body);
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); clearTimeout(timer); if (!document.hidden) load(); });
  window.addEventListener('pagehide', () => { stopCamera(); clearTimeout(timer); });
  window.addEventListener('beforeunload', e => { if (busy || files.length) { e.preventDefault(); e.returnValue = ''; } });
  if (selected) tab('batches'); load();
})();
