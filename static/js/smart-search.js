(() => {
  const form = document.querySelector(".search__form");
  if (!form) return;

  const button = form.querySelector(".search__button");
  const searchInput = form.querySelector("#q");
  const status = document.querySelector(".search__status");
  let timer = 0;
  let activeController = null;
  let requestVersion = 0;

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
    if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    event.preventDefault();
    form.requestSubmit(button);
  });

  form.querySelectorAll(".filter-row select").forEach((filter) => {
    filter.addEventListener("change", () => form.requestSubmit(button));
  });

  form.addEventListener("input", () => {
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

  window.addEventListener("popstate", () => window.location.reload());
})();