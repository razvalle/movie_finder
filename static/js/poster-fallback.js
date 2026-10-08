const posterTimers = new WeakMap();

function markPosterLoaded(image) {
  const timer = posterTimers.get(image);
  if (timer) window.clearTimeout(timer);
  image.closest(".movie-card__poster")?.classList.add("is-loaded");
}

function usePosterFallback(image) {
  if (image.dataset.posterFallbackApplied) {
    markPosterLoaded(image);
    return;
  }
  if (!image.dataset.posterFallback) {
    markPosterLoaded(image);
    return;
  }
  image.dataset.posterFallbackApplied = "true";
  image.loading = "eager";
  image.src = image.dataset.posterFallback;
}

document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement)) return;
  usePosterFallback(image);
}, true);

document.querySelectorAll(".site-header__logo").forEach((logo) => {
  const showLogo = () => logo.classList.add("is-loaded");
  if (logo.complete) {
    if (logo.naturalWidth > 0) showLogo();
    return;
  }
  logo.addEventListener("load", showLogo, { once: true });
});

function initializePosters() {
  document.querySelectorAll(".movie-card__poster img").forEach((poster) => {
    if (!poster.dataset.posterLoadBound) {
      poster.dataset.posterLoadBound = "true";
      poster.addEventListener("load", () => markPosterLoaded(poster), { once: true });
    }
    if (poster.complete) {
      if (poster.naturalWidth > 0) markPosterLoaded(poster);
      else if (poster.getAttribute("src")) usePosterFallback(poster);
      return;
    }
    const bounds = poster.getBoundingClientRect();
    const nearViewport = bounds.bottom >= 0 && bounds.top <= window.innerHeight + 240;
    if (!nearViewport) return;
    poster.loading = "eager";
    if (!posterTimers.has(poster)) {
      posterTimers.set(poster, window.setTimeout(() => usePosterFallback(poster), 7000));
    }
  });
}

initializePosters();
window.addEventListener("movie-finder:content-updated", initializePosters);
window.addEventListener("scroll", initializePosters, { passive: true });