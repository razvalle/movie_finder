(() => {
  const viewButtons = [...document.querySelectorAll("[data-view-mode]")];

  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      viewButtons.forEach((candidate) => {
        candidate.setAttribute("aria-pressed", String(candidate === button));
      });
      document.documentElement.dataset.viewMode = button.dataset.viewMode;
    });
  });

  const sort = document.querySelector("#result-sort");
  sort?.addEventListener("change", () => {
    document.documentElement.dataset.sortMode = sort.value;
  });
})();
