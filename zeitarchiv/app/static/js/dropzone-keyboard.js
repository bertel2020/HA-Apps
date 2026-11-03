// Tastaturbedienung der Datei-Dropzones.
//
// Eine Dropzone ist ein <label role="button" tabindex="0"> um ein verstecktes Dateifeld. Ein Label reagiert nur auf
// einen Mausklick, nicht auf Enter oder Leertaste: Wer nur die Tastatur nutzt, konnte weder ein Symcon-ZIP noch eine
// CSV noch ein Backup hochladen. Ein Handler am Dokument für alle diese Labels (sie werden von htmx ausgetauscht,
// feste Listener wären danach weg).
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const zone = event.target;
  if (!zone || zone.tagName !== 'LABEL' || zone.getAttribute('role') !== 'button') return;
  const input = zone.querySelector('input[type="file"]')
    || (zone.htmlFor ? document.getElementById(zone.htmlFor) : null);
  if (!input) return;
  event.preventDefault();
  input.click();
});
