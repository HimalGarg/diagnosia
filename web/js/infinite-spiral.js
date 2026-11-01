/**
 * Diagnosia - Infinite 3D Spiral Component (Vanilla JS)
 * Implements the ReactBits InfiniteSpiral 3D helix physics for anatomical organ visualization
 */

(function () {
  'use strict';

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  const modulo = (value, divisor) => ((value % divisor) + divisor) % divisor;
  const smoothstep = (min, max, value) => {
    const x = clamp((value - min) / (max - min || 1), 0, 1);
    return x * x * (3 - 2 * x);
  };

  const organItems = [
    { src: 'images/organs/brain.jpg', alt: 'Cerebral Cortex & Neural Pathways', label: 'Cerebral Cortex' },
    { src: 'images/organs/heart.jpg', alt: 'Coronary Vascular Anatomy', label: 'Cardiovascular' },
    { src: 'images/organs/lungs.jpg', alt: 'Pulmonary Bronchial Tree', label: 'Pulmonary Tree' },
    { src: 'images/organs/retina.jpg', alt: 'Retinal Microvasculature & Iris', label: 'Retinal Fundus' },
    { src: 'images/organs/dna.jpg', alt: 'DNA Double Helix Molecular Structure', label: 'Genomics' },
    { src: 'images/organs/spine.jpg', alt: 'Cervical & Vertebral Biomechanics', label: 'Vertebral Spine' },
    { src: 'images/organs/kidney.jpg', alt: 'Renal Cortex & Glomerular Network', label: 'Renal Cortex' },
    { src: 'images/organs/skull.jpg', alt: 'Cranial Osteology & Radiograph', label: 'Cranium' },
    { src: 'images/organs/neuron.jpg', alt: 'Synaptic Neuronal Network', label: 'Synapse' },
    { src: 'images/organs/heart.jpg', alt: 'Cardiac Ventricular Myocardium', label: 'Myocardium' },
    { src: 'images/organs/brain.jpg', alt: 'Axonal Synaptic Tractography', label: 'Neural System' },
    { src: 'images/organs/retina.jpg', alt: 'Ophthalmic Microcirculation', label: 'Ophthalmology' }
  ];

  function initInfiniteSpiral() {
    const root = document.getElementById('infiniteSpiralHero');
    if (!root) return;

    // Config defaults
    const speed = 0.52;
    const direction = 'up';
    const animationMode = 'all'; // auto + drag + scroll
    const radius = 150;
    const cardWidth = 110;
    const cardHeight = 110;
    const verticalSpacing = 68;
    const perspective = 1000;
    const cardsPerTurn = 7;
    const rotation = 0;
    const cardTilt = 0;
    const centerScale = 1.24;
    const edgeFade = 0.45;
    const edgeBlur = 8;
    const pauseOnHover = true;

    // Populate stage cards if not already present
    const stage = root.querySelector('.infinite-spiral__stage');
    if (!stage) return;

    stage.innerHTML = '';
    const cardElements = [];

    organItems.forEach((item, index) => {
      const card = document.createElement('div');
      card.className = 'infinite-spiral__item';
      card.setAttribute('role', 'listitem');
      card.setAttribute('aria-label', item.label);
      card.style.width = `${cardWidth}px`;
      card.style.height = `${cardHeight}px`;

      const img = document.createElement('img');
      img.className = 'infinite-spiral__image';
      img.src = item.src;
      img.alt = item.alt;
      img.draggable = false;
      img.loading = index < 6 ? 'eager' : 'lazy';

      const label = document.createElement('div');
      label.className = 'spiral-item-label';
      label.textContent = item.label;

      card.appendChild(img);
      card.appendChild(label);
      stage.appendChild(card);
      cardElements.push(card);
    });

    let frameId;
    let previousTime = performance.now();
    let bounds = root.getBoundingClientRect();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const scrollEnabled = animationMode === 'scroll' || animationMode === 'all';
    const scrollSpeedMultiplier = Math.max(speed, 0) / 0.55;
    let lastScrollY = window.scrollY;

    let progress = 0;
    let targetProgress = 0;
    let autoSpeed = 0;
    let isHovered = false;
    let isVisible = true;
    let isDragging = false;
    let lastPointerY = 0;
    let dragMoved = false;

    // Resize observer
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => {
        bounds = root.getBoundingClientRect();
      });
      ro.observe(root);
    }

    // Intersection observer
    if (window.IntersectionObserver) {
      const io = new IntersectionObserver(
        ([entry]) => {
          isVisible = entry.isIntersecting;
        },
        { threshold: 0.02 }
      );
      io.observe(root);
    }

    // Scroll acceleration
    const handleScroll = () => {
      const nextScrollY = window.scrollY;
      const scrollDelta = nextScrollY - lastScrollY;
      lastScrollY = nextScrollY;
      if (!scrollEnabled || !isVisible || scrollDelta === 0) return;
      targetProgress += clamp(
        (scrollDelta * scrollSpeedMultiplier) / Math.max(verticalSpacing * 2, 1),
        -1.5,
        1.5
      );
    };
    window.addEventListener('scroll', handleScroll, { passive: true });

    // Hover listeners
    root.addEventListener('mouseenter', () => {
      isHovered = true;
    });
    root.addEventListener('mouseleave', () => {
      isHovered = false;
    });

    // Pointer dragging
    const dragEnabled = animationMode === 'drag' || animationMode === 'all';

    root.addEventListener('pointerdown', (e) => {
      if (!dragEnabled || e.button !== 0) return;
      isDragging = true;
      dragMoved = false;
      lastPointerY = e.clientY;
      targetProgress = progress;
      if (root.setPointerCapture) root.setPointerCapture(e.pointerId);
      root.style.cursor = 'grabbing';
    });

    root.addEventListener('pointermove', (e) => {
      if (!isDragging) return;
      const deltaY = e.clientY - lastPointerY;
      lastPointerY = e.clientY;
      if (Math.abs(deltaY) > 0.5) dragMoved = true;
      targetProgress -= deltaY / Math.max(verticalSpacing, 1);
    });

    const stopDragging = (e) => {
      if (!isDragging) return;
      isDragging = false;
      if (root.releasePointerCapture && e.pointerId) {
        try {
          root.releasePointerCapture(e.pointerId);
        } catch (_) {}
      }
      root.style.cursor = dragEnabled ? 'grab' : 'default';
    };

    root.addEventListener('pointerup', stopDragging);
    root.addEventListener('pointercancel', stopDragging);

    // Render loop
    const render = (time) => {
      const delta = Math.min((time - previousTime) / 1000, 0.05);
      previousTime = time;

      const autoEnabled = animationMode === 'auto' || animationMode === 'all';
      const motionPaused = isDragging || (pauseOnHover && isHovered);
      const directionMultiplier = direction === 'down' ? -1 : 1;
      const desiredAutoSpeed =
        autoEnabled && isVisible && !reducedMotion.matches && !motionPaused
          ? speed * directionMultiplier
          : 0;
      const speedBlend = 1 - Math.exp(-delta * 7);
      autoSpeed += (desiredAutoSpeed - autoSpeed) * speedBlend;
      targetProgress += autoSpeed * delta;

      const followBlend = 1 - Math.exp(-delta * (isDragging ? 22 : 11));
      progress += (targetProgress - progress) * followBlend;

      const count = organItems.length;
      const half = count / 2;
      const width = Math.max(bounds.width || 360, 1);
      const height = Math.max(bounds.height || 480, 1);
      const fit = Math.min(1, width / (cardWidth * 2.8), height / (cardHeight * 2.35));
      const responsiveRadius = Math.min(radius, Math.max(72, width * 0.36)) * fit;
      const fadeStart = clamp(1 - edgeFade, 0, 0.98);
      const turnSize = Math.max(cardsPerTurn, 1);

      cardElements.forEach((card, index) => {
        let offset = index - progress;
        offset = modulo(offset + half, count) - half;

        const edge = Math.min(Math.abs(offset) / Math.max(half, 1), 1);
        const opacity = 1 - smoothstep(fadeStart, 1, edge);
        const focus = 1 - Math.min(Math.abs(offset) / Math.max(turnSize * 0.65, 1), 1);
        const scale = (1 + (centerScale - 1) * focus) * fit;
        const angle = offset * (360 / turnSize) + rotation;
        const angleRadians = (angle * Math.PI) / 180;
        const x = Math.sin(angleRadians) * responsiveRadius;
        const z = Math.cos(angleRadians) * responsiveRadius;
        const depthScale = clamp(perspective / Math.max(perspective - z, 1), 0.72, 1.45);
        const visualScale = scale * depthScale;
        const depth = (z / Math.max(responsiveRadius, 1) + 1) / 2;
        const blur = edgeBlur * smoothstep(0.35, 1, edge);

        card.style.transform = `translate(-50%, -50%) translate3d(${x.toFixed(1)}px, ${(offset * verticalSpacing * fit).toFixed(1)}px, 0) rotateZ(${cardTilt}deg) scale(${visualScale.toFixed(3)})`;
        card.style.opacity = opacity.toFixed(3);
        card.style.filter = blur > 0.01 ? `blur(${blur.toFixed(2)}px)` : 'none';
        card.style.zIndex = String(Math.round(depth * 100000) + index);
        card.style.pointerEvents = opacity > 0.25 ? 'auto' : 'none';
      });

      frameId = requestAnimationFrame(render);
    };

    frameId = requestAnimationFrame(render);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initInfiniteSpiral);
  } else {
    initInfiniteSpiral();
  }
})();
