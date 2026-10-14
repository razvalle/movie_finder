(() => {
  const searchInput = document.querySelector("#q");
  const helpDrawer = document.querySelector("#help-drawer");
  const aboutDrawer = document.querySelector("#about-drawer");
  const onboarding = document.querySelector("[data-onboarding-layer]");
  const offlineNotice = document.querySelector("[data-offline-notice]");
  const offlineMessage = document.querySelector("[data-offline-message]");
  const focusReturn = new Map();
  const onboardingKey = "movie-finder-onboarding-dismissed";
  let activeDialog = null;

  function focusableElements(dialog) {
    return [...dialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.hidden && element.getClientRects().length);
  }

  function openDialog(dialog, trigger) {
    if (!dialog) return;
    if (activeDialog && activeDialog !== dialog) closeDialog(activeDialog, false, false);
    focusReturn.set(dialog, trigger || searchInput);
    dialog.hidden = false;
    activeDialog = dialog;
    document.body.classList.add("support-dialog-open");
    const focusTarget = focusableElements(dialog)[0] || dialog.querySelector("[role='dialog']");
    focusTarget?.focus();
  }

  function closeDialog(dialog, restore = true, rememberOnboarding = true) {
    if (!dialog || dialog.hidden) return;
    dialog.hidden = true;
    if (dialog === onboarding && rememberOnboarding) {
      try { localStorage.setItem(onboardingKey, "true"); } catch { /* Keep the current visit dismissible. */ }
    }
    if (activeDialog === dialog) activeDialog = null;
    document.body.classList.remove("support-dialog-open");
    if (restore) focusReturn.get(dialog)?.focus();
  }

  function fillSearch(example, dialog) {
    if (!searchInput || !example) return;
    searchInput.value = example;
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    closeDialog(dialog, false);
    searchInput.focus();
  }

  function showOffline(message) {
    if (!offlineNotice || !offlineMessage) return;
    offlineMessage.textContent = message;
    offlineNotice.hidden = false;
  }

  function clearOffline() {
    if (offlineNotice) offlineNotice.hidden = true;
  }

  document.querySelector("[data-open-help]")?.addEventListener("click", (event) => openDialog(helpDrawer, event.currentTarget));
  document.querySelector("[data-open-about]")?.addEventListener("click", (event) => openDialog(aboutDrawer, event.currentTarget));
  document.querySelectorAll("[data-support-close]").forEach((button) => button.addEventListener("click", () => closeDialog(button.closest(".support-drawer"))));
  document.querySelectorAll("[data-support-backdrop]").forEach((backdrop) => backdrop.addEventListener("click", () => closeDialog(backdrop.closest(".support-drawer"))));
  document.querySelector("[data-onboarding-close]")?.addEventListener("click", () => closeDialog(onboarding));
  document.querySelectorAll("[data-example-query]").forEach((button) => button.addEventListener("click", () => fillSearch(button.dataset.exampleQuery, helpDrawer)));
  document.querySelectorAll("[data-onboarding-example]").forEach((button) => button.addEventListener("click", () => fillSearch(button.dataset.onboardingExample, onboarding)));
  document.querySelector("[data-offline-retry]")?.addEventListener("click", () => window.location.reload());

  document.addEventListener("keydown", (event) => {
    if (!activeDialog) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog(activeDialog);
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = focusableElements(activeDialog);
    if (!focusables.length) { event.preventDefault(); return; }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  try {
    if (localStorage.getItem(onboardingKey) !== "true") {
      window.setTimeout(() => openDialog(onboarding, searchInput), 120);
    }
  } catch {
    window.setTimeout(() => openDialog(onboarding, searchInput), 120);
  }

  document.addEventListener("error", (event) => {
    const image = event.target;
    if (image instanceof HTMLImageElement && image.closest(".movie-card__poster, .browse-row__movie")) {
      showOffline("A poster could not be reached. The app is showing its local fallback where available.");
    }
  }, true);
  window.addEventListener("movie-finder:data-unreachable", () => {
    showOffline("Movie data could not be reached. Your current results remain available; retry to reconnect.");
  });
  window.addEventListener("offline", () => showOffline("You appear to be offline. Retry when your connection is available."));
  window.addEventListener("online", clearOffline);
})();