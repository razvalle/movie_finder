(() => {
  const modal = document.querySelector("#movie-modal");
  const dialog = modal?.querySelector(".movie-modal__dialog");
  const closeButton = modal?.querySelector("[data-modal-close]");
  const cards = [...document.querySelectorAll(".movie-card[data-detail-title]")];
  if (!modal || !dialog || !cards.length) return;

  let returnFocus = null;
  let activeCard = null;
  let closeTimer = 0;

  const cardImage = (card) => card.querySelector(".movie-card__poster img");
  const templateText = (card, selector) => [...(card.querySelector(selector)?.content.querySelectorAll("li, span") || [])]
    .map((element) => element.textContent.trim()).filter(Boolean);
  const topRated = (card) => Number(card.dataset.detailRating) >= 8 && Number(card.dataset.detailVotes) >= 10000;

  // Top Rated means IMDb rating >= 8.0 with at least 10,000 recorded votes.
  function addPreview(card) {
    const source = card.querySelector(".movie-card__preview-template");
    const image = cardImage(card);
    if (!source || !image) return;
    const fragment = source.content.cloneNode(true);
    const preview = fragment.querySelector(".movie-preview");
    const previewImage = preview.querySelector("img");
    previewImage.src = image.currentSrc || image.src;
    previewImage.alt = `${card.dataset.detailTitle} poster`;
    previewImage.dataset.posterFallback = image.dataset.posterFallback || "";
    const meta = preview.querySelector(".movie-preview__meta");
    if (card.dataset.detailCertification) {
      const rating = document.createElement("span");
      rating.className = "movie-preview__rating";
      rating.textContent = card.dataset.detailCertification;
      meta.append(rating);
    }
    if (card.dataset.detailRuntime) meta.append(textElement("span", "", `${card.dataset.detailRuntime} min`));
    if (card.dataset.detailYear) meta.append(textElement("span", "", card.dataset.detailYear));
    preview.querySelector(".movie-preview__tags").replaceChildren(...templateText(card, ".movie-card__preview-tags").map((label) => {
      const tag = document.createElement("span");
      tag.textContent = label;
      return tag;
    }));
    preview.querySelector(".movie-preview__top-rated").hidden = !topRated(card);
    preview.querySelectorAll("[data-preview-feedback]").forEach((button) => {
      button.hidden = !card.querySelector(".movie-card__feedback");
    });
    card.append(preview);
  }

  function textElement(tagName, className, text) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = text;
    return element;
  }

  function iconElement(name, className) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon ${className}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#icon-${name}`);
    svg.append(use);
    return svg;
  }

  function renderCard(card) {
    activeCard = card;
    const title = card.dataset.detailTitle;
    const hero = modal.querySelector(".movie-modal__hero-image");
    const poster = cardImage(card);
    const fallback = poster?.dataset.posterFallback || "";
    hero.alt = `${title} poster`;
    hero.dataset.posterFallback = fallback;
    hero.src = poster?.currentSrc || poster?.src || fallback;
    modal.querySelector("#movie-modal-title").textContent = title;

    const source = modal.querySelector(".movie-modal__source");
    if (card.dataset.detailImdbId) {
      source.href = `https://www.imdb.com/title/${encodeURIComponent(card.dataset.detailImdbId)}/`;
      source.hidden = false;
    } else {
      source.removeAttribute("href");
      source.hidden = true;
    }

    const badges = modal.querySelector(".movie-modal__badges");
    badges.replaceChildren();
    if (card.dataset.detailYear) badges.append(textElement("span", "", card.dataset.detailYear));
    if (card.dataset.detailRuntime) badges.append(textElement("span", "", `${card.dataset.detailRuntime} min`));
    if (card.dataset.detailQuality) badges.append(textElement("span", "movie-modal__quality", card.dataset.detailQuality));
    if (card.dataset.detailCertification) badges.append(textElement("span", "movie-modal__age", card.dataset.detailCertification));
    if (topRated(card)) {
      const badge = textElement("span", "movie-modal__top-rated", "");
      badge.append(iconElement("thumbs-up", "movie-modal__top-rated-icon"), document.createTextNode(" Top Rated"));
      badges.append(badge);
    }

    const descriptors = modal.querySelector(".movie-modal__descriptors");
    descriptors.textContent = card.dataset.detailDescriptors || "";
    descriptors.hidden = !descriptors.textContent;
    if (!descriptors.hidden) badges.append(descriptors);
    badges.hidden = !badges.childElementCount;
    const synopsis = modal.querySelector(".movie-modal__synopsis");
    const synopsisText = card.dataset.detailSynopsis || "";
    const genericSynopsis = synopsisText.startsWith("IMDb rating ") || (synopsisText.includes("), a ") && synopsisText.endsWith(" movie."));
    synopsis.textContent = genericSynopsis ? "" : synopsisText;
    synopsis.hidden = !synopsis.textContent;

    const cast = templateText(card, ".movie-card__detail-cast");
    const castRow = modal.querySelector("[data-modal-cast-row]");
    const castValues = modal.querySelector(".movie-modal__cast-values");
    const moreButton = modal.querySelector("[data-cast-more]");
    castValues.replaceChildren();
    cast.slice(0, 3).forEach((name, index) => {
      if (index) castValues.append(document.createTextNode(", "));
      castValues.append(document.createTextNode(name));
    });
    moreButton.hidden = cast.length <= 3;
    moreButton.setAttribute("aria-expanded", "false");
    moreButton.textContent = "more";
    moreButton.onclick = () => {
      const expanded = moreButton.getAttribute("aria-expanded") === "true";
      castValues.textContent = expanded ? cast.slice(0, 3).join(", ") : cast.join(", ");
      moreButton.setAttribute("aria-expanded", String(!expanded));
      moreButton.textContent = expanded ? "more" : "less";
    };
    castRow.hidden = !cast.length;

    const genres = templateText(card, ".movie-card__genres");
    modal.querySelector(".movie-modal__genre-values").textContent = genres.join(", ");
    modal.querySelector("[data-modal-genres-row]").hidden = !genres.length;
    const moods = templateText(card, ".movie-card__moods");
    modal.querySelector(".movie-modal__mood-values").textContent = moods.join(", ");
    modal.querySelector("[data-modal-moods-row]").hidden = !moods.length;
    renderRelated(card);
    modal.querySelectorAll("[data-modal-feedback]").forEach((button) => {
      button.hidden = !card.querySelector(".movie-card__feedback");
    });
  }

  function openModal(card, focusTarget = card) {
    window.clearTimeout(closeTimer);
    if (modal.hidden) returnFocus = focusTarget;
    renderCard(card);
    modal.hidden = false;
    document.body.classList.add("movie-modal-open");
    requestAnimationFrame(() => modal.classList.add("is-open"));
    closeButton.focus();
  }

  function closeModal() {
    if (modal.hidden) return;
    modal.classList.remove("is-open");
    closeTimer = window.setTimeout(() => {
      modal.hidden = true;
      document.body.classList.remove("movie-modal-open");
      returnFocus?.focus();
    }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180);
  }

  function renderRelated(active) {
    const section = modal.querySelector(".movie-modal__related");
    const list = modal.querySelector(".movie-modal__related-list");
    const documents = cards.map((card) => ({ card, text: card.dataset.similarityText || "" }))
      .filter(({ card, text }) => card !== active && text.trim());
    if (!documents.length || !(active.dataset.similarityText || "").trim()) {
      section.hidden = true;
      list.replaceChildren();
      return;
    }
    const tokenize = (text) => text.toLowerCase().match(/[a-z0-9-]+/g) || [];
    const corpus = [active, ...documents.map(({ card }) => card)].map((card) => tokenize(card.dataset.similarityText || ""));
    const documentFrequency = new Map();
    corpus.forEach((tokens) => new Set(tokens).forEach((token) => documentFrequency.set(token, (documentFrequency.get(token) || 0) + 1)));
    const vectorize = (tokens) => {
      const counts = new Map();
      tokens.forEach((token) => counts.set(token, (counts.get(token) || 0) + 1));
      const vector = new Map();
      if (!tokens.length) return vector;
      counts.forEach((count, token) => vector.set(token, (count / tokens.length) * (Math.log((1 + corpus.length) / (1 + documentFrequency.get(token))) + 1)));
      return vector;
    };
    const vectors = corpus.map(vectorize);
    const cosine = (left, right) => {
      let dot = 0;
      let leftNorm = 0;
      let rightNorm = 0;
      left.forEach((value, token) => { dot += value * (right.get(token) || 0); leftNorm += value * value; });
      right.forEach((value) => { rightNorm += value * value; });
      return leftNorm && rightNorm ? dot / Math.sqrt(leftNorm * rightNorm) : 0;
    };
    const ranked = documents.map(({ card }, index) => ({ card, score: cosine(vectors[0], vectors[index + 1]) }))
      .sort((left, right) => right.score - left.score).slice(0, 5);
    list.replaceChildren();
    ranked.forEach(({ card }) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "movie-modal__related-card";
      button.setAttribute("aria-label", `View details for ${card.dataset.detailTitle}`);
      const image = cardImage(card);
      const poster = document.createElement("img");
      poster.src = image?.currentSrc || image?.src || "";
      poster.alt = `${card.dataset.detailTitle} poster`;
      if (image?.dataset.posterFallback) poster.dataset.posterFallback = image.dataset.posterFallback;
      button.append(poster, textElement("span", "", card.dataset.detailTitle));
      button.addEventListener("click", () => renderCard(card));
      list.append(button);
    });
    section.hidden = !ranked.length;
  }

  cards.forEach((card) => {
    addPreview(card);
    card.onclick = (event) => {
      if (event.target.closest("a, button, form, input, select, textarea, summary")) return;
      openModal(card, card);
    };
    card.onkeydown = (event) => {
      if (event.target !== card || !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      openModal(card, card);
    };
    card.querySelectorAll("[data-preview-open]").forEach((button) => button.addEventListener("click", () => openModal(card, card)));
    card.querySelectorAll("[data-preview-feedback]").forEach((button) => button.addEventListener("click", () => {
      card.querySelector(`.movie-card__feedback button[value="${button.dataset.previewFeedback}"]`)?.click();
    }));
  });

  modal.querySelectorAll("[data-modal-feedback]").forEach((button) => button.addEventListener("click", () => {
    activeCard?.querySelector(`.movie-card__feedback button[value="${button.dataset.modalFeedback}"]`)?.click();
  }));
  closeButton.addEventListener("click", closeModal);
  modal.querySelector("[data-modal-backdrop]").addEventListener("click", closeModal);
  document.addEventListener("keydown", (event) => {
    if (modal.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeModal();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll('a[href]:not([hidden]), button:not([hidden]), [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.disabled && element.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); dialog.focus(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener("movie-finder:open-movie", (event) => {
    const { card, trigger } = event.detail || {};
    if (card) openModal(card, trigger || card);
  });
})();