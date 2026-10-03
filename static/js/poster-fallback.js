document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.dataset.posterFallback) return;
  if (image.dataset.posterFallbackApplied) return;
  image.dataset.posterFallbackApplied = "true";
  image.src = image.dataset.posterFallback;
}, true);