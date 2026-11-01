/**
 * Diagnosia - Live Interactive Hero Pink Gradient Engine
 * Simulates organic floating gradient mesh physics with dynamic cursor interactivity
 */
(function () {
  function initHeroGradient() {
    const stage = document.getElementById('hero-interactive-stage');
    if (!stage) return;

    const orb1 = document.getElementById('live-orb-1');
    const orb2 = document.getElementById('live-orb-2');
    const orb3 = document.getElementById('live-orb-3');
    const orbCursor = document.getElementById('live-orb-cursor');

    if (!orb1 || !orb2 || !orbCursor) return;

    let stageW = stage.offsetWidth;
    let stageH = stage.offsetHeight;

    function updateDimensions() {
      stageW = stage.offsetWidth;
      stageH = stage.offsetHeight;
    }

    window.addEventListener('resize', updateDimensions, { passive: true });

    // Radii of orbs (half dimensions)
    const isMobile = window.innerWidth <= 768;
    const r1 = isMobile ? 190 : 290;
    const r2 = isMobile ? 170 : 260;
    const r3 = isMobile ? 140 : 220;
    const rCursor = isMobile ? 150 : 230;

    // Mouse tracking state
    const mouse = {
      x: stageW * 0.25,
      y: stageH * 0.35,
      targetX: stageW * 0.25,
      targetY: stageH * 0.35,
      prevX: stageW * 0.25,
      prevY: stageH * 0.35,
      vx: 0,
      vy: 0,
      active: false,
      opacity: 0,
      targetOpacity: 0.2,
      pulseScale: 1.0
    };

    // Current positions for smooth interpolation (Lerp)
    const state = {
      orb1: { x: stageW * 0.20, y: stageH * 0.25 },
      orb2: { x: stageW * 0.80, y: stageH * 0.75 },
      orb3: { x: stageW * 0.50, y: stageH * 0.50 }
    };

    // Track mouse interaction on window & stage
    function onPointerMove(clientX, clientY) {
      const rect = stage.getBoundingClientRect();
      const inX = clientX >= rect.left - 40 && clientX <= rect.right + 40;
      const inY = clientY >= rect.top - 40 && clientY <= rect.bottom + 40;

      if (inX && inY) {
        mouse.targetX = Math.max(0, Math.min(stageW, clientX - rect.left));
        mouse.targetY = Math.max(0, Math.min(stageH, clientY - rect.top));
        mouse.active = true;
        mouse.targetOpacity = 0.95;
      } else if (mouse.active) {
        mouse.active = false;
        mouse.targetOpacity = 0.15;
      }
    }

    window.addEventListener('mousemove', (e) => {
      onPointerMove(e.clientX, e.clientY);
    }, { passive: true });

    // Touch support for tablets and mobile
    window.addEventListener('touchmove', (e) => {
      if (!e.touches.length) return;
      onPointerMove(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });

    window.addEventListener('touchstart', (e) => {
      if (!e.touches.length) return;
      onPointerMove(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });

    window.addEventListener('touchend', () => {
      mouse.active = false;
      mouse.targetOpacity = 0.15;
    }, { passive: true });

    // Click wave pulse
    stage.addEventListener('pointerdown', () => {
      mouse.pulseScale = 1.35;
    });

    let startTime = null;

    function render(timestamp) {
      if (!startTime) startTime = timestamp;
      const elapsed = (timestamp - startTime) * 0.001; // seconds

      // 1. Cursor lerp physics
      mouse.prevX = mouse.x;
      mouse.prevY = mouse.y;
      mouse.x += (mouse.targetX - mouse.x) * 0.08;
      mouse.y += (mouse.targetY - mouse.y) * 0.08;

      mouse.vx = mouse.x - mouse.prevX;
      mouse.vy = mouse.y - mouse.prevY;
      const cursorSpeed = Math.sqrt(mouse.vx * mouse.vx + mouse.vy * mouse.vy);

      // Smooth decay for click pulse
      mouse.pulseScale += (1.0 - mouse.pulseScale) * 0.08;
      const velocityScale = Math.min(1.25, 1.0 + cursorSpeed * 0.012);
      const finalCursorScale = velocityScale * mouse.pulseScale;

      // Cursor opacity lerp
      mouse.opacity += (mouse.targetOpacity - mouse.opacity) * 0.06;

      // Update cursor orb
      orbCursor.style.transform = `translate3d(${(mouse.x - rCursor).toFixed(1)}px, ${(mouse.y - rCursor).toFixed(1)}px, 0) scale(${finalCursorScale.toFixed(3)})`;
      orbCursor.style.opacity = mouse.opacity.toFixed(3);

      // 2. Ambient wandering paths (Multi-frequency smooth harmonic drift)
      // Orb 1: Primary Pink/Magenta/Coral Aura (roams across left, center, upper-mid)
      const base1X = stageW * (0.32 + 0.25 * Math.sin(elapsed * 0.42) + 0.12 * Math.cos(elapsed * 0.22 + 1.2));
      const base1Y = stageH * (0.32 + 0.20 * Math.cos(elapsed * 0.38) + 0.10 * Math.sin(elapsed * 0.18 + 2.4));
      const scale1 = 1.0 + 0.12 * Math.sin(elapsed * 0.55);
      const rot1 = (elapsed * 8) % 360;

      // Orb 2: Secondary Coral/Rose/Violet Aura (counter-orbits lower-right & mid)
      const base2X = stageW * (0.68 - 0.25 * Math.cos(elapsed * 0.35) - 0.12 * Math.sin(elapsed * 0.19 + 0.9));
      const base2Y = stageH * (0.65 - 0.22 * Math.sin(elapsed * 0.40) - 0.10 * Math.cos(elapsed * 0.21 + 1.6));
      const scale2 = 1.05 + 0.14 * Math.cos(elapsed * 0.48);
      const rot2 = (-elapsed * 7) % 360;

      // Orb 3: Accent Glow (gentle central interplay)
      const base3X = stageW * (0.50 + 0.18 * Math.sin(elapsed * 0.3 + 2.1));
      const base3Y = stageH * (0.48 + 0.16 * Math.cos(elapsed * 0.34 + 1.5));
      const scale3 = 0.95 + 0.15 * Math.sin(elapsed * 0.65);

      // 3. Magnetic pull calculation (attracts ambient orbs gently towards cursor)
      const pull1 = mouse.active ? 0.24 : 0.06;
      const pull2 = mouse.active ? 0.20 : 0.05;
      const pull3 = mouse.active ? 0.15 : 0.04;

      const target1X = base1X + (mouse.x - base1X) * pull1;
      const target1Y = base1Y + (mouse.y - base1Y) * pull1;

      const target2X = base2X + (mouse.x - base2X) * pull2;
      const target2Y = base2Y + (mouse.y - base2Y) * pull2;

      const target3X = base3X + (mouse.x - base3X) * pull3;
      const target3Y = base3Y + (mouse.y - base3Y) * pull3;

      // Smooth damped lerp to target
      state.orb1.x += (target1X - state.orb1.x) * 0.05;
      state.orb1.y += (target1Y - state.orb1.y) * 0.05;

      state.orb2.x += (target2X - state.orb2.x) * 0.05;
      state.orb2.y += (target2Y - state.orb2.y) * 0.05;

      if (orb3) {
        state.orb3.x += (target3X - state.orb3.x) * 0.045;
        state.orb3.y += (target3Y - state.orb3.y) * 0.045;
        orb3.style.transform = `translate3d(${(state.orb3.x - r3).toFixed(1)}px, ${(state.orb3.y - r3).toFixed(1)}px, 0) scale(${scale3.toFixed(3)})`;
      }

      orb1.style.transform = `translate3d(${(state.orb1.x - r1).toFixed(1)}px, ${(state.orb1.y - r1).toFixed(1)}px, 0) scale(${scale1.toFixed(3)}) rotate(${rot1.toFixed(1)}deg)`;
      orb2.style.transform = `translate3d(${(state.orb2.x - r2).toFixed(1)}px, ${(state.orb2.y - r2).toFixed(1)}px, 0) scale(${scale2.toFixed(3)}) rotate(${rot2.toFixed(1)}deg)`;

      requestAnimationFrame(render);
    }

    requestAnimationFrame(render);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHeroGradient);
  } else {
    initHeroGradient();
  }
})();
