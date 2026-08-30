// Eigenes Bestätigungs-Popup im App-Look, als Ersatz für den nativen
// window.confirm()/hx-confirm-Dialog des Browsers (passt sonst optisch nicht
// zur restlichen Oberfläche). Drei Wege, es auszulösen:
//   1. hx-confirm="..." auf einem htmx-Element — hier über das htmx:confirm-
//      Event abgefangen (siehe htmx-Doku zu hx-confirm für dieses Muster).
//   2. Direkter Aufruf window.appConfirm(text, {danger}) für Bestätigungen
//      außerhalb von htmx (z. B. Chart löschen), als Promise<boolean>.
//   3. window.appAlert(text, {danger}) als Ersatz für window.alert() — reine
//      Meldung, nur "OK" statt Abbrechen/Bestätigen. Der native alert() zeigte
//      sonst Host und Port des Servers ("Auf 192.168.x.x:8127 wird Folgendes
//      angezeigt") über der eigentlichen Nachricht.
(function () {
  let overlay = null;

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.innerHTML =
      '<div class="confirm-dialog" role="alertdialog" aria-modal="true">' +
        '<p class="confirm-message"></p>' +
        '<div class="confirm-actions">' +
          '<button type="button" class="btn confirm-cancel">Abbrechen</button>' +
          '<button type="button" class="btn confirm-ok">Bestätigen</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    return overlay;
  }

  window.appConfirm = function (message, opts) {
    opts = opts || {};
    if (!overlay) build();
    overlay.querySelector('.confirm-message').textContent = message;
    const okBtn = overlay.querySelector('.confirm-ok');
    const cancelBtn = overlay.querySelector('.confirm-cancel');
    okBtn.textContent = opts.confirmLabel || 'Bestätigen';
    okBtn.className = 'btn confirm-ok ' + (opts.danger ? 'btn-danger' : 'primary');
    overlay.classList.add('open');
    // Fokus auf Abbrechen, nicht auf die Aktion — bei destruktiven Aktionen ein
    // sichererer Default, falls jemand versehentlich Enter/Leertaste drückt.
    cancelBtn.focus();

    return new Promise((resolve) => {
      function close(result) {
        overlay.classList.remove('open');
        document.removeEventListener('keydown', onKeydown);
        okBtn.onclick = null; cancelBtn.onclick = null; overlay.onclick = null;
        resolve(result);
      }
      function onKeydown(e) {
        if (e.key === 'Escape') close(false);
      }
      okBtn.onclick = () => close(true);
      cancelBtn.onclick = () => close(false);
      overlay.onclick = (e) => { if (e.target === overlay) close(false); };
      document.addEventListener('keydown', onKeydown);
    });
  };

  // Meldung ohne Wahlmöglichkeit: derselbe Dialog, nur ohne "Abbrechen" und
  // mit "OK" statt "Bestätigen". Gibt wie appConfirm ein Promise zurück (das
  // immer true liefert), damit sich beides gleich verwenden lässt; die
  // meisten Aufrufer feuern es einfach ab, ohne zu warten.
  window.appAlert = function (message, opts) {
    opts = opts || {};
    if (!overlay) build();
    const cancelBtn = overlay.querySelector('.confirm-cancel');
    cancelBtn.style.display = 'none';
    const promise = window.appConfirm(message, {
      danger: opts.danger, confirmLabel: opts.confirmLabel || 'OK',
    });
    // appConfirm setzt den Fokus auf "Abbrechen" — das ist hier ausgeblendet,
    // der Fokus liefe also ins Leere.
    overlay.querySelector('.confirm-ok').focus();
    // Erst nach dem Schließen zurücksetzen, sonst taucht "Abbrechen" beim
    // nächsten appConfirm() nicht wieder auf.
    return promise.then((result) => { cancelBtn.style.display = ''; return result; });
  };

  document.body.addEventListener('htmx:confirm', function (evt) {
    if (!evt.detail.question) return; // kein hx-confirm auf diesem Element
    evt.preventDefault();
    const elt = evt.detail.elt;
    const danger = !!(elt && elt.classList.contains('btn-danger'));
    const confirmLabel = elt && elt.dataset ? elt.dataset.confirmLabel : undefined;
    window.appConfirm(evt.detail.question, {danger, confirmLabel}).then((ok) => {
      if (ok) evt.detail.issueRequest(true);
    });
  });
})();
