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
    if (!searchInput.value.trim()) {
      window.location.assign(form.action);
      return;
    }
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

  form.querySelectorAll(".filter-row select").forEach((filter) => {
    filter.addEventListener("change", () => form.requestSubmit(button));
  });

  form.querySelectorAll("[data-chip-kind]").forEach((addButton) => {
    addButton.addEventListener("click", () => {
      const kind = addButton.dataset.chipKind;
      const selector = kind === "genre" ? "#genre-chip-select" : "#recognition-chip-select";
      const value = form.querySelector(selector)?.value;
      if (!value) return;
      const url = new URL(window.location.href);
      const params = new URLSearchParams(url.search);
      const key = kind === "genre" ? "genre" : "awards";
      params.append(key, value);
      url.search = params.toString();
      window.location.assign(url);
    });
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