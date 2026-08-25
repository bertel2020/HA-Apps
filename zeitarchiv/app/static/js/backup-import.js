(function () {
  'use strict';

  function zoneFromEvent(event) {
    return event.target.closest && event.target.closest('[data-backup-dropzone]');
  }

  function setState(zone, text) {
    const state = zone.closest('form').querySelector('[data-backup-upload-state]');
    if (state) state.textContent = text;
  }

  function submitFile(zone, file) {
    if (!file || !file.name.toLowerCase().endsWith('.zip')) {
      setState(zone, 'Bitte eine ZIP-Datei auswählen.');
      return;
    }
    const input = zone.querySelector('input[type="file"]');
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    setState(zone, file.name + ' wird hochgeladen und geprüft…');
    zone.closest('form').requestSubmit();
  }

  document.addEventListener('change', function (event) {
    const input = event.target.closest && event.target.closest('[data-backup-dropzone] input[type="file"]');
    if (input && input.files.length) submitFile(input.closest('[data-backup-dropzone]'), input.files[0]);
  });

  document.addEventListener('keydown', function (event) {
    const zone = zoneFromEvent(event);
    if (zone && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      zone.querySelector('input[type="file"]').click();
    }
  });

  ['dragenter', 'dragover'].forEach(function (name) {
    document.addEventListener(name, function (event) {
      const zone = zoneFromEvent(event);
      if (!zone) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
      zone.classList.add('dragging');
    });
  });

  document.addEventListener('dragleave', function (event) {
    const zone = zoneFromEvent(event);
    if (!zone || (event.relatedTarget && zone.contains(event.relatedTarget))) return;
    zone.classList.remove('dragging');
  });

  document.addEventListener('drop', function (event) {
    const zone = zoneFromEvent(event);
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove('dragging');
    if (event.dataTransfer.files.length) submitFile(zone, event.dataTransfer.files[0]);
  });
}());
