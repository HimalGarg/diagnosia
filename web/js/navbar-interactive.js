/**
 * Diagnosia - Interactive Cursor Navbar Engine
 * Implements smooth cursor spotlight tracking and subtle magnetic spring pull
 */
(function () {
  function initInteractiveNavbar() {
    const items = document.querySelectorAll(".nav-item-premium");

    items.forEach((item) => {
      let isHovered = false;
      let rafId = null;

      function updateSpotlight(e) {
        const rect = item.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        item.style.setProperty("--mouse-x", `${x}px`);
        item.style.setProperty("--mouse-y", `${y}px`);

        // Calculate magnetic offset normalized [-1, 1] from center
        const centerX = rect.width / 2;
        const centerY = rect.height / 2;
        const tx = Math.max(-1, Math.min(1, (x - centerX) / centerX));
        const ty = Math.max(-1, Math.min(1, (y - centerY) / centerY));

        item.style.setProperty("--tx", tx.toFixed(3));
        item.style.setProperty("--ty", ty.toFixed(3));
      }

      item.addEventListener("mouseenter", (e) => {
        isHovered = true;
        item.classList.add("is-hovered");
        updateSpotlight(e);
      });

      item.addEventListener("mousemove", (e) => {
        if (!isHovered) return;
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => updateSpotlight(e));
      });

      item.addEventListener("mouseleave", () => {
        isHovered = false;
        if (rafId) cancelAnimationFrame(rafId);
        item.classList.remove("is-hovered");
        item.style.setProperty("--tx", "0");
        item.style.setProperty("--ty", "0");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initInteractiveNavbar);
  } else {
    initInteractiveNavbar();
  }
})();
