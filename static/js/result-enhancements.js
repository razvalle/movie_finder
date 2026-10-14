(() => {
  const results = document.querySelector(".results");
  const list = results?.querySelector(".results__list");
  const groupSelect = document.querySelector("[data-group-mode]");
  const cards = list ? [...list.querySelectorAll(":scope > .movie-card")] : [];
  if (!results || !list) return;

  const feedbackStorageKey = "movie-finder-pending-feedback-ui";
  let orderedCards = cards.slice();
  let activeGroup = "";

  function runtimeFit(constraint, movieRuntime) {
    const runtime = Number(movieRuntime);
    if (!constraint || !runtime) return "";
    const upper = constraint.match(/^<\s*(\d+)/);
    const lower = constraint.match(/^>\s*(\d+)/);
    const range = constraint.match(/^(\d+)\s*-\s*(\d+)/);
    if (upper && runtime <= Number(upper[1])) {
      const minutes = Number(upper[1]);
      const limit = minutes === 120 ? "2-hour" : `${minutes}-minute`;
      return `Fits your ${limit} limit`;
    }
    if (lower && runtime >= Number(lower[1])) return `Fits your ${lower[1]}-minute minimum`;
    if (range && runtime >= Number(range[1]) && runtime <= Number(range[2])) {
      return `Fits your ${range[1]}-${range[2]} min range`;
    }
    return "";
  }

  function applyRuntimeFits() {
    const constraint = results.dataset.runtimeConstraint || "";
    cards.forEach((card) => {
      const badge = card.querySelector("[data-runtime-fit]");
      if (!badge) return;
      const text = runtimeFit(constraint, card.dataset.detailRuntime);
      badge.textContent = text;
      badge.hidden = !text;
    });
  }

  function groupLabel(card, mode) {
    if (mode === "decade") {
      const year = Number(card.dataset.detailYear);
      return year ? `${Math.floor(year / 10) * 10}s` : "Year unavailable";
    }
    const genreTemplate = card.querySelector(".movie-card__genres");
    const genre = genreTemplate?.content.querySelector("span")?.textContent.trim();
    return genre || "Genre unavailable";
  }

  function renderGroups(mode) {
    activeGroup = mode;
    list.classList.toggle("results__list--grouped", Boolean(mode));
    list.dataset.grouped = mode;
    if (!mode) {
      list.replaceChildren(...orderedCards);
      return;
    }

    const groups = new Map();
    orderedCards.forEach((card) => {
      const label = groupLabel(card, mode);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(card);
    });
    const fragment = document.createDocumentFragment();
    groups.forEach((groupCards, label) => {
      const section = document.createElement("section");
      section.className = "result-group";
      const heading = document.createElement("h3");
      heading.className = "result-group__heading";
      heading.textContent = label;
      const groupList = document.createElement("div");
      groupList.className = "result-group__cards";
      groupCards.forEach((card) => groupList.append(card));
      section.append(heading, groupList);
      fragment.append(section);
    });
    list.replaceChildren(fragment);
  }

  function applyFeedbackSelection(card, value) {
    const buttons = card.querySelectorAll(".movie-card__feedback button[name='relevant']");
    buttons.forEach((button) => {
      const selected = button.value === value;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
      const check = button.querySelector("[data-feedback-check]");
      if (check) check.hidden = !selected;
    });
    const status = card.querySelector("[data-feedback-status]");
    if (status) {
      status.textContent = "Thanks, noted";
      status.hidden = false;
    }
  }

  function restoreFeedbackConfirmation() {
    if (results.dataset.feedbackSaved !== "true") return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(feedbackStorageKey) || "null");
      if (!saved || !["0", "1"].includes(saved.value)) return;
      const card = cards.find((candidate) => candidate.querySelector(".movie-card__feedback input[name='movie_id']")?.value === saved.movieId);
      if (card) applyFeedbackSelection(card, saved.value);
      sessionStorage.removeItem(feedbackStorageKey);
    } catch {
      sessionStorage.removeItem(feedbackStorageKey);
    }
  }

  cards.forEach((card) => {
    card.querySelector(".movie-card__feedback")?.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (!submitter || !["0", "1"].includes(submitter.value)) return;
      try {
        sessionStorage.setItem(feedbackStorageKey, JSON.stringify({
          movieId: card.querySelector(".movie-card__feedback input[name='movie_id']")?.value || "",
          value: submitter.value,
        }));
      } catch {
        return;
      }
    });
  });

  groupSelect?.addEventListener("change", () => renderGroups(groupSelect.value));
  document.querySelector("#result-sort")?.addEventListener("change", () => {
    orderedCards = [...list.querySelectorAll(".movie-card")];
    if (activeGroup) renderGroups(activeGroup);
  });

  applyRuntimeFits();
  restoreFeedbackConfirmation();
})();