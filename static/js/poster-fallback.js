document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.dataset.posterFallback) return;
  if (image.dataset.posterFallbackApplied) return;
  image.dataset.posterFallbackApplied = "true";
  image.src = image.dataset.posterFallback;
}, true);

document.querySelectorAll(".site-header__logo").forEach((logo) => {
  const showLogo = () => logo.classList.add("is-loaded");
  if (logo.complete) {
    if (logo.naturalWidth > 0) showLogo();
    return;
  }
  logo.addEventListener("load", showLogo, { once: true });
});

document.querySelectorAll(".movie-card__poster img").forEach((poster) => {
  const markLoaded = () => poster.closest(".movie-card__poster")?.classList.add("is-loaded");
  if (poster.complete && poster.naturalWidth > 0) markLoaded();
  else poster.addEventListener("load", markLoaded, { once: true });
});