// Zeigt an Reiterzeilen (.entity-tabs), die breiter sind als der Platz, links und/oder rechts einen kleinen Pfeil.
// Ohne ihn sieht man am Handy nicht, dass man die Reiter seitlich wischen kann. Ein Tipp auf den Pfeil scrollt weiter.
// Die Pfeile sind Schaltflächen ohne Reiter-Rolle und ohne Tabstopp (Tastatur: Pfeiltasten der Reiter wie bisher);
// sie stehen in der Zeile selbst, kleben per position:sticky am Rand und beanspruchen keinen Platz (siehe app.css).
(function () {
  'use strict';

  const MARKED = '[aria-selected="true"], [aria-current], .is-active';

  function arrow(side) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'entity-tabs-arrow entity-tabs-arrow-' + side;
    b.tabIndex = -1;
    b.setAttribute('aria-hidden', 'true');
    b.textContent = side === 'left' ? '‹' : '›';
    b.hidden = true;
    return b;
  }

  function update(nav, left, right) {
    const max = nav.scrollWidth - nav.clientWidth;
    left.hidden = !(max > 1 && nav.scrollLeft > 1);
    right.hidden = !(max > 1 && nav.scrollLeft < max - 1);
  }

  function reveal(nav) {
    // Den gewählten Reiter in die Mitte der Zeile holen, ohne die Seite zu bewegen.
    const active = nav.querySelector(MARKED);
    if (!active || nav.scrollWidth <= nav.clientWidth) return;
    nav.scrollLeft = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
  }

  function setup(nav) {
    if (nav.dataset.tabsScroll) return;
    nav.dataset.tabsScroll = '1';
    const left = arrow('left');
    const right = arrow('right');
    nav.insertBefore(left, nav.firstChild);
    nav.appendChild(right);
    const refresh = () => update(nav, left, right);
    left.addEventListener('click', () => nav.scrollBy({left: -nav.clientWidth * 0.7, behavior: 'smooth'}));
    right.addEventListener('click', () => nav.scrollBy({left: nav.clientWidth * 0.7, behavior: 'smooth'}));
    nav.addEventListener('scroll', refresh, {passive: true});
    if (window.ResizeObserver) new ResizeObserver(refresh).observe(nav);
    window.addEventListener('resize', refresh);
    reveal(nav);
    refresh();
    // Reiter, die Alpine erst nach dem Laden einsetzt, verändern die Breite.
    if (window.MutationObserver) {
      new MutationObserver(refresh).observe(nav, {childList: true, subtree: true});
    }
    setTimeout(() => { reveal(nav); refresh(); }, 300);
  }

  function init(root) {
    (root || document).querySelectorAll('.entity-tabs').forEach(setup);
  }

  document.addEventListener('DOMContentLoaded', () => init());
  if (window.MutationObserver) {
    document.addEventListener('DOMContentLoaded', () => {
      new MutationObserver((records) => {
        for (const r of records) {
          r.addedNodes.forEach((n) => {
            if (n.nodeType !== 1) return;
            if (n.matches && n.matches('.entity-tabs')) setup(n);
            else if (n.querySelector) init(n);
          });
        }
      }).observe(document.body, {childList: true, subtree: true});
    });
  }
})();
