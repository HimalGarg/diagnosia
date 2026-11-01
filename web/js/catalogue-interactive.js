/**
 * Diagnosia - Interactive Medical Suite Catalogue
 * Handles category filtering with staggered reveal
 */

(function () {
  'use strict';

  function initCatalogueInteractivity() {
    const filterButtons = document.querySelectorAll('.filter-tab-btn');
    const cards = document.querySelectorAll('.catalogue-card');

    if (filterButtons.length && cards.length) {
      filterButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
          filterButtons.forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');

          const filter = btn.getAttribute('data-filter') || 'all';

          cards.forEach((card, index) => {
            const stage = card.getAttribute('data-stage');
            if (filter === 'all' || stage === filter) {
              card.classList.remove('card-filtered-out');
              card.style.opacity = '0';
              card.style.transform = 'translateY(10px)';
              setTimeout(() => {
                card.style.transition = 'opacity 260ms ease, transform 260ms ease';
                card.style.opacity = '1';
                card.style.transform = '';
              }, index * 20);
            } else {
              card.classList.add('card-filtered-out');
            }
          });
        });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCatalogueInteractivity);
  } else {
    initCatalogueInteractivity();
  }
})();
