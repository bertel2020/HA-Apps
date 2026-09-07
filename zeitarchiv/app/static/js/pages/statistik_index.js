  // Bestätigung im App-Look statt des nativen confirm()-Dialogs (dieselbe
  // appConfirm()-Mechanik wie beim Löschen von Charts/Tabellen). form.submit()
  // löst absichtlich KEIN weiteres submit-Event aus, deshalb genügt das
  // einmalige preventDefault() ohne zusätzliche Schleifen-Absicherung.
  // Danach übernimmt der Button die Rückmeldung: das VACUUM läuft synchron,
  // die Seite steht bis zur Antwort — ohne sichtbaren Zustandswechsel wirkt
  // der Klick sonst folgenlos.
  const optimizeForm = document.getElementById('index-optimize-form');
  optimizeForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const ok = await appConfirm(
      'Index jetzt optimieren? Die Indexdatei wird kompakt neu geschrieben, '
      + 'Schreibzugriffe werden währenddessen kurz pausiert.',
      {confirmLabel: 'Optimieren'}
    );
    if (!ok) return;
    const button = optimizeForm.querySelector('button[type=submit]');
    if (button) { button.disabled = true; button.textContent = 'Index wird optimiert …'; }
    optimizeForm.submit();
  });
