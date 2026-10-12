// Karte „Externes Ziel“: Auswahlliste der Netzwerkspeicher ein-/ausklappen.
// Eingeklappt bleibt nur die gewählte Freigabe sichtbar (Regeln in backup.css).
function toggleExtMounts(btn) {
  const box = btn.closest('.ext-mounts');
  const collapsed = box.classList.toggle('collapsed');
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}

function collapseExtMounts(input) {
  const box = input.closest('.ext-mounts');
  box.classList.add('collapsed');
  const btn = box.querySelector('.ext-mounts-toggle');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}
