(() => {
  const cards = [...document.querySelectorAll(".movie-card[data-detail-title]")];
  const pageCards = new Set(cards);
  const searchInput = document.querySelector("#q");
  const searchForm = document.querySelector(".search__form");
  const browseRows = document.querySelector("[data-catalog-shelves]");
  const surpriseButton = document.querySelector("[data-surprise-movie]");
  const copyButton = document.querySelector("[data-copy-search-link]");
  const copyStatus = document.querySelector("[data-copy-link-status]");
  const compareStatus = document.querySelector("[data-compare-status]");
  const compareModal = document.querySelector("#compare-modal");
  const compareDialog = compareModal?.querySelector(".compare-modal__dialog");
  const compareClose = compareModal?.querySelector("[data-compare-close]");
  const comparisonContent = compareModal?.querySelector("[data-comparison-content]");
  const selectedCards = [];
  let compareReturnFocus = null;
  let copyStatusTimer = 0;

  const cardImage = (card) => card.querySelector(".movie-card__poster img");
  const templateValues = (card, selector) => [...(card.querySelector(selector)?.content.querySelectorAll("span") || [])]
    .map((element) => element.textContent.trim()).filter(Boolean);
  const rating = (card) => Number(card.dataset.detailRating) || 0;
  const runtime = (card) => Number(card.dataset.detailRuntime) || 0;
  const year = (card) => Number(card.dataset.detailYear) || 0;

  function openMovie(card) {
    card.click();
  }

  function createShelf(title, matches, sort) {
    if (!matches.length) return null;
    const section = document.createElement("section");
    section.className = "browse-row";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const controls = document.createElement("div");
    controls.className = "browse-row__controls";
    const track = document.createElement("div");
    track.className = "browse-row__track";
    track.tabIndex = 0;
    track.setAttribute("role", "region");
    track.setAttribute("aria-label", title);
    [-1, 1].forEach((direction) => {
      const arrow = document.createElement("button");
      arrow.type = "button";
      arrow.className = "browse-row__arrow";
      arrow.setAttribute("aria-label", `Scroll ${title} ${direction < 0 ? "left" : "right"}`);
      arrow.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#icon-chevron-${direction < 0 ? "left" : "right"}"></use></svg>`;
      arrow.addEventListener("click", () => track.scrollBy({
        left: direction * Math.max(240, track.clientWidth * 0.8),
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      }));
      controls.append(arrow);
    });
    const sorted = sort(matches).slice(0, 8);
    sorted.forEach((card) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "browse-row__movie";
      item.setAttribute("aria-label", `View details for ${card.dataset.detailTitle}`);
      const image = cardImage(card);
      if (image) {
        const poster = document.createElement("img");
        poster.src = image.currentSrc || image.src;
        poster.alt = `${card.dataset.detailTitle} poster`;
        poster.loading = "lazy";
        poster.dataset.posterFallback = image.dataset.posterFallback || "";
        item.append(poster);
      }
      const titleText = document.createElement("span");
      titleText.textContent = card.dataset.detailTitle;
      item.append(titleText);
      item.addEventListener("click", () => {
        if (pageCards.has(card)) openMovie(card);
        else document.dispatchEvent(new CustomEvent("movie-finder:open-movie", { detail: { card, trigger: item } }));
      });
      track.append(item);
    });
    section.append(heading, track, controls);
    return section;
  }

  async function renderBrowseRows() {
    if (!browseRows || !cards.length) return;
    let browseCards = cards;
    try {
      const catalogUrl = new URL(window.location.href);
      catalogUrl.searchParams.set("per_page", "48");
      catalogUrl.searchParams.set("page", "1");
      const response = await fetch(catalogUrl, { headers: { "X-Requested-With": "fetch" } });
      if (response.ok) {
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        const loadedCards = [...page.querySelectorAll(".movie-card--browse[data-detail-title]")];
        if (loadedCards.length) browseCards = loadedCards;
      }
    } catch {
      browseCards = cards;
      window.dispatchEvent(new CustomEvent("movie-finder:data-unreachable"));
    }
    // A 7.0 IMDb rating defines “Top rated” in these local browse shelves.
    const shelves = [
      createShelf("Top rated", browseCards.filter((card) => rating(card) >= 7), (items) => items.sort((a, b) => rating(b) - rating(a))),
      createShelf("Under 100 minutes", browseCards.filter((card) => runtime(card) > 0 && runtime(card) < 100), (items) => items.sort((a, b) => runtime(a) - runtime(b))),
      createShelf("90s classics", browseCards.filter((card) => year(card) >= 1990 && year(card) <= 1999), (items) => items.sort((a, b) => rating(b) - rating(a))),
    ].filter(Boolean);
    browseRows.replaceChildren(...shelves);
    browseRows.hidden = !shelves.length;
  }

  function submitMoodSearch(event) {
    const tile = event.target.closest("[data-mood-query]");
    if (!tile || !searchInput || !searchForm) return;
    searchInput.value = tile.dataset.moodQuery;
    searchForm.requestSubmit(searchForm.querySelector(".search__button"));
  }

  function surpriseMe() {
    if (!surpriseButton) return;
    // Only movies rated 7.0+ qualify; the current rendered cards already honor active filters.
    const candidates = cards.filter((card) => rating(card) >= 7);
    if (!candidates.length) return;
    openMovie(candidates[Math.floor(Math.random() * candidates.length)]);
  }

  function setCompareState(card, selected) {
    const button = card.querySelector("[data-compare-select]");
    if (!button) return;
    button.setAttribute("aria-pressed", String(selected));
    button.textContent = selected ? "Selected" : "Compare";
  }

  function addCompareValue(parent, label, value) {
    if (!value) return;
    const row = document.createElement("p");
    row.className = "compare-modal__fact";
    const term = document.createElement("strong");
    term.textContent = `${label}:`;
    const description = document.createElement("span");
    description.textContent = value;
    row.append(term, description);
    parent.append(row);
  }

  function compareColumn(card) {
    const column = document.createElement("section");
    column.className = "compare-modal__movie";
    const title = document.createElement("h3");
    title.textContent = card.dataset.detailTitle;
    column.append(title);
    const genres = templateValues(card, ".movie-card__genres");
    addCompareValue(column, "Runtime", runtime(card) ? `${runtime(card)} min` : "");
    addCompareValue(column, "Genres", genres.join(", "));
    addCompareValue(column, "IMDb rating", card.dataset.detailRating ? `${Number(card.dataset.detailRating).toFixed(1)}/10` : "");
    return column;
  }

  function renderComparison() {
    const [left, right] = selectedCards;
    comparisonContent.replaceChildren(compareColumn(left), compareColumn(right));
    const leftTags = new Set([...templateValues(left, ".movie-card__themes"), ...templateValues(left, ".movie-card__moods")]);
    const rightTags = new Set([...templateValues(right, ".movie-card__themes"), ...templateValues(right, ".movie-card__moods")]);
    const shared = [...leftTags].filter((tag) => rightTags.has(tag));
    if (shared.length) addCompareValue(comparisonContent, "Shared themes or moods", shared.join(", "));
    compareReturnFocus = selectedCards[1].querySelector("[data-compare-select]");
    compareModal.hidden = false;
    document.body.classList.add("compare-modal-open");
    compareClose.focus();
    compareStatus.textContent = `Comparing ${left.dataset.detailTitle} and ${right.dataset.detailTitle}`;
  }

  function clearComparison() {
    selectedCards.splice(0).forEach((card) => setCompareState(card, false));
    if (compareStatus) compareStatus.textContent = "";
  }

  function closeComparison(restoreFocus = true) {
    if (!compareModal || compareModal.hidden) return;
    compareModal.hidden = true;
    document.body.classList.remove("compare-modal-open");
    clearComparison();
    if (restoreFocus) compareReturnFocus?.focus();
  }

  function toggleCompare(button) {
    const card = button.closest(".movie-card");
    const index = selectedCards.indexOf(card);
    if (index >= 0) {
      selectedCards.splice(index, 1);
      setCompareState(card, false);
    } else {
      if (selectedCards.length === 2) {
        setCompareState(selectedCards.shift(), false);
      }
      selectedCards.push(card);
      setCompareState(card, true);
    }
    if (selectedCards.length === 2) {
      renderComparison();
    } else if (compareStatus) {
      compareStatus.textContent = selectedCards.length ? "1 of 2 movies selected" : "";
    }
  }

  async function copySearchLink() {
    if (!copyButton || !copyStatus) return;
    const link = window.location.href;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(link);
      } else {
        const input = document.createElement("textarea");
        input.value = link;
        input.setAttribute("readonly", "");
        input.className = "sr-only";
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      copyStatus.textContent = "Link copied";
      window.clearTimeout(copyStatusTimer);
      copyStatusTimer = window.setTimeout(() => { copyStatus.textContent = ""; }, 2200);
    } catch {
      copyStatus.textContent = "Unable to copy link";
    }
  }

  document.querySelector(".mood-picks")?.addEventListener("click", submitMoodSearch);
  surpriseButton?.addEventListener("click", surpriseMe);
  copyButton?.addEventListener("click", copySearchLink);
  cards.forEach((card) => {
    const button = card.querySelector("[data-compare-select]");
    button?.addEventListener("click", () => toggleCompare(button));
  });
  compareClose?.addEventListener("click", () => closeComparison());
  compareModal?.querySelector("[data-compare-backdrop]")?.addEventListener("click", () => closeComparison());
  compareDialog?.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      compareClose.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && compareModal && !compareModal.hidden) {
      event.preventDefault();
      closeComparison();
    }
  });

  renderBrowseRows();
  if (surpriseButton) surpriseButton.disabled = !cards.some((card) => rating(card) >= 7);
})();