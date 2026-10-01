(() => {
  const root = document.documentElement;
  const themeButton = document.querySelector("#theme-toggle");
  const themeIcon = themeButton?.querySelector(".theme-toggle__icon");
  const languageSelect = document.querySelector("#ui-language");
  const translations = {
    es: {
      brand: "Buscador de películas",
      headerSubtitle: "Busca por descripción o explora películas por género.",
      interfaceLanguage: "Idioma de la interfaz",
      findLabel: "Encuentra una película",
      searchPlaceholder: "Ej.: una película de misterio tensa, pero no de terror, de menos de dos horas.",
      findButton: "Buscar películas",
      genreLabel: "Género",
      allGenres: "Todos los géneros",
      recognitionLabel: "Reconocimiento",
      allFilms: "Todas las películas",
      awardWinners: "Películas con premios importantes",
      perPageLabel: "Por página",
      languageLabel: "Idioma",
      allLanguages: "Todos los idiomas",
      noLanguageData: "El catálogo todavía no incluye idiomas",
      ageRatingLabel: "Clasificación por edad",
      allRatings: "Todas las clasificaciones",
      noRatingData: "El catálogo todavía no incluye clasificaciones",
      recommendedMovies: "Películas recomendadas",
      allMovies: "Todas las películas",
      countExact: "{count} coincidencias exactas",
      countClosest: "{count} opciones cercanas",
      countBrowse: "{count} películas",
      exactMatch: "Coincidencia exacta",
      closestMatch: "Opción cercana",
      unverified: "Sin verificar",
      themeLight: "Cambiar a tema claro",
      themeDark: "Cambiar a tema oscuro",
      partialMessage: "No encontré una coincidencia exacta. Aquí tienes opciones cercanas; algunos detalles no se pueden confirmar con los datos actuales.",
      emptyMessage: "No encontré una opción cercana. Prueba con un género, estado de ánimo, actor o una pista breve de la trama.",
      emptyStateTitle: "No se encontraron películas.",
      emptyStateBody: "Prueba otra búsqueda o elige un género distinto.",
      exactMessage: "Encontré películas que coinciden con lo que pediste.",
      filterDataMessage: "El catálogo todavía no tiene datos suficientes para aplicar ese filtro.",
    },
  };

  function applyTheme(theme) {
    const selected = theme === "dark" ? "dark" : "light";
    root.dataset.theme = selected;
    if (!themeButton || !themeIcon) return;
    themeIcon.textContent = selected === "dark" ? "☀" : "☾";
    const label = selected === "dark" ? "Switch to light mode" : "Switch to dark mode";
    themeButton.setAttribute("aria-label", label);
    themeButton.title = label;
  }

  function applyLanguage(language) {
    const selected = language === "es" ? "es" : "en";
    const dictionary = translations[selected];
    root.lang = selected;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      const translation = dictionary?.[element.dataset.i18n];
      if (translation) element.textContent = translation;
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      const translation = dictionary?.[element.dataset.i18nPlaceholder];
      if (translation) element.placeholder = translation;
    });
    document.querySelectorAll("[data-result-kind]").forEach((element) => {
      const count = Number(element.dataset.resultCount || 0);
      const kind = element.dataset.resultKind;
      const key = kind === "exact" ? "countExact" : kind === "closest" ? "countClosest" : "countBrowse";
      const template = dictionary?.[key];
      if (template) element.textContent = template.replace("{count}", String(count));
    });
    document.querySelectorAll("[data-assistant-state]").forEach((element) => {
      const state = element.dataset.assistantState;
      const messageKey = state === "filter-data" ? "filterDataMessage" : `${state}Message`;
      const translation = dictionary?.[messageKey];
      if (translation) element.textContent = translation;
    });
    if (themeButton) {
      const themeLabel = root.dataset.theme === "dark" ? dictionary?.themeLight : dictionary?.themeDark;
      if (themeLabel) {
        themeButton.setAttribute("aria-label", themeLabel);
        themeButton.title = themeLabel;
      }
    }
    if (languageSelect && dictionary?.interfaceLanguage) {
      languageSelect.setAttribute("aria-label", dictionary.interfaceLanguage);
    }
    if (languageSelect) languageSelect.value = selected;
    localStorage.setItem("movie-finder-language", selected);
  }

  const storedTheme = localStorage.getItem("movie-finder-theme");
  applyTheme(storedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  applyLanguage(localStorage.getItem("movie-finder-language") || "en");

  themeButton?.addEventListener("click", () => {
    const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    localStorage.setItem("movie-finder-theme", nextTheme);
  });

  languageSelect?.addEventListener("change", () => applyLanguage(languageSelect.value));
  window.addEventListener("movie-finder:content-updated", () => applyLanguage(languageSelect?.value || "en"));
})();
