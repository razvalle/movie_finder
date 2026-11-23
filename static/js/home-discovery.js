(() => {
  const cards = [...document.querySelectorAll(".movie-card[data-detail-title]")];
  const searchInput = document.querySelector("#q");
  const searchForm = document.querySelector(".search__form");
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

  const templateValues = (card, selector) => [...(card.querySelector(selector)?.content.querySelectorAll("span") || [])]
    .map((element) => element.textContent.trim()).filter(Boolean);
  const rating = (card) => Number(card.dataset.detailRating) || 0;
  const runtime = (card) => Number(card.dataset.detailRuntime) || 0;

  function openMovie(card) {
    card.click();
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

  if (surpriseButton) surpriseButton.disabled = !cards.some((card) => rating(card) >= 7);
})();