// Inline auto-save for [data-field] inputs and the "Write proposal" dialog.
const toast = document.getElementById('toast');
function show(msg, bad) {
  toast.textContent = msg;
  toast.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(show.t);
  show.t = setTimeout(() => toast.className = 'toast', 1500);
}
async function save(el) {
  const row = el.closest('[data-id]');
  const body = {};
  body[el.dataset.field] = el.type === 'checkbox' ? el.checked : el.value;
  // Changing status away from New records today as the last contact date
  if (el.dataset.field === 'lead_status' && el.value !== 'New') {
    body.last_contact = new Date().toISOString().slice(0, 10);
  }
  try {
    const res = await fetch('/api/leads/' + encodeURIComponent(row.dataset.id), {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error();
    if (el.dataset.field === 'lead_status') {
      el.className = 'status s-' + el.value.toLowerCase().replace(/ /g, '-');
    }
    show('Saved');
  } catch (e) {
    show('Could not save', true);
  }
}
const dlg = document.getElementById('proposal');
const text = document.getElementById('proposal-text');
const waLink = document.getElementById('proposal-wa');
const setWa = () => waLink.href = 'https://wa.me/' + waLink.dataset.num + '?text=' + encodeURIComponent(text.value);
text.addEventListener('input', setWa);
document.getElementById('proposal-copy').onclick = () => navigator.clipboard.writeText(text.value).then(() => show('Copied'));
document.querySelectorAll('.write-proposal').forEach(btn => {
  btn.addEventListener('click', async () => {
    const row = btn.closest('[data-id]');
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const res = await fetch('/api/leads/' + encodeURIComponent(row.dataset.id) + '/proposal', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({language: row.querySelector('.proposal-lang').value}),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);
      text.value = data.message;
      waLink.dataset.num = btn.dataset.wa;
      setWa();
      dlg.showModal();
      btn.dataset.reload && dlg.addEventListener('close', () => location.reload(), {once: true});
    } catch (e) {
      show(e.message || 'Could not write proposal', true);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Write';
    }
  });
});
document.querySelectorAll('[data-field]').forEach(el => {
  el.addEventListener('change', () => save(el));
});
