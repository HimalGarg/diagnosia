/**
 * Diagnosia - Fullscreen Edge-to-Edge ECG Line Loader
 * Zero text, clinical P-Q-R-S-T heartbeat wave animation spanning 0 to 100vw
 */
(function () {
  function initEcgFullscreenLoader() {
    const loader = document.getElementById('loader');
    if (!loader) return;

    const canvas = document.getElementById('ecg-loader-canvas');
    let ctx = null;
    let animId = null;
    let width = window.innerWidth;
    const height = 180;
    const centerY = height / 2;

    if (canvas && canvas.getContext) {
      ctx = canvas.getContext('2d');
    }

    function resize() {
      if (!canvas || !ctx) return;
      width = window.innerWidth;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
    }

    if (canvas && ctx) {
      resize();
      window.addEventListener('resize', resize, { passive: true });
    }

    // Mathematical formula for authentic clinical ECG P-Q-R-S-T complex
    // dx = x - pulseCenter (in pixels)
    function getEcgY(dx) {
      if (dx < -130 || dx > 130) return 0;
      const u = dx / 130;
      // P wave (atrial depolarization): gentle smooth bump
      const p = 14 * Math.exp(-Math.pow((u + 0.65) / 0.12, 2));
      // Q wave (septal depolarization): sharp quick downward dip
      const q = -20 * Math.exp(-Math.pow((u + 0.32) / 0.065, 2));
      // R wave (ventricular depolarization): towering sharp electrical peak!
      const r = 85 * Math.exp(-Math.pow((u + 0.12) / 0.042, 2));
      // S wave (late ventricular depolarization): sharp rebound dip
      const s = -32 * Math.exp(-Math.pow((u - 0.08) / 0.065, 2));
      // T wave (ventricular repolarization): smooth recovery dome
      const t = 24 * Math.exp(-Math.pow((u - 0.52) / 0.18, 2));
      return p + q + r + s + t;
    }

    let startTime = null;
    let dismissed = false;

    // Minimum display duration to enjoy the butter-smooth heartbeat
    const minDisplayMs = 1200;
    const loadStartTime = performance.now();
    let isPageLoaded = document.readyState === 'complete';

    function render(timestamp) {
      if (dismissed) return;
      if (!startTime) startTime = timestamp;
      const elapsed = (timestamp - startTime) * 0.001; // seconds

      if (ctx) {
        ctx.clearRect(0, 0, width, height);

        // 1. Draw subtle baseline track line spanning 0 to 100vw
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.09)';
        ctx.lineWidth = 1.5;
        ctx.moveTo(0, centerY);
        ctx.lineTo(width, centerY);
        ctx.stroke();

        // 2. Pulse wave traveling from left edge (0) to right edge (width)
        // Duration of one full traversal: 1.4 seconds
        const traversalPeriod = 1.4;
        const totalDistance = width + 320;
        const speed = totalDistance / traversalPeriod; // px per second

        // Main pulse traveling across
        const pulsePos = ((elapsed * speed) % totalDistance) - 160;

        // Draw active ECG waveform across full screen
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(0, centerY);

        const step = 2; // high-resolution step for crisp peaks
        for (let x = 0; x <= width; x += step) {
          const dy = getEcgY(x - pulsePos);
          ctx.lineTo(x, centerY - dy);
        }
        ctx.lineTo(width, centerY);

        // Outer high-energy red neon glow
        ctx.shadowColor = '#ff1244';
        ctx.shadowBlur = 18;
        ctx.strokeStyle = '#ff1244';
        ctx.lineWidth = 3.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Inner bright white electrical spark core
        ctx.shadowBlur = 4;
        ctx.shadowColor = '#ffffff';
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.restore();

        // 3. Leading cardiac spark blip at the wave front
        if (pulsePos >= 0 && pulsePos <= width) {
          const sparkY = centerY - getEcgY(0);
          ctx.save();
          // Halo glow
          const grad = ctx.createRadialGradient(pulsePos, sparkY, 0, pulsePos, sparkY, 18);
          grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
          grad.addColorStop(0.3, 'rgba(255, 18, 68, 0.9)');
          grad.addColorStop(0.7, 'rgba(255, 18, 68, 0.35)');
          grad.addColorStop(1, 'rgba(255, 18, 68, 0)');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(pulsePos, sparkY, 18, 0, Math.PI * 2);
          ctx.fill();

          // Core point
          ctx.fillStyle = '#ffffff';
          ctx.beginPath();
          ctx.arc(pulsePos, sparkY, 2.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      }

      animId = requestAnimationFrame(render);
    }

    animId = requestAnimationFrame(render);

    function dismissLoader() {
      if (dismissed) return;
      const now = performance.now();
      const elapsedSinceStart = now - loadStartTime;

      if (elapsedSinceStart < minDisplayMs) {
        setTimeout(dismissLoader, minDisplayMs - elapsedSinceStart);
        return;
      }

      dismissed = true;
      loader.classList.add('ecg-loaded');

      setTimeout(function () {
        if (animId) cancelAnimationFrame(animId);
        loader.style.display = 'none';
        if (loader.parentNode) {
          loader.parentNode.removeChild(loader);
        }
      }, 550);
    }

    if (isPageLoaded) {
      setTimeout(dismissLoader, minDisplayMs);
    } else {
      window.addEventListener('load', function () {
        isPageLoaded = true;
        dismissLoader();
      }, { once: true });
      // Safety fail-safe timeout (max 2.8s)
      setTimeout(dismissLoader, 2800);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initEcgFullscreenLoader);
  } else {
    initEcgFullscreenLoader();
  }
})();
