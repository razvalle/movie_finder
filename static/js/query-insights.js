(() => {
  const searchInput = document.querySelector("#q");
  const highlightLayer = document.querySelector(".search__highlight-layer");
  const breakdownTriggers = [...document.querySelectorAll("[data-match-breakdown]")];
  let activeTrigger = null;

  function parserTerms() {
    const terms = [];
    document.querySelectorAll(".pref-tag").forEach((tag) => {
      const label = tag.querySelector(".pref-tag__label")?.textContent.trim().toLowerCase() || "";
      const value = tag.textContent.replace(tag.querySelector(".pref-tag__label")?.textContent || "", "").replace(/[×+−]/g, "").trim();
      const isExcluded = tag.classList.contains("pref-tag--exclude");
      const type = isExcluded ? "exclude" : label === "genre" ? "genre" : label === "mood" ? "mood" : label === "theme" ? "theme" : label === "runtime" || label === "year" ? "runtime" : "include";
      const values = label === "keywords" ? value.split(",").map((item) => item.trim()) : [value];
      values.filter(Boolean).forEach((term) => terms.push({ term, type }));
    });
    return terms.sort((left, right) => right.term.length - left.term.length);
  }

  function escapeHtml(value) {
    return value.replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function enhanceSearchHighlights() {
    if (!highlightLayer || highlightLayer.hidden) return;
    const terms = parserTerms();
    const tokens = [...highlightLayer.querySelectorAll(".highlight-token")];
    tokens.forEach((token) => {
      const term = terms.find((candidate) => candidate.term.toLowerCase() === token.textContent.trim().toLowerCase());
      if (!term) return;
      token.classList.remove("highlight-token--include", "highlight-token--genre", "highlight-token--mood", "highlight-token--theme", "highlight-token--runtime", "highlight-token--exclude");
      token.classList.add(`highlight-token--${term.type}`);
    });
    [...highlightLayer.querySelectorAll(".highlight-token--exclude")].forEach((token) => {
      const previous = token.previousSibling;
      if (!previous || previous.nodeType !== Node.TEXT_NODE) return;
      const cue = previous.textContent.match(/\b(?:do not want|does not want|did not want|don't want|dont want|rather not|not|no|without|excluding|exclude|avoid|avoiding|skip|never)\s+(?:any\s+)?$/i);
      if (!cue) return;
      const cueText = cue[0];
      previous.textContent = previous.textContent.slice(0, -cueText.length);
      const cueMark = document.createElement("span");
      cueMark.className = "highlight-token highlight-token--exclude";
      cueMark.textContent = cueText;
      previous.after(cueMark);
    });
  }

  function markSynopsis(synopsis, terms) {
    const source = synopsis.textContent;
    if (!source || synopsis.dataset.highlighted === "true") return;
    const matches = [];
    terms.forEach(({ term, type }) => {
      if (["runtime", "include"].includes(type) && /^(?:[<>~]|\d{4}|\d+-\d+|\d{4}s)/.test(term)) return;
      const expression = new RegExp(`\\b${term.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}\\b`, "gi");
      for (const match of source.matchAll(expression)) matches.push({ start: match.index, end: match.index + match[0].length, type });
    });
    matches.sort((left, right) => left.start - right.start || right.end - left.end);
    const selected = [];
    matches.forEach((match) => {
      if (match.start >= (selected.at(-1)?.end || 0)) selected.push(match);
    });
    if (!selected.length) return;
    let position = 0;
    let html = "";
    selected.forEach(({ start, end, type }) => {
      html += escapeHtml(source.slice(position, start));
      html += `<mark class="synopsis-match synopsis-match--${type}">${escapeHtml(source.slice(start, end))}</mark>`;
      position = end;
    });
    synopsis.innerHTML = html + escapeHtml(source.slice(position));
    synopsis.dataset.highlighted = "true";
  }

  function enhanceSynopses() {
    const terms = parserTerms();
    document.querySelectorAll(".movie-card__synopsis").forEach((synopsis) => markSynopsis(synopsis, terms));
  }

  function closeBreakdown(restoreFocus = false) {
    if (!activeTrigger) return;
    const panel = document.getElementById(activeTrigger.dataset.breakdownId);
    if (panel) {
      panel.hidden = true;
      panel.querySelector("[data-breakdown-close]")?.remove();
    }
    activeTrigger.setAttribute("aria-expanded", "false");
    const trigger = activeTrigger;
    activeTrigger = null;
    if (restoreFocus) trigger.focus();
  }

  function openBreakdown(trigger) {
    if (activeTrigger && activeTrigger !== trigger) closeBreakdown();
    const panel = document.getElementById(trigger.dataset.breakdownId);
    if (!panel) return;
    if (activeTrigger === trigger) {
      closeBreakdown();
      return;
    }
    activeTrigger = trigger;
    trigger.setAttribute("aria-expanded", "true");
    panel.hidden = false;
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "match-breakdown__close";
    closeButton.dataset.breakdownClose = "";
    closeButton.textContent = "Close";
    closeButton.addEventListener("click", () => closeBreakdown(true));
    panel.append(closeButton);
    closeButton.focus();
  }

  breakdownTriggers.forEach((trigger) => {
    trigger.setAttribute("role", "button");
    trigger.setAttribute("tabindex", "0");
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", trigger.dataset.breakdownId);
    trigger.setAttribute("aria-label", `Open match breakdown, ${trigger.textContent.trim()}`);
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      openBreakdown(trigger);
    });
    trigger.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        event.stopPropagation();
        openBreakdown(trigger);
      }
    });
  });

  document.addEventListener("click", (event) => {
    if (activeTrigger && !event.target.closest(".match-breakdown, [data-match-breakdown]")) closeBreakdown();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activeTrigger) {
      event.preventDefault();
      closeBreakdown(true);
    }
  });
  searchInput?.addEventListener("input", enhanceSearchHighlights);
  enhanceSearchHighlights();
  enhanceSynopses();
})();