(() => {
  const form = document.querySelector(".search__form");
  if (!form) return;

  const button = form.querySelector(".search__button");
  const searchInput = form.querySelector("#q");
  const status = document.querySelector(".search__status");
  const suggestions = document.querySelector("#search-suggestions");
  const originalLabel = button.textContent;
  let timer = 0;
  let activeController = null;
  let requestVersion = 0;
  let suggestionTimer = 0;
  const recentPanel = document.querySelector("#recent-searches");
  const recentItems = recentPanel?.querySelector("[data-recent-items]");
  const clearRecentButton = recentPanel?.querySelector("[data-clear-recent]");
  const clearFiltersButton = form.querySelector("[data-clear-filters]");
  const genreMenuSearch = form.querySelector("#genre-menu-search");
  const genreNoMatches = form.querySelector("[data-no-genres]");
  const awardsFilter = form.querySelector("#awards");
  const recentStorageKey = "movie-finder-recent-searches";
  let resultSort = "match";

  function readRecentSearches() {
    try {
      const saved = JSON.parse(localStorage.getItem(recentStorageKey) || "[]");
      return Array.isArray(saved)
        ? saved.filter((item) => item && Array.isArray(item.genres)).map((item) => ({
          query: String(item.query || ""),
          genres: item.genres.filter((genre) => typeof genre === "string"),
          awards: Array.isArray(item.awards) ? item.awards.filter((award) => typeof award === "string") : [],
          perPage: String(item.perPage || "12"),
        }))
        : [];
    } catch {
      return [];
    }
  }

  function closeRecentSearches() {
    if (recentPanel) recentPanel.hidden = true;
    if (!suggestions || suggestions.hidden) searchInput.setAttribute("aria-expanded", "false");
  }

  function renderRecentSearches() {
    if (!recentItems) return;
    recentItems.replaceChildren();
    readRecentSearches().forEach((item) => {
      const label = item.query || item.genres.join(", ") || (item.awards.length ? "Award winners" : "Filtered movies");
      const option = document.createElement("button");
      option.type = "button";
      option.className = "search-suggestion recent-search";
      option.setAttribute("role", "option");
      const text = document.createElement("span");
      text.textContent = label;
      const detail = document.createElement("small");
      detail.textContent = [...item.genres, ...item.awards].join(" · ");
      option.append(text, detail);
      option.addEventListener("click", () => restoreRecentSearch(item));
      recentItems.append(option);
    });
  }

  function showRecentSearches() {
    if (!recentPanel || searchInput.value.trim()) return;
    renderRecentSearches();
    const hasItems = Boolean(recentItems?.childElementCount);
    recentPanel.hidden = !hasItems;
    if (hasItems) {
      closeSuggestions();
      searchInput.setAttribute("aria-expanded", "true");
    }
  }

  function rememberSearch(params) {
    const entry = {
      query: (params.get("q") || "").trim(),
      genres: params.getAll("genre"),
      awards: params.getAll("awards").filter(Boolean),
      perPage: params.get("per_page") || "12",
    };
    if (!entry.query && !entry.genres.length && !entry.awards.length) return;
    const signature = JSON.stringify(entry);
    const history = readRecentSearches().filter((item) => JSON.stringify(item) !== signature);
    history.unshift(entry);
    try {
      localStorage.setItem(recentStorageKey, JSON.stringify(history.slice(0, 5)));
    } catch {
      return;
    }
  }

  function updateClearFiltersButton() {
    if (!clearFiltersButton) return;
    const hasGenres = Boolean(genreChips?.querySelector("[data-selected-genre]"));
    clearFiltersButton.hidden = !hasGenres && !awardsFilter?.value;
  }

  function filterGenreOptions() {
    if (!genreMenuSearch || !genreMenu) return;
    const query = genreMenuSearch.value.trim().toLocaleLowerCase();
    let visibleCount = 0;
    genreMenu.querySelectorAll("[data-genre-option]").forEach((option) => {
      const name = option.querySelector(".genre-picker__name")?.textContent.toLocaleLowerCase() || "";
      const key = (option.dataset.genreName || "").toLocaleLowerCase();
      const matches = !query || name.includes(query) || key.includes(query);
      option.hidden = !matches;
      if (matches) visibleCount += 1;
    });
    if (genreNoMatches) genreNoMatches.hidden = visibleCount > 0;
  }

  function clearActiveFilters() {
    genreChips?.querySelectorAll("[data-selected-genre]").forEach((chip) => {
      const removeButton = chip.querySelector("[data-remove-genre]");
      if (removeButton) removeGenreChip(removeButton, false);
    });
    if (awardsFilter) awardsFilter.value = "";
    if (genreMenuSearch) genreMenuSearch.value = "";
    filterGenreOptions();
    updateClearFiltersButton();
    form.requestSubmit(button);
  }

  function applyLocalSort() {
    const list = document.querySelector(".results__list");
    if (!list) return;
    const cards = [...list.querySelectorAll(".movie-card")];
    const number = (card, key) => Number(card.dataset[`sort${key}`]) || 0;
    cards.sort((left, right) => {
      if (resultSort === "popular") return number(right, "Votes") - number(left, "Votes") || number(right, "Rating") - number(left, "Rating");
      if (resultSort === "rating") return number(right, "Rating") - number(left, "Rating") || number(right, "Votes") - number(left, "Votes");
      if (resultSort === "newest") return number(right, "Year") - number(left, "Year") || number(right, "Rating") - number(left, "Rating");
      return number(left, "Rank") - number(right, "Rank");
    });
    cards.forEach((card, index) => {
      list.append(card);
      const rank = card.querySelector(".movie-card__rank");
      if (rank) rank.textContent = `#${index + 1}`;
    });
  }

  function bindResultSort() {
    const sortSelect = document.querySelector("#result-sort");
    if (!sortSelect) return;
    sortSelect.value = resultSort;
    sortSelect.addEventListener("change", () => {
      resultSort = sortSelect.value;
      try {
        sessionStorage.setItem("movie-finder-sort", resultSort);
      } catch {
        resultSort = sortSelect.value;
      }
      applyLocalSort();
    });
    applyLocalSort();
  }

  try {
    const savedSort = sessionStorage.getItem("movie-finder-sort");
    if (["match", "popular", "rating", "newest"].includes(savedSort)) resultSort = savedSort;
  } catch {
    resultSort = "match";
  }

  function clearRecentSearches() {
    try {
      localStorage.removeItem(recentStorageKey);
    } catch {
      return;
    }
    renderRecentSearches();
    closeRecentSearches();
  }

  function restoreRecentSearch(item) {
    searchInput.value = item.query;
    genreChips?.querySelectorAll("[data-selected-genre]").forEach((chip) => {
      const removeButton = chip.querySelector("[data-remove-genre]");
      if (removeButton) removeGenreChip(removeButton, false);
    });
    item.genres.forEach((genre) => {
      const addButton = genreMenu?.querySelector(`[data-add-genre="${CSS.escape(genre)}"]`);
      if (addButton) addGenreChip(addButton, false);
    });
    if (awardsFilter) awardsFilter.value = item.awards[0] || "";
    const perPage = form.querySelector("#per_page");
    if (perPage && [...perPage.options].some((option) => option.value === item.perPage)) perPage.value = item.perPage;
    updateClearFiltersButton();
    closeRecentSearches();
    form.requestSubmit(button);
  }

  function closeSuggestions() {
    if (!suggestions) return;
    suggestions.hidden = true;
    suggestions.innerHTML = "";
    searchInput.setAttribute("aria-expanded", "false");
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderSuggestions(items) {
    if (!suggestions) return;
    closeRecentSearches();
    suggestions.innerHTML = items.map((item) => `
      <button type="button" role="option" class="search-suggestion" data-suggestion="${escapeHtml(item.text)}">
        <span>${escapeHtml(item.text)}</span>
        <small>${escapeHtml(item.kind)}</small>
      </button>
    `).join("");
    suggestions.hidden = false;
    searchInput.setAttribute("aria-expanded", "true");
    suggestions.querySelectorAll("[data-suggestion]").forEach((option) => {
      option.addEventListener("click", () => {
        searchInput.value = option.dataset.suggestion;
        closeSuggestions();
        form.requestSubmit(button);
      });
    });
  }

  async function loadSuggestions() {
    const typed = searchInput.value.trim();
    if (typed.length < 2) {
      closeSuggestions();
      return;
    }
    try {
      const response = await fetch(`/suggest?q=${encodeURIComponent(typed)}`);
      if (!response.ok) throw new Error("Suggestions unavailable");
      const items = await response.json();
      renderSuggestions(items);
    } catch {
      closeSuggestions();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const restoreLabel = button.textContent;
    window.clearTimeout(timer);
    activeController?.abort();

    const controller = new AbortController();
    activeController = controller;
    const version = ++requestVersion;
    const params = new URLSearchParams(new FormData(form, event.submitter));
    rememberSearch(params);
    const url = `${form.action}?${params.toString()}`;
    button.disabled = true;
    button.textContent = window.movieFinderText?.("searchingButton") || "Searching...";
    form.setAttribute("aria-busy", "true");
    status.textContent = window.movieFinderText?.("searchingStatus") || "Searching movies...";

    timer = window.setTimeout(async () => {
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const response = await fetch(url, {
          signal: controller.signal,
          headers: { "X-Requested-With": "fetch" },
        });
        if (!response.ok) throw new Error(`Search failed: ${response.status}`);
        const html = await response.text();
        if (version !== requestVersion) return;

        const nextPage = new DOMParser().parseFromString(html, "text/html");
        const nextResults = nextPage.querySelector(".results");
        const currentResults = document.querySelector(".results");
        if (!nextResults || !currentResults) throw new Error("Search results missing");
        currentResults.replaceWith(nextResults);
        bindResultSort();

        const currentFeedback = document.querySelector(".search-feedback");
        const nextFeedback = nextPage.querySelector(".search-feedback");
        if (currentFeedback && nextFeedback) currentFeedback.replaceWith(nextFeedback);
        window.dispatchEvent(new Event("movie-finder:content-updated"));

        const currentDebug = document.querySelector(".debug-panel");
        const nextDebug = nextPage.querySelector(".debug-panel");
        if (currentDebug && nextDebug) currentDebug.replaceWith(nextDebug);
        else if (currentDebug) currentDebug.remove();
        else if (nextDebug) nextResults.before(nextDebug);

        window.history.pushState({}, "", url);
        status.textContent = nextResults.querySelector(".results__count")?.textContent || "";
      } catch (error) {
        if (version !== requestVersion) return;
        if (error.name === "AbortError") {
          status.textContent = window.movieFinderText?.("searchSlow") || "Search took too long. Please try again.";
        } else {
          window.location.assign(url);
        }
      } finally {
        window.clearTimeout(timeout);
        if (version === requestVersion) {
          button.disabled = false;
          button.textContent = restoreLabel;
          form.removeAttribute("aria-busy");
          activeController = null;
        }
      }
    }, 220);
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSuggestions();
      return;
    }
    if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    event.preventDefault();
    form.requestSubmit(button);
  });

  searchInput.addEventListener("input", () => {
    closeRecentSearches();
    window.clearTimeout(suggestionTimer);
    suggestionTimer = window.setTimeout(loadSuggestions, 140);
    if (!activeController) return;
    requestVersion += 1;
    window.clearTimeout(timer);
    activeController.abort();
    activeController = null;
    button.disabled = false;
    button.textContent = originalLabel;
    form.removeAttribute("aria-busy");
    status.textContent = "";
  });
  searchInput.addEventListener("focus", showRecentSearches);

  form.querySelectorAll(".filter-row select[name]").forEach((filter) => {
    filter.addEventListener("change", () => form.requestSubmit(button));
  });

  const genreChips = form.querySelector("[data-genre-chips]");
  const genreMenu = form.querySelector("[data-genre-menu]");

  function addGenreChip(addButton, submit = true) {
    const value = addButton.dataset.addGenre;
    const option = addButton.closest("[data-genre-option]");
    const labelText = option?.querySelector(".genre-picker__name")?.textContent.trim();
    if (!genreChips || !value || !labelText || addButton.disabled || form.querySelector(`[data-selected-genre="${CSS.escape(value)}"]`)) return;

    const chip = document.createElement("span");
    chip.className = "active-filter-chip";
    chip.dataset.selectedGenre = value;

    const hiddenInput = document.createElement("input");
    hiddenInput.type = "hidden";
    hiddenInput.name = "genre";
    hiddenInput.value = value;

    const label = document.createElement("span");
    label.className = "active-filter-chip__label";
    label.dataset.i18nGenre = value;
    label.textContent = labelText;

    const removeButton = document.createElement("button");
    removeButton.className = "active-filter-chip__remove";
    removeButton.type = "button";
    removeButton.dataset.removeGenre = value;
    removeButton.setAttribute("aria-label", `Remove ${labelText} genre`);
    removeButton.title = `Remove ${labelText}`;
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => removeGenreChip(removeButton));

    chip.append(hiddenInput, label, removeButton);
    genreChips.append(chip);
    addButton.disabled = true;
    updateClearFiltersButton();
    window.dispatchEvent(new Event("movie-finder:content-updated"));
    if (submit) form.requestSubmit(button);
  }

  function removeGenreChip(removeButton, submit = true) {
    const chip = removeButton.closest("[data-selected-genre]");
    if (!chip) return;
    const value = chip.dataset.selectedGenre;
    const addButton = genreMenu?.querySelector(`[data-add-genre="${CSS.escape(value)}"]`);
    if (addButton) addButton.disabled = false;
    chip.remove();
    updateClearFiltersButton();
    window.dispatchEvent(new Event("movie-finder:content-updated"));
    if (submit) form.requestSubmit(button);
  }

  genreMenu?.querySelectorAll("[data-add-genre]").forEach((addButton) => {
    addButton.addEventListener("click", () => addGenreChip(addButton));
  });
  genreChips?.querySelectorAll("[data-remove-genre]").forEach((removeButton) => {
    removeButton.addEventListener("click", () => removeGenreChip(removeButton));
  });

  genreMenuSearch?.addEventListener("input", filterGenreOptions);
  clearFiltersButton?.addEventListener("click", clearActiveFilters);
  awardsFilter?.addEventListener("change", updateClearFiltersButton);
  clearRecentButton?.addEventListener("click", clearRecentSearches);
  document.querySelectorAll("#genre-menu-search, [data-clear-filters], [data-clear-recent]").forEach((control) => {
    control.addEventListener("click", (event) => event.stopPropagation());
  });

  document.querySelectorAll("[data-pagination-form]").forEach((paginationForm) => {
    const pageInput = paginationForm.querySelector("input[name='page']");
    const navigateToPage = () => {
      const totalPages = Number(pageInput.max);
      const page = Math.min(Math.max(Number.parseInt(pageInput.value, 10) || 1, 1), totalPages);
      pageInput.value = page;
      paginationForm.submit();
    };
    paginationForm.addEventListener("submit", (event) => {
      event.preventDefault();
      navigateToPage();
    });
    pageInput.addEventListener("change", navigateToPage);
  });

  document.addEventListener("click", (event) => {
    if (suggestions && !suggestions.contains(event.target) && event.target !== searchInput) closeSuggestions();
    if (recentPanel && !recentPanel.contains(event.target) && event.target !== searchInput) closeRecentSearches();
    if (genreMenu?.open && !genreMenu.contains(event.target)) genreMenu.open = false;
  });

  document.addEventListener("click", (event) => {
    if (event.target === searchInput) showRecentSearches();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && genreMenu?.open) genreMenu.open = false;
  });

  window.addEventListener("popstate", () => window.location.reload());
  renderRecentSearches();
  if (!searchInput.value.trim()) showRecentSearches();
  bindResultSort();
  updateClearFiltersButton();
})();