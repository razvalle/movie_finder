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
  let activeSuggestionIndex = -1;
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

  function setSearchLoading(loading) {
    const results = document.querySelector(".results");
    const list = results?.querySelector("[data-results-list]");
    if (!results || !list) return;
    results.setAttribute("aria-busy", String(loading));
    list.querySelectorAll(".movie-card--skeleton").forEach((card) => card.remove());
    if (!loading) return;
    for (let index = 0; index < 3; index += 1) {
      const card = document.createElement("article");
      card.className = "movie-card movie-card--skeleton";
      card.setAttribute("aria-hidden", "true");
      card.innerHTML = '<div class="skeleton-block skeleton-poster"></div><div><div class="skeleton-block skeleton-line"></div><div class="skeleton-block skeleton-line"></div><div class="skeleton-block skeleton-line"></div></div>';
      list.append(card);
    }
  }

  function showErrorToast(fallbackUrl) {
    document.querySelector(".error-toast")?.remove();
    const toast = document.createElement("div");
    toast.className = "error-toast";
    toast.setAttribute("role", "alert");
    toast.setAttribute("aria-live", "assertive");
    toast.textContent = "Search could not refresh. Loading the results page instead.";
    document.body.append(toast);
    window.setTimeout(() => window.location.assign(fallbackUrl), 1300);
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
    activeSuggestionIndex = -1;
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
    if (!items.length) {
      closeSuggestions();
      return;
    }
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
    activeSuggestionIndex = -1;
  }

  const highlightLayer = form.querySelector(".search__highlight-layer");
  const genreChips = form.querySelector("[data-genre-chips]");
  const genreMenu = form.querySelector("[data-genre-menu]");
  let sliderBaseQuery = searchInput.value.trim();
  let sliderQuery = "";
  let sliderStarted = false;
  const sliderTouched = new Set();
  const sliderPatterns = {
    "runtime-range": /\b(?:under|below|less than|over|above|more than|at most|at least)\s+\d+\s*(?:minutes?|hours?|mins?|hrs?)\b/gi,
    "year-range": /\b(?:from|after|since|before|in)\s+\d{4}(?:\s*(?:to|through|until|-)\s*\d{4})?\b/gi,
    "rating-range": /\b(?:rated|rating)\s+(?:at least|above|over|minimum of)?\s*\d+(?:\.\d+)?(?:\s+out of 10)?\b/gi,
  };

  function updateSearchHighlight() {
    if (!highlightLayer) return;
    const value = searchInput.value;
    const rules = [];
    const knownTerms = new Map();
    form.querySelectorAll("[data-genre-option]").forEach((option) => {
      const term = option.dataset.genreName || "";
      if (term) knownTerms.set(term, "include");
    });
    document.querySelectorAll(".pref-tag").forEach((tag) => {
      const label = tag.querySelector(".pref-tag__label")?.textContent.trim().toLowerCase();
      const term = tag.textContent.replace(tag.querySelector(".pref-tag__label")?.textContent || "", "").replace(/[×+−]/g, "").trim();
      const type = tag.classList.contains("pref-tag--exclude") ? "exclude" : tag.classList.contains("pref-tag--neutral") ? "runtime" : "include";
      if (term && !["keywords", "filtered out"].includes(label)) knownTerms.set(term.toLowerCase(), type);
    });
    [...knownTerms.entries()].sort((left, right) => right[0].length - left[0].length).forEach(([term, type]) => {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const match = new RegExp(`\\b${escaped}\\b`, "gi");
      for (const found of value.matchAll(match)) {
        const prefix = value.slice(Math.max(0, found.index - 28), found.index);
        const detectedType = /\b(?:no|not|without|excluding|exclude|avoid|avoiding)\s+(?:any\s+)?(?:\w+\s+){0,1}$/i.test(prefix) ? "exclude" : type;
        rules.push({ start: found.index, end: found.index + found[0].length, type: detectedType });
      }
    });
    for (const match of value.matchAll(/\b(?:under|below|less than|over|above|more than)\s+\d+\s*(?:minutes?|hours?|mins?|hrs?)\b|\b(?:from|after|since|before)\s+\d{4}\b/gi)) {
      rules.push({ start: match.index, end: match.index + match[0].length, type: "runtime" });
    }
    rules.sort((left, right) => left.start - right.start || right.end - left.end);
    const nonOverlapping = [];
    rules.forEach((rule) => {
      if (rule.start >= (nonOverlapping.at(-1)?.end || 0)) nonOverlapping.push(rule);
    });
    let cursor = 0;
    let html = "";
    nonOverlapping.forEach((rule) => {
      html += escapeHtml(value.slice(cursor, rule.start));
      html += `<span class="highlight-token highlight-token--${rule.type}">${escapeHtml(value.slice(rule.start, rule.end))}</span>`;
      cursor = rule.end;
    });
    html += escapeHtml(value.slice(cursor));
    highlightLayer.innerHTML = html;
    highlightLayer.hidden = !value;
    searchInput.classList.toggle("has-highlight", Boolean(value));
    highlightLayer.scrollTop = searchInput.scrollTop;
  }

  function bindViewToggle() {
    const list = document.querySelector(".results__list");
    const controls = document.querySelectorAll("[data-view-mode]");
    if (!list || !controls.length) return;
    let mode = "grid";
    try { mode = localStorage.getItem("movie-finder-view") || "grid"; } catch { mode = "grid"; }
    if (!["grid", "list"].includes(mode)) mode = "grid";
    list.dataset.view = mode;
    controls.forEach((control) => {
      control.setAttribute("aria-pressed", String(control.dataset.viewMode === mode));
      control.onclick = () => {
        mode = control.dataset.viewMode;
        list.dataset.view = mode;
        controls.forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.viewMode === mode)));
        try { localStorage.setItem("movie-finder-view", mode); } catch { /* Keep this as a page-only preference. */ }
      };
    });
  }

  const drawer = document.querySelector("#movie-drawer");
  let drawerTrigger = null;

  function closeDrawer(restoreFocus = true) {
    if (!drawer) return;
    drawer.hidden = true;
    document.body.classList.remove("drawer-open");
    if (restoreFocus) drawerTrigger?.focus();
  }

  function openDrawer(card, trigger) {
    if (!drawer) return;
    drawerTrigger = trigger;
    const title = card.dataset.detailTitle || "";
    drawer.querySelector(".movie-drawer__title").textContent = title;
    drawer.querySelector(".movie-drawer__year").textContent = card.dataset.detailYear || "";
    drawer.querySelector(".movie-drawer__synopsis").textContent = card.querySelector(".movie-card__synopsis")?.textContent.trim() || "";
    const poster = drawer.querySelector(".movie-drawer__poster");
    const cardPoster = card.querySelector(".movie-card__poster img");
    poster.hidden = !cardPoster;
    poster.alt = `${title} poster`;
    if (cardPoster) {
      poster.dataset.posterFallback = cardPoster.dataset.posterFallback || "";
      poster.src = cardPoster.currentSrc || cardPoster.src;
    }
    const cast = drawer.querySelector(".movie-drawer__cast");
    cast.replaceChildren(...(card.querySelector(".movie-card__cast")?.content.cloneNode(true).childNodes || []));
    drawer.querySelector(".movie-drawer__cast-section").hidden = !cast.childElementCount;
    const related = drawer.querySelector(".movie-drawer__similar-results");
    related.hidden = true;
    related.querySelector("ul").replaceChildren();
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    drawer.querySelector("[data-close-drawer]").focus();
  }

  function bindDetailTriggers() {
    document.querySelectorAll(".movie-card").forEach((card) => {
      card.onclick = (event) => {
        if (event.target.closest("a, button, form, input, select, textarea, summary")) return;
        card.tabIndex = -1;
        openDrawer(card, card);
      };
    });
    document.querySelectorAll("[data-open-detail]").forEach((trigger) => {
      trigger.onclick = () => openDrawer(trigger.closest(".movie-card"), trigger);
    });
  }

  async function loadSimilarMovies() {
    const title = drawer?.querySelector(".movie-drawer__title")?.textContent.trim();
    const section = drawer?.querySelector(".movie-drawer__similar-results");
    if (!title || !section) return;
    const list = section.querySelector("ul");
    list.replaceChildren();
    section.hidden = false;
    const loading = document.createElement("li");
    loading.textContent = "Searching this catalog...";
    list.append(loading);
    try {
      const query = `movies like ${title}`;
      const response = await fetch(`/?q=${encodeURIComponent(query)}&per_page=12`, { headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) throw new Error("Related titles unavailable");
      const documentResult = new DOMParser().parseFromString(await response.text(), "text/html");
      const titles = [...documentResult.querySelectorAll(".movie-card[data-detail-title]")]
        .filter((card) => card.dataset.detailTitle.toLowerCase() !== title.toLowerCase())
        .slice(0, 5);
      list.replaceChildren();
      titles.forEach((card) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        const url = new URL("/", window.location.origin);
        url.searchParams.set("q", query);
        link.href = url.href;
        link.textContent = card.dataset.detailTitle + (card.dataset.detailYear ? ` (${card.dataset.detailYear})` : "");
        item.append(link);
        list.append(item);
      });
      if (!titles.length) {
        const item = document.createElement("li");
        item.textContent = "No related catalog titles found.";
        list.append(item);
      }
    } catch {
      list.replaceChildren();
      const item = document.createElement("li");
      item.textContent = "Related titles could not be loaded.";
      list.append(item);
    }
  }

  function bindTierThreeControls() {
    const sliders = [
      ["runtime-range", "runtime-range", (value) => value < 240 ? `under ${value} minutes` : "", " min"],
      ["year-range", "year-range", (value) => value > 1880 ? `after ${value - 1}` : "", ""],
      ["rating-range", "rating-range", (value) => value > 0 ? `rated at least ${value} out of 10` : "", ""],
    ];
    const elements = sliders.map(([id]) => document.getElementById(id)).filter(Boolean);
    const parsedTags = [...document.querySelectorAll(".pref-tag")].map((tag) => ({
      label: tag.querySelector(".pref-tag__label")?.textContent.trim().toLowerCase(),
      value: tag.textContent.replace(tag.querySelector(".pref-tag__label")?.textContent || "", "").replace(/[×+−]/g, "").trim(),
    }));
    parsedTags.forEach(({ label, value }) => {
      if (label === "runtime") {
        const limit = value.match(/<\s*(\d+)/);
        const runtime = document.getElementById("runtime-range");
        if (limit && runtime) runtime.value = String(Math.min(Number(runtime.max), Math.max(Number(runtime.min), Number(limit[1]))));
      }
      if (label === "year") {
        const span = value.match(/(\d{4})\s*-\s*(\d{4})/);
        const after = value.match(/after\s+(\d{4})/i);
        const year = document.getElementById("year-range");
        const parsedYear = span ? Number(span[1]) : after ? Number(after[1]) + 1 : null;
        if (year && parsedYear) year.value = String(Math.min(Number(year.max), Math.max(Number(year.min), parsedYear)));
      }
    });
    elements.forEach((input) => {
      const output = input.previousElementSibling?.querySelector("output");
      if (output) output.textContent = input.id === "runtime-range" ? `${input.value} min` : input.id === "rating-range" ? Number(input.value).toFixed(1) : input.value;
    });
    const applySliders = (changedInput, submit) => {
      if (!sliderStarted) {
        sliderBaseQuery = searchInput.value.trim();
        sliderStarted = true;
      }
      sliderTouched.add(changedInput.id);
      const phrases = sliders.map(([id, , phrase]) => {
        const input = document.getElementById(id);
        const output = input?.previousElementSibling?.querySelector("output");
        if (output) output.textContent = id === "runtime-range" ? `${input.value} min` : id === "rating-range" ? Number(input.value).toFixed(1) : input.value;
        return input ? phrase(Number(input.value)) : "";
      }).filter(Boolean);
      const base = [...sliderTouched].reduce((query, id) => query.replace(sliderPatterns[id], ""), sliderBaseQuery).replace(/\s+/g, " ").trim();
      sliderQuery = phrases.join(" ");
      const queryParts = /\b(?:movies?|films?)\s+like\b/i.test(base) ? [sliderQuery, base] : [base, sliderQuery];
      searchInput.value = queryParts.filter(Boolean).join(" ");
      updateSearchHighlight();
      if (submit) {
        const params = new URLSearchParams(new FormData(form));
        window.location.assign(`${form.action}?${params.toString()}`);
      }
    };
    elements.forEach((input) => {
      input.addEventListener("input", () => applySliders(input, false));
      input.addEventListener("change", () => applySliders(input, true));
    });
  }

  bindDetailTriggers();
  bindViewToggle();
  bindTierThreeControls();
  updateSearchHighlight();

  drawer?.querySelector("[data-close-drawer]")?.addEventListener("click", () => closeDrawer());
  drawer?.querySelector("[data-more-like-this]")?.addEventListener("click", loadSimilarMovies);
  document.querySelector(".back-to-top")?.addEventListener("click", () => window.scrollTo({
    top: 0,
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  }));
  window.addEventListener("scroll", () => {
    document.querySelector(".site-header")?.classList.toggle("is-scrolled", window.scrollY > 4);
    const topButton = document.querySelector(".back-to-top");
    if (topButton) topButton.hidden = window.scrollY < 480;
  }, { passive: true });

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
    setSearchLoading(true);

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
        bindDetailTriggers();
        bindViewToggle();

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
          setSearchLoading(false);
          showErrorToast(url);
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
      if (drawer && !drawer.hidden) closeDrawer(false);
      return;
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && suggestions && !suggestions.hidden) {
      event.preventDefault();
      const options = [...suggestions.querySelectorAll("[data-suggestion]")];
      activeSuggestionIndex = activeSuggestionIndex < 0
        ? (event.key === "ArrowDown" ? 0 : options.length - 1)
        : (activeSuggestionIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options.forEach((option, index) => {
        option.classList.toggle("is-active", index === activeSuggestionIndex);
        option.setAttribute("aria-selected", String(index === activeSuggestionIndex));
      });
      return;
    }
    if (event.key === "Enter" && activeSuggestionIndex >= 0 && suggestions && !suggestions.hidden) {
      event.preventDefault();
      suggestions.querySelectorAll("[data-suggestion]")[activeSuggestionIndex]?.click();
      return;
    }
    if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    event.preventDefault();
    form.requestSubmit(button);
  });

  searchInput.addEventListener("input", () => {
    closeRecentSearches();
    sliderBaseQuery = searchInput.value.replace(sliderQuery, "").trim();
    sliderQuery = "";
    sliderStarted = false;
    sliderTouched.clear();
    updateSearchHighlight();
    window.clearTimeout(suggestionTimer);
    suggestionTimer = window.setTimeout(loadSuggestions, 140);
    if (!activeController) return;
    requestVersion += 1;
    window.clearTimeout(timer);
    activeController.abort();
    activeController = null;
    setSearchLoading(false);
    button.disabled = false;
    button.textContent = originalLabel;
    form.removeAttribute("aria-busy");
    status.textContent = "";
  });
  searchInput.addEventListener("scroll", updateSearchHighlight);
  searchInput.addEventListener("focus", showRecentSearches);

  form.querySelectorAll(".filter-row select[name]").forEach((filter) => {
    filter.addEventListener("change", () => form.requestSubmit(button));
  });

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

  const refinePanel = form.querySelector(".refine-panel");
  refinePanel?.addEventListener("toggle", () => {
    document.body.classList.toggle("refine-open", refinePanel.open);
  });
  refinePanel?.querySelector("[data-close-refine]")?.addEventListener("click", () => {
    refinePanel.open = false;
    refinePanel.querySelector("summary")?.focus();
  });

  document.querySelectorAll("[data-page-url]").forEach((pageButton) => {
    pageButton.addEventListener("click", () => {
      if (!pageButton.disabled) window.location.assign(pageButton.dataset.pageUrl);
    });
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
    if (event.key === "Escape") {
      if (genreMenu?.open) genreMenu.open = false;
      if (drawer && !drawer.hidden) closeDrawer();
      form.querySelector(".refine-panel")?.removeAttribute("open");
    }
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
      && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
    if (event.key === "ArrowDown" && drawer && !drawer.hidden) {
      drawer.querySelector("[data-close-drawer]")?.focus();
    }
  });

  drawer?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = [...drawer.querySelectorAll("button:not([disabled]), a[href], input:not([disabled])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener("popstate", () => window.location.reload());
    window.addEventListener("movie-finder:content-updated", updateSearchHighlight);
  renderRecentSearches();
  if (!searchInput.value.trim()) showRecentSearches();
  bindResultSort();
  updateClearFiltersButton();
})();