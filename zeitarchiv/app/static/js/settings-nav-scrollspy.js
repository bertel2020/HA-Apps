// Scroll-Spy für Seiten mit seitlicher Sprungmarken-Navigation (.settings-nav,
// siehe Einstellungen UND Housekeeping): alle Abschnitte liegen auf DERSELBEN
// Seite, nav_active wird serverseitig nur einmal beim Laden gesetzt — ohne
// dieses Skript bliebe der erste Eintrag unabhängig vom tatsächlichen
// Scroll-/Anker-Stand dauerhaft hervorgehoben. Generisch (kein fester Bezug
// auf einzelne Abschnittsnamen), damit mehrere Seiten dasselbe Skript nutzen
// können, statt es je Seite zu duplizieren.
(function () {
  const nav = document.querySelector('.settings-nav');
  if (!nav) return;
  const navLinks = Array.from(nav.querySelectorAll('a[href*="#"]'));
  const sections = navLinks
    .map(a => document.getElementById(a.getAttribute('href').split('#')[1]))
    .filter(Boolean);
  if (!sections.length) return;

  // Mobil ist .settings-nav eine horizontal scrollbare Einzeile (siehe
  // app.css) — ohne diesen Abgleich bliebe der Scroll-Streifen an seiner
  // ursprünglichen Position stehen, während sich die Hervorhebung beim
  // Scrollen durch die Seite weiterbewegt, sodass der aktive Punkt
  // irgendwann außerhalb des sichtbaren Streifens verschwindet. Nur bei
  // TATSÄCHLICHEM Wechsel scrollen (currentId-Vergleich), sonst würde
  // jeder scroll-Event (auch ohne Abschnittswechsel) erneut
  // scrollIntoView auslösen und mit dem Scrollen der Seite selbst
  // konkurrieren. Auf Desktop (vertikale Liste, komplett sichtbar) ist
  // der Aufruf ein No-op, da bereits alles im Bild ist.
  let currentId = null;
  function setActive(id) {
    if (id === currentId) return;
    currentId = id;
    let activeLink = null;
    navLinks.forEach(a => {
      const isActive = a.getAttribute('href').split('#')[1] === id;
      a.classList.toggle('active', isActive);
      if (isActive) activeLink = a;
    });
    if (activeLink) activeLink.scrollIntoView({behavior: 'smooth', inline: 'center', block: 'nearest'});
  }

  // "Aktuell" ist der Abschnitt, von dem GERADE DER GRÖSSTE ANTEIL seiner
  // eigenen Höhe im Viewport sichtbar ist (Anteil, nicht Pixelhöhe — sonst
  // gewinnt ein großer Abschnitt allein durch seine Größe). Zwei frühere
  // Varianten scheiterten bei hohen Viewports/kurzen Abschnitten nahe dem
  // Seitenende: ein fester Pixel-Schwellwert ("Überschrift hat Marke X
  // überschritten") setzt voraus, dass darunter noch genug Scroll-Weg
  // übrig ist, um dorthin zu gelangen — reicht der Rest der Seite dafür
  // nicht mehr aus (z. B. "Verbindung" kurz vor "Über Zeitarchiv"), wird
  // der Abschnitt nie aktiv. Der Sichtbarkeits-Anteil braucht dagegen
  // keinen bestimmten Scroll-Weg zu erreichen und funktioniert für jede
  // Seitenlänge/jeden Viewport gleich.
  function updateActiveSection() {
    const viewportHeight = window.innerHeight;
    let current = sections[0];
    let bestRatio = -1;
    for (const section of sections) {
      const rect = section.getBoundingClientRect();
      if (rect.height <= 0) continue;
      const visibleHeight = Math.max(0, Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0));
      const ratio = visibleHeight / rect.height;
      if (ratio > bestRatio) {
        bestRatio = ratio;
        current = section;
      }
    }
    setActive(current.id);
  }

  updateActiveSection();
  window.addEventListener('scroll', updateActiveSection, {passive: true});
})();
