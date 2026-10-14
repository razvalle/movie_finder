(() => {
  const root = document.documentElement;
  const loadingScreen = document.querySelector("[data-loading-screen]");
  const accentButtons = [...document.querySelectorAll("[data-accent-choice]")];
  const validAccents = new Set(["blue", "teal", "violet"]);
  const storageKey = "movie-finder-accent";

  function applyAccent(accent) {
    const selected = validAccents.has(accent) ? accent : "blue";
    root.dataset.accent = selected;
    accentButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.accentChoice === selected));
    });
  }

  accentButtons.forEach((button) => button.addEventListener("click", () => {
    const selected = button.dataset.accentChoice;
    applyAccent(selected);
    try { localStorage.setItem(storageKey, selected); } catch { /* Keep the current tab's choice. */ }
  }));

  let loadingReleased = false;
  function releaseLoadingScreen() {
    if (!loadingScreen || loadingReleased) return;
    loadingReleased = true;
    loadingScreen.classList.add("is-done");
    window.setTimeout(() => loadingScreen.remove(), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180);
  }

  let storedAccent = "blue";
  try { storedAccent = localStorage.getItem(storageKey) || "blue"; } catch { storedAccent = "blue"; }
  applyAccent(storedAccent);

  if (document.readyState === "loading" || document.readyState === "interactive") {
    document.addEventListener("DOMContentLoaded", releaseLoadingScreen, { once: true });
  } else {
    releaseLoadingScreen();
  }
  window.addEventListener("pageshow", releaseLoadingScreen, { once: true });
  window.setTimeout(releaseLoadingScreen, 1100);
})();