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
        status.textContent = "";
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

  form.querySelectorAll(".filter-row select[name]").forEach((filter) => {
    filter.addEventListener("change", () => form.requestSubmit(button));
  });

  const genreSelect = form.querySelector("#genre-add-select");
  const addGenreButton = form.querySelector("[data-add-genre]");
  const genreChips = form.querySelector("[data-genre-chips]");

  function addGenreChip(value) {
    if (!genreSelect || !genreChips || !value) return;
    const option = [...genreSelect.options].find((item) => item.value === value);
    if (!option || option.disabled || form.querySelector(`[data-selected-genre="${CSS.escape(value)}"]`)) return;

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
    label.textContent = option.textContent;

    const removeButton = document.createElement("button");
    removeButton.className = "active-filter-chip__remove";
    removeButton.type = "button";
    removeButton.dataset.removeGenre = value;
    removeButton.setAttribute("aria-label", `Remove ${option.textContent} genre`);
    removeButton.title = `Remove ${option.textContent}`;
    removeButton.textContent = "×";

    chip.append(hiddenInput, label, removeButton);
    genreChips.append(chip);
    option.disabled = true;
    genreSelect.value = "";
    addGenreButton.disabled = true;
    window.dispatchEvent(new Event("movie-finder:content-updated"));
  }

  function removeGenreChip(button) {
    const chip = button.closest("[data-selected-genre]");
    if (!chip) return;
    const value = chip.dataset.selectedGenre;
    const option = [...(genreSelect?.options || [])].find((item) => item.value === value);
    if (option) option.disabled = false;
    chip.remove();
  }

  genreSelect?.addEventListener("change", () => {
    if (addGenreButton) addGenreButton.disabled = !genreSelect.value;
  });
  addGenreButton?.addEventListener("click", () => {
    addGenreChip(genreSelect?.value);
  });
  genreChips?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-remove-genre]");
    if (removeButton) removeGenreChip(removeButton);
  });

  form.querySelectorAll("[data-pagination-form]").forEach((paginationForm) => {
    paginationForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const pageInput = paginationForm.querySelector("input[name='page']");
      const totalPages = Number(pageInput.max);
      const page = Math.min(Math.max(Number.parseInt(pageInput.value, 10) || 1, 1), totalPages);
      pageInput.value = page;
      paginationForm.submit();
    });
  });

  document.addEventListener("click", (event) => {
    if (suggestions && !suggestions.contains(event.target) && event.target !== searchInput) closeSuggestions();
  });

  window.addEventListener("popstate", () => window.location.reload());
})();