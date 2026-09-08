// Hält .menu-popover (Optionen-Menü auf der Entitäts-Verlaufsseite und im
// Chart-Editor) waagerecht im Fenster.
//
// Das Problem, gemessen auf einem 375px-Telefon: das Menü ist fest 290px breit
// und mit left:0 an seinem Knopf verankert. Der Knopf steht am rechten Ende
// der Werkzeugleiste (links 257) — das Menü lief also von 257 bis 547 und ragte
// 172px aus dem Fenster. Da <html> overflow-x:hidden trägt, ließ sich das nicht
// wegscrollen: die Schalter jeder Menüzeile sitzen rechtsbündig (bei x≈493) und
// waren damit unerreichbar. Man sah die Beschriftungen und konnte nichts davon
// bedienen. Im Chart-Editor derselbe Fehler, nur kleiner (143 + 290 = 433, also
// 58px daneben).
//
// Warum nicht in CSS: Rechtsöffnung per Media Query (left:auto;right:0, wie sie
// app.css ab 641px schon setzt) verschiebt das Problem nur an den anderen Rand
// — im Chart-Editor säße die linke Kante dann bei −97px. Ein fest breites
// Popover an einem beweglichen Anker ist in CSS nicht zu klemmen, weil die
// Ankerposition dort nicht bekannt ist. Deshalb dasselbe Vorgehen wie beim
// Kalender-Popover (reposition() in calendar-picker.js), das aus genau diesem
// Grund schon so arbeitet.
(function () {
  'use strict';

  // Seitenrand der App — dieselben 16px, die der Seiteninhalt links und rechts
  // frei lässt. Das Menü soll bündig mit ihm abschließen, nicht am Fensterrand
  // kleben.
  const RAND = 16;

  function klemmen(pop) {
    // Erst die eigene Verschiebung zurücknehmen, dann messen. Das ist der
    // einzige verlässliche Weg an die Grundposition: .menu-popover ist je nach
    // Fensterbreite mal links (left:0), mal rechts (left:auto;right:0, ab 641px
    // in .toolbar-right) verankert, und ein Inline-left sticht beides. Eine
    // frühere Fassung rechnete die eigene Verschiebung stattdessen heraus
    // (box.left - schub) — das stimmt nur, solange die Verankerung dieselbe
    // bleibt, und ging beim Verbreitern des Fensters über 641px schief: das
    // Menü blieb am Inline-left hängen, statt wieder bündig zum Knopf zu
    // stehen (gemessen: rechte Kante 1264 statt 1200).
    pop.style.left = '';
    // Unsichtbar ist nichts zu messen — offsetWidth wäre 0 und die Rechnung
    // ergäbe eine Verschiebung um die halbe Fensterbreite. Zurückgesetzt ist
    // die Verschiebung jetzt trotzdem, damit beim nächsten Öffnen frisch
    // gerechnet wird.
    if (pop.offsetParent === null) return;

    const box = pop.getBoundingClientRect();
    // documentElement.clientWidth, nicht innerWidth: geklemmt wird gegen genau
    // die Box, an der <html>s overflow-x:hidden abschneidet. innerWidth zählt
    // eine sichtbare Bildlaufleiste mit und liegt damit je nach Plattform ein
    // paar Pixel daneben.
    const ueberstand = box.right - (document.documentElement.clientWidth - RAND);
    if (ueberstand <= 0) return;
    // Nur so weit zurückschieben, wie links Platz ist: sonst tauschte man den
    // abgeschnittenen rechten Rand gegen einen abgeschnittenen linken.
    const schub = Math.min(ueberstand, Math.max(0, box.left - RAND));
    // left und nicht transform: die Öffnen-Animation (.menu-pop-enter-*)
    // gehört transform bereits, ein Inline-Wert würde sie ersatzlos schlucken.
    pop.style.left = -schub + 'px';
  }

  function alleKlemmen() {
    document.querySelectorAll('.menu-popover').forEach(klemmen);
  }

  // Ausgelöst wird über den Klick auf den Auslöser-Knopf, nicht über einen
  // MutationObserver auf dem Popover: dessen Rückruf feuerte auf die eigenen
  // style-Schreibvorgänge mit und musste gegen die daraus entstehende
  // Endlosschleife abgesichert werden. Alpine setzt display beim Öffnen
  // synchron (x-show), die Übergangsklassen danach — deshalb einmal sofort
  // messen und einmal nach dem Übergang (.menu-pop-enter dauert 120ms).
  document.addEventListener('click', (e) => {
    if (!e.target.closest || !e.target.closest('.menu-btn')) return;
    setTimeout(alleKlemmen, 0);
    setTimeout(alleKlemmen, 160);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', alleKlemmen);
  else alleKlemmen();
  window.addEventListener('resize', alleKlemmen);
})();
