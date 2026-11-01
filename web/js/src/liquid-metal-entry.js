import { liquidMetalFragmentShader, ShaderMount } from "@paper-design/shaders";

export function initLiquidMetalButtons() {
  const planes = document.querySelectorAll(".liquid-metal-plane");

  planes.forEach((plane) => {
    const holder = plane.querySelector(".shader-container-exploded");
    const surface = plane.querySelector(".lm-click-surface");
    if (!holder || !surface) return;

    // Clear any previous canvas
    holder.innerHTML = "";

    try {
      const shaderMount = new ShaderMount(
        holder,
        liquidMetalFragmentShader,
        {
          u_repetition: 4,
          u_softness: 0.5,
          u_shiftRed: 0.3,
          u_shiftBlue: 0.3,
          u_distortion: 0,
          u_contour: 0,
          u_angle: 45,
          u_scale: 8,
          u_shape: 1,
          u_offsetX: 0.1,
          u_offsetY: -0.1,
        },
        undefined,
        0.6
      );

      let isHovered = false;

      surface.addEventListener("mouseenter", () => {
        isHovered = true;
        shaderMount.setSpeed?.(1.0);
      });

      surface.addEventListener("mouseleave", () => {
        isHovered = false;
        plane.classList.remove("is-pressed");
        shaderMount.setSpeed?.(0.6);
      });

      surface.addEventListener("mousedown", () => {
        plane.classList.add("is-pressed");
      });

      window.addEventListener("mouseup", () => {
        plane.classList.remove("is-pressed");
      });

      surface.addEventListener("click", (e) => {
        shaderMount.setSpeed?.(2.4);
        setTimeout(() => {
          if (isHovered) {
            shaderMount.setSpeed?.(1.0);
          } else {
            shaderMount.setSpeed?.(0.6);
          }
        }, 300);

        // Ripple animation
        const rect = surface.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const ripple = document.createElement("span");
        ripple.className = "lm-ripple";
        ripple.style.left = `${x}px`;
        ripple.style.top = `${y}px`;
        surface.appendChild(ripple);

        setTimeout(() => {
          ripple.remove();
        }, 600);
      });
    } catch (err) {
      console.warn("ShaderMount initialization fallback:", err);
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initLiquidMetalButtons);
} else {
  initLiquidMetalButtons();
}
