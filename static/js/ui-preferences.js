(() => {
  const root = document.documentElement;
  const themeButton = document.querySelector("#theme-toggle");
  const themeIcon = themeButton?.querySelector(".theme-toggle__icon");
  const languageSelect = document.querySelector("#ui-language");
  const translations = {
    en: {
      pageTitle: "Movie Finder — by Mood & Description", brand: "Movie Finder",
      headerSubtitle: "Search by description or browse movies by genre.", interfaceLanguage: "Interface and movie language",
      searchRegion: "Movie search", moviesRegion: "Movies", findLabel: "Find a movie",
      searchPlaceholder: "e.g. I want something suspenseful but not horror, preferably a mystery under two hours.",
      findButton: "Find Movies", genreLabel: "Genre", allGenres: "All genres", recognitionLabel: "Recognition",
      allFilms: "All films", awardWinners: "Major award winners", perPageLabel: "Per page",
      allLanguages: "All movie languages", recommendedMovies: "Recommended Movies", allMovies: "All Movies",
      countExact: "{count} exact matches", countClosest: "{count} closest matches", countBrowse: "{count} matches",
      exactMatch: "Exact match", closestMatch: "Closest match", unverified: "Unverified",
      partialMessage: "I couldn't find an exact match, but here are some close options. Some details cannot be confirmed from this catalog.",
      emptyMessage: "I couldn't find a close fit. Try a genre, mood, actor, or short plot clue; the suggestions below are a good starting point.",
      exactMessage: "I found movies that fit what you asked for.", filterDataMessage: "This catalog doesn't include data for that language yet, so I can't filter by it. Showing all available movies instead.",
      emptyStateTitle: "No movies found.", emptyStateBody: "Try a different search or choose another genre.",
      themeLight: "Switch to light mode", themeDark: "Switch to dark mode", previousPage: "Previous", nextPage: "Next",
      posterAlt: "Poster for {title}", contentRating: "Age rating", originLabel: "Origin", originUnavailable: "Origin unavailable",
      whyMatched: "Why this matched", relevant: "Relevant", notRelevant: "Not relevant", feedbackSaved: "Feedback saved. Thanks.",
      searchingButton: "Searching...", searchingStatus: "Searching movies...", searchSlow: "Search took too long. Please try again.",
      footerAttribution: "This product uses the TMDB API but is not endorsed or certified by TMDB.", commonsCredit: "Poster image source: Wikimedia Commons",
      queryDebug: "Query Debug", userQuery: "User query", normalized: "Normalized", detectedIntent: "Detected intent",
      detectedCountry: "Detected country", detectedGenre: "Detected genre", detectedYear: "Detected year",
      negativeFilters: "Negative filters", negativeContent: "Negative content", unsupportedMetadata: "Unsupported metadata",
      semanticDescription: "Semantic description", ranking: "Ranking", structuredQuery: "Structured Query",
      filterResults: "Filter Results", noExplicitFilters: "No explicit metadata filters detected.",
      genres: {
        action: "Action", adventure: "Adventure", adult: "Adult", animation: "Animation", biography: "Biography",
        comedy: "Comedy", crime: "Crime", documentary: "Documentary", drama: "Drama", family: "Family",
        fantasy: "Fantasy", "game-show": "Game Show", history: "History", horror: "Horror", music: "Music",
        musical: "Musical", mystery: "Mystery", news: "News", "reality-tv": "Reality TV", romance: "Romance",
        "sci-fi": "Sci-Fi", sport: "Sport", "talk-show": "Talk Show", thriller: "Thriller", war: "War", western: "Western",
      },
    },
    es: {
      pageTitle: "Buscador de películas — por género y descripción", brand: "Buscador de películas",
      headerSubtitle: "Busca por descripción o explora películas por género.", interfaceLanguage: "Idioma de la interfaz y de las películas",
      searchRegion: "Búsqueda de películas", moviesRegion: "Películas", findLabel: "Encuentra una película",
      searchPlaceholder: "Ej.: una película de misterio tensa, pero no de terror, de menos de dos horas.",
      findButton: "Buscar películas", genreLabel: "Género", allGenres: "Todos los géneros", recognitionLabel: "Reconocimiento",
      allFilms: "Todas las películas", awardWinners: "Películas con premios importantes", perPageLabel: "Por página",
      allLanguages: "Todos los idiomas de películas", recommendedMovies: "Películas recomendadas", allMovies: "Todas las películas",
      countExact: "{count} coincidencias exactas", countClosest: "{count} opciones cercanas", countBrowse: "{count} películas",
      exactMatch: "Coincidencia exacta", closestMatch: "Opción cercana", unverified: "Sin verificar",
      partialMessage: "No encontré una coincidencia exacta, pero aquí tienes opciones cercanas. No se pueden confirmar algunos detalles con este catálogo.",
      emptyMessage: "No encontré una opción cercana. Prueba con un género, estado de ánimo, actor o una pista breve de la trama; las sugerencias pueden ayudarte.",
      exactMessage: "Encontré películas que coinciden con lo que pediste.", filterDataMessage: "Este catálogo aún no incluye datos de ese idioma, así que no puedo filtrar por él. Muestro todas las películas disponibles.",
      emptyStateTitle: "No se encontraron películas.", emptyStateBody: "Prueba otra búsqueda o elige un género distinto.",
      themeLight: "Cambiar a tema claro", themeDark: "Cambiar a tema oscuro", previousPage: "Anterior", nextPage: "Siguiente",
      posterAlt: "Póster de {title}", contentRating: "Clasificación", originLabel: "Origen", originUnavailable: "Origen no disponible",
      whyMatched: "Por qué coincide", relevant: "Relevante", notRelevant: "No relevante", feedbackSaved: "Opinión guardada. Gracias.",
      searchingButton: "Buscando...", searchingStatus: "Buscando películas...", searchSlow: "La búsqueda tardó demasiado. Inténtalo de nuevo.",
      footerAttribution: "Este producto utiliza la API de TMDB, pero TMDB no lo respalda ni certifica.", commonsCredit: "Fuente del póster: Wikimedia Commons",
      queryDebug: "Análisis de búsqueda", userQuery: "Búsqueda", normalized: "Normalizada", detectedIntent: "Intención detectada",
      detectedCountry: "País detectado", detectedGenre: "Género detectado", detectedYear: "Año detectado",
      negativeFilters: "Filtros excluidos", negativeContent: "Contenido excluido", unsupportedMetadata: "Datos no disponibles",
      semanticDescription: "Descripción semántica", ranking: "Orden", structuredQuery: "Búsqueda estructurada",
      filterResults: "Resultados de filtros", noExplicitFilters: "No se detectaron filtros de metadatos.",
      genres: {
        action: "Acción", adventure: "Aventura", adult: "Adultos", animation: "Animación", biography: "Biografía",
        comedy: "Comedia", crime: "Crimen", documentary: "Documental", drama: "Drama", family: "Familia",
        fantasy: "Fantasía", "game-show": "Concursos", history: "Historia", horror: "Terror", music: "Música",
        musical: "Musical", mystery: "Misterio", news: "Noticias", "reality-tv": "Telerrealidad", romance: "Romance",
        "sci-fi": "Ciencia ficción", sport: "Deportes", "talk-show": "Entrevistas", thriller: "Suspense", war: "Guerra", western: "Western",
      },
    },
    tl: {
      pageTitle: "Tagahanap ng Pelikula", brand: "Tagahanap ng Pelikula",
      headerSubtitle: "Maghanap gamit ang paglalarawan o pumili ng genre.", interfaceLanguage: "Wika ng interface at pelikula",
      searchRegion: "Paghahanap ng pelikula", moviesRegion: "Mga pelikula", findLabel: "Maghanap ng pelikula",
      searchPlaceholder: "Hal.: nakakaabang na misteryo pero hindi horror, wala pang dalawang oras.",
      findButton: "Maghanap", genreLabel: "Genre", allGenres: "Lahat ng genre", recognitionLabel: "Pagkilala",
      allFilms: "Lahat ng pelikula", awardWinners: "Mga pelikulang may pangunahing parangal", perPageLabel: "Bawat pahina",
      allLanguages: "Lahat ng wika ng pelikula", recommendedMovies: "Mga inirerekomendang pelikula", allMovies: "Lahat ng pelikula",
      countExact: "{count} eksaktong tugma", countClosest: "{count} pinakamalapit na pelikula", countBrowse: "{count} pelikula",
      exactMatch: "Eksaktong tugma", closestMatch: "Pinakamalapit", unverified: "Hindi pa napatutunayan",
      partialMessage: "Walang eksaktong tugma, pero may malalapit na pelikula. May mga detalyeng hindi makumpirma sa katalogong ito.",
      emptyMessage: "Walang malapit na tugma. Subukan ang genre, mood, artista, o maikling pahiwatig sa kuwento.",
      exactMessage: "May nahanap akong mga pelikulang tugma sa hiniling mo.", filterDataMessage: "Wala pang datos ang katalogo para sa wikang ito. Ipinapakita ko muna ang lahat ng pelikulang mayroon.",
      emptyStateTitle: "Walang nahanap na pelikula.", emptyStateBody: "Subukan ang ibang paghahanap o pumili ng ibang genre.",
      themeLight: "Gawing maliwanag ang tema", themeDark: "Gawing madilim ang tema", previousPage: "Nakaraan", nextPage: "Susunod",
      posterAlt: "Poster ng {title}", contentRating: "Rating ng edad", originLabel: "Pinagmulan", originUnavailable: "Walang datos ng pinagmulan",
      whyMatched: "Bakit tumugma", relevant: "May kaugnayan", notRelevant: "Hindi nauugnay", feedbackSaved: "Nai-save ang feedback. Salamat.",
      searchingButton: "Naghahanap...", searchingStatus: "Naghahanap ng mga pelikula...", searchSlow: "Masyadong matagal ang paghahanap. Subukan muli.",
      footerAttribution: "Gumagamit ang produktong ito ng TMDB API ngunit hindi ito ineendorso o sertipikado ng TMDB.", commonsCredit: "Pinagmulan ng poster: Wikimedia Commons",
      queryDebug: "Pagsusuri ng query", userQuery: "Query", normalized: "Inayos", detectedIntent: "Layuning natukoy",
      detectedCountry: "Bansang natukoy", detectedGenre: "Genre na natukoy", detectedYear: "Taong natukoy",
      negativeFilters: "Mga hindi isinama", negativeContent: "Hindi gustong nilalaman", unsupportedMetadata: "Walang datos",
      semanticDescription: "Paglalarawan", ranking: "Pagkakasunod-sunod", structuredQuery: "Nakaayos na query",
      filterResults: "Resulta ng filter", noExplicitFilters: "Walang natukoy na metadata filter.",
      genres: {
        action: "Aksyon", adventure: "Pakikipagsapalaran", adult: "Pangmatanda", animation: "Animasyon", biography: "Talambuhay",
        comedy: "Komediya", crime: "Krimen", documentary: "Dokumentaryo", drama: "Drama", family: "Pamilya",
        fantasy: "Pantasya", "game-show": "Palabas-paligsahan", history: "Kasaysayan", horror: "Katatakutan", music: "Musika",
        musical: "Musikal", mystery: "Misteryo", news: "Balita", "reality-tv": "Reality TV", romance: "Romansa",
        "sci-fi": "Agham-piksyon", sport: "Palakasan", "talk-show": "Usapang palabas", thriller: "Kilig at kaba", war: "Digmaan", western: "Kanluranin",
      },
    },
    fr: {
      pageTitle: "Recherche de films", brand: "Recherche de films", headerSubtitle: "Recherchez par description ou par genre.",
      interfaceLanguage: "Langue de l’interface et des films", searchRegion: "Recherche de films", moviesRegion: "Films",
      findLabel: "Trouver un film", searchPlaceholder: "Ex. : un mystère prenant, sans horreur, de moins de deux heures.", findButton: "Rechercher",
      genreLabel: "Genre", allGenres: "Tous les genres", recognitionLabel: "Distinctions", allFilms: "Tous les films",
      awardWinners: "Films primés", perPageLabel: "Par page", allLanguages: "Toutes les langues des films",
      recommendedMovies: "Films recommandés", allMovies: "Tous les films", countExact: "{count} correspondances exactes",
      countClosest: "{count} options proches", countBrowse: "{count} films", exactMatch: "Correspondance exacte",
      closestMatch: "Option proche", unverified: "Non vérifié", partialMessage: "Aucune correspondance exacte, mais voici des options proches. Certains détails ne sont pas vérifiables dans ce catalogue.",
      emptyMessage: "Aucun résultat proche. Essayez un genre, une ambiance, un acteur ou un bref indice d’intrigue.",
      exactMessage: "J’ai trouvé des films qui correspondent à votre demande.", filterDataMessage: "Le catalogue ne contient pas encore de données pour cette langue. Toutes les langues disponibles sont affichées.",
      emptyStateTitle: "Aucun film trouvé.", emptyStateBody: "Essayez une autre recherche ou un autre genre.",
      themeLight: "Activer le thème clair", themeDark: "Activer le thème sombre", previousPage: "Précédent", nextPage: "Suivant",
      posterAlt: "Affiche de {title}", contentRating: "Classification", originLabel: "Origine", originUnavailable: "Origine indisponible",
      whyMatched: "Pourquoi ce résultat", relevant: "Pertinent", notRelevant: "Non pertinent", feedbackSaved: "Avis enregistré. Merci.",
      searchingButton: "Recherche...", searchingStatus: "Recherche de films...", searchSlow: "La recherche a pris trop de temps. Réessayez.",
      footerAttribution: "Ce produit utilise l’API TMDB, sans être approuvé ni certifié par TMDB.", commonsCredit: "Source de l’affiche : Wikimedia Commons",
      queryDebug: "Analyse de la recherche", userQuery: "Recherche", normalized: "Normalisée", detectedIntent: "Intention détectée",
      detectedCountry: "Pays détecté", detectedGenre: "Genre détecté", detectedYear: "Année détectée", negativeFilters: "Exclusions",
      negativeContent: "Contenu exclu", unsupportedMetadata: "Données indisponibles", semanticDescription: "Description sémantique",
      ranking: "Classement", structuredQuery: "Recherche structurée", filterResults: "Résultats des filtres", noExplicitFilters: "Aucun filtre de métadonnées détecté.",
      genres: { action:"Action",adventure:"Aventure",adult:"Adulte",animation:"Animation",biography:"Biographie",comedy:"Comédie",crime:"Crime",documentary:"Documentaire",drama:"Drame",family:"Famille",fantasy:"Fantastique","game-show":"Jeu télévisé",history:"Histoire",horror:"Horreur",music:"Musique",musical:"Comédie musicale",mystery:"Mystère",news:"Actualités","reality-tv":"Télé-réalité",romance:"Romance","sci-fi":"Science-fiction",sport:"Sport","talk-show":"Talk-show",thriller:"Thriller",war:"Guerre",western:"Western" },
    },
    de: {
      pageTitle: "Filmsuche", brand: "Filmsuche", headerSubtitle: "Suche nach Beschreibung oder Genre.", interfaceLanguage: "Sprache für Oberfläche und Filme",
      searchRegion: "Filmsuche", moviesRegion: "Filme", findLabel: "Film finden", searchPlaceholder: "Zum Beispiel: ein spannender Krimi ohne Horror, kürzer als zwei Stunden.",
      findButton: "Filme suchen", genreLabel: "Genre", allGenres: "Alle Genres", recognitionLabel: "Auszeichnungen", allFilms: "Alle Filme",
      awardWinners: "Wichtige Preisträger", perPageLabel: "Pro Seite", allLanguages: "Alle Filmsprachen",
      recommendedMovies: "Filmempfehlungen", allMovies: "Alle Filme", countExact: "{count} genaue Treffer", countClosest: "{count} ähnliche Treffer",
      countBrowse: "{count} Treffer", exactMatch: "Genaue Übereinstimmung", closestMatch: "Ähnliche Option", unverified: "Nicht bestätigt",
      partialMessage: "Kein genauer Treffer, aber hier sind ähnliche Filme. Einige Details lassen sich mit diesem Katalog nicht bestätigen.",
      emptyMessage: "Keine passenden Filme gefunden. Probiere ein Genre, eine Stimmung, einen Schauspieler oder einen kurzen Handlungshinweis.",
      exactMessage: "Ich habe Filme gefunden, die zu deiner Suche passen.", filterDataMessage: "Für diese Sprache enthält der Katalog noch keine Daten. Es werden alle verfügbaren Filme angezeigt.",
      emptyStateTitle: "Keine Filme gefunden.", emptyStateBody: "Versuche eine andere Suche oder ein anderes Genre.",
      themeLight: "Helles Design aktivieren", themeDark: "Dunkles Design aktivieren", previousPage: "Zurück", nextPage: "Weiter",
      posterAlt: "Filmplakat für {title}", contentRating: "Altersfreigabe", originLabel: "Herkunft", originUnavailable: "Herkunft nicht verfügbar",
      whyMatched: "Warum dieser Treffer", relevant: "Relevant", notRelevant: "Nicht relevant", feedbackSaved: "Feedback gespeichert. Danke.",
      searchingButton: "Suche läuft...", searchingStatus: "Filme werden gesucht...", searchSlow: "Die Suche dauerte zu lange. Bitte erneut versuchen.",
      footerAttribution: "Dieses Produkt nutzt die TMDB-API, wird aber nicht von TMDB unterstützt oder zertifiziert.", commonsCredit: "Posterquelle: Wikimedia Commons",
      queryDebug: "Suchanalyse", userQuery: "Suchanfrage", normalized: "Normalisiert", detectedIntent: "Erkannte Absicht", detectedCountry: "Erkanntes Land",
      detectedGenre: "Erkanntes Genre", detectedYear: "Erkanntes Jahr", negativeFilters: "Ausschlüsse", negativeContent: "Ausgeschlossene Inhalte",
      unsupportedMetadata: "Keine Metadaten", semanticDescription: "Inhaltsbeschreibung", ranking: "Sortierung", structuredQuery: "Strukturierte Suche",
      filterResults: "Filterergebnisse", noExplicitFilters: "Keine Metadatenfilter erkannt.",
      genres: { action:"Action",adventure:"Abenteuer",adult:"Erwachsene",animation:"Animation",biography:"Biografie",comedy:"Komödie",crime:"Krimi",documentary:"Dokumentation",drama:"Drama",family:"Familie",fantasy:"Fantasy","game-show":"Spielshow",history:"Geschichte",horror:"Horror",music:"Musik",musical:"Musical",mystery:"Mystery",news:"Nachrichten","reality-tv":"Reality-TV",romance:"Romanze","sci-fi":"Science-Fiction",sport:"Sport","talk-show":"Talkshow",thriller:"Thriller",war:"Krieg",western:"Western" },
    },
    pt: {
      pageTitle: "Busca de filmes", brand: "Busca de filmes", headerSubtitle: "Busque por descrição ou explore por gênero.", interfaceLanguage: "Idioma da interface e dos filmes",
      searchRegion: "Busca de filmes", moviesRegion: "Filmes", findLabel: "Encontrar um filme", searchPlaceholder: "Ex.: um mistério envolvente, sem terror e com menos de duas horas.",
      findButton: "Buscar filmes", genreLabel: "Gênero", allGenres: "Todos os gêneros", recognitionLabel: "Prêmios", allFilms: "Todos os filmes",
      awardWinners: "Vencedores de grandes prêmios", perPageLabel: "Por página", allLanguages: "Todos os idiomas de filmes",
      recommendedMovies: "Filmes recomendados", allMovies: "Todos os filmes", countExact: "{count} correspondências exatas", countClosest: "{count} opções próximas",
      countBrowse: "{count} filmes", exactMatch: "Correspondência exata", closestMatch: "Opção próxima", unverified: "Não verificado",
      partialMessage: "Não encontrei uma correspondência exata, mas estas opções são próximas. Alguns detalhes não podem ser confirmados neste catálogo.",
      emptyMessage: "Não encontrei uma opção próxima. Tente um gênero, clima, ator ou uma breve pista da trama.",
      exactMessage: "Encontrei filmes que correspondem ao que você pediu.", filterDataMessage: "O catálogo ainda não tem dados para esse idioma. Mostrando todos os filmes disponíveis.",
      emptyStateTitle: "Nenhum filme encontrado.", emptyStateBody: "Tente outra busca ou escolha outro gênero.",
      themeLight: "Ativar tema claro", themeDark: "Ativar tema escuro", previousPage: "Anterior", nextPage: "Próxima",
      posterAlt: "Pôster de {title}", contentRating: "Classificação etária", originLabel: "Origem", originUnavailable: "Origem indisponível",
      whyMatched: "Por que este resultado", relevant: "Relevante", notRelevant: "Irrelevante", feedbackSaved: "Opinião salva. Obrigado.",
      searchingButton: "Buscando...", searchingStatus: "Buscando filmes...", searchSlow: "A busca demorou demais. Tente novamente.",
      footerAttribution: "Este produto usa a API TMDB, mas não é endossado nem certificado pela TMDB.", commonsCredit: "Fonte do pôster: Wikimedia Commons",
      queryDebug: "Análise da busca", userQuery: "Busca", normalized: "Normalizada", detectedIntent: "Intenção detectada", detectedCountry: "País detectado",
      detectedGenre: "Gênero detectado", detectedYear: "Ano detectado", negativeFilters: "Exclusões", negativeContent: "Conteúdo excluído",
      unsupportedMetadata: "Dados indisponíveis", semanticDescription: "Descrição semântica", ranking: "Classificação", structuredQuery: "Busca estruturada",
      filterResults: "Resultados dos filtros", noExplicitFilters: "Nenhum filtro de metadados detectado.",
      genres: { action:"Ação",adventure:"Aventura",adult:"Adulto",animation:"Animação",biography:"Biografia",comedy:"Comédia",crime:"Crime",documentary:"Documentário",drama:"Drama",family:"Família",fantasy:"Fantasia","game-show":"Programa de jogos",history:"História",horror:"Terror",music:"Música",musical:"Musical",mystery:"Mistério",news:"Notícias","reality-tv":"Reality show",romance:"Romance","sci-fi":"Ficção científica",sport:"Esporte","talk-show":"Talk show",thriller:"Suspense",war:"Guerra",western:"Faroeste" },
    },
    ja: {
      pageTitle: "映画ファインダー", brand: "映画ファインダー", headerSubtitle: "説明やジャンルから映画を検索できます。", interfaceLanguage: "画面と映画の言語",
      searchRegion: "映画検索", moviesRegion: "映画", findLabel: "映画を探す", searchPlaceholder: "例：ホラーではない、2時間以内の緊張感あるミステリー",
      findButton: "映画を検索", genreLabel: "ジャンル", allGenres: "すべてのジャンル", recognitionLabel: "受賞歴", allFilms: "すべての映画",
      awardWinners: "主要な受賞作", perPageLabel: "1ページあたり", allLanguages: "すべての映画の言語",
      recommendedMovies: "おすすめ映画", allMovies: "すべての映画", countExact: "完全一致 {count} 件", countClosest: "近い候補 {count} 件",
      countBrowse: "{count} 件", exactMatch: "完全一致", closestMatch: "近い候補", unverified: "未確認",
      partialMessage: "完全に一致する作品はありませんが、近い候補を表示します。一部の情報はこのカタログでは確認できません。",
      emptyMessage: "近い作品が見つかりません。ジャンル、雰囲気、俳優、短いあらすじを指定してください。",
      exactMessage: "ご希望に合う映画が見つかりました。", filterDataMessage: "この言語のデータはまだありません。利用可能な映画をすべて表示しています。",
      emptyStateTitle: "映画が見つかりません。", emptyStateBody: "別の検索語句かジャンルをお試しください。",
      themeLight: "ライトテーマに切り替え", themeDark: "ダークテーマに切り替え", previousPage: "前へ", nextPage: "次へ",
      posterAlt: "{title}のポスター", contentRating: "年齢区分", originLabel: "製作国", originUnavailable: "製作国情報なし",
      whyMatched: "一致した理由", relevant: "関連あり", notRelevant: "関連なし", feedbackSaved: "フィードバックを保存しました。ありがとうございます。",
      searchingButton: "検索中...", searchingStatus: "映画を検索しています...", searchSlow: "検索に時間がかかりすぎました。もう一度お試しください。",
      footerAttribution: "本製品はTMDB APIを使用していますが、TMDBによる推奨・認定を受けていません。", commonsCredit: "ポスター画像：Wikimedia Commons",
      queryDebug: "検索の解析", userQuery: "検索語句", normalized: "正規化", detectedIntent: "検出した意図", detectedCountry: "検出した国",
      detectedGenre: "検出したジャンル", detectedYear: "検出した年", negativeFilters: "除外条件", negativeContent: "除外コンテンツ",
      unsupportedMetadata: "データなし", semanticDescription: "内容の説明", ranking: "並び順", structuredQuery: "構造化検索",
      filterResults: "フィルター結果", noExplicitFilters: "メタデータ条件はありません。",
      genres: { action:"アクション",adventure:"アドベンチャー",adult:"成人向け",animation:"アニメーション",biography:"伝記",comedy:"コメディ",crime:"犯罪",documentary:"ドキュメンタリー",drama:"ドラマ",family:"ファミリー",fantasy:"ファンタジー","game-show":"ゲーム番組",history:"歴史",horror:"ホラー",music:"音楽",musical:"ミュージカル",mystery:"ミステリー",news:"ニュース","reality-tv":"リアリティ番組",romance:"ロマンス","sci-fi":"SF",sport:"スポーツ","talk-show":"トーク番組",thriller:"スリラー",war:"戦争",western:"西部劇" },
    },
    ko: {
      pageTitle: "영화 찾기", brand: "영화 찾기", headerSubtitle: "설명이나 장르로 영화를 검색하세요.", interfaceLanguage: "인터페이스 및 영화 언어",
      searchRegion: "영화 검색", moviesRegion: "영화", findLabel: "영화 찾기", searchPlaceholder: "예: 공포가 아닌 긴장감 있는 미스터리, 2시간 미만",
      findButton: "영화 검색", genreLabel: "장르", allGenres: "모든 장르", recognitionLabel: "수상", allFilms: "모든 영화",
      awardWinners: "주요 수상작", perPageLabel: "페이지당", allLanguages: "모든 영화 언어",
      recommendedMovies: "추천 영화", allMovies: "모든 영화", countExact: "정확히 일치하는 영화 {count}편", countClosest: "가까운 영화 {count}편",
      countBrowse: "영화 {count}편", exactMatch: "정확히 일치", closestMatch: "가까운 추천", unverified: "확인되지 않음",
      partialMessage: "정확히 일치하는 영화는 없지만 가까운 작품을 찾았습니다. 일부 정보는 카탈로그에서 확인할 수 없습니다.",
      emptyMessage: "가까운 영화를 찾지 못했습니다. 장르, 분위기, 배우 또는 짧은 줄거리를 입력해 보세요.",
      exactMessage: "요청에 맞는 영화를 찾았습니다.", filterDataMessage: "이 언어의 카탈로그 정보가 아직 없습니다. 이용 가능한 영화를 모두 표시합니다.",
      emptyStateTitle: "영화를 찾지 못했습니다.", emptyStateBody: "다른 검색어나 장르를 선택해 보세요.",
      themeLight: "라이트 모드로 전환", themeDark: "다크 모드로 전환", previousPage: "이전", nextPage: "다음",
      posterAlt: "{title} 포스터", contentRating: "관람 등급", originLabel: "제작 국가", originUnavailable: "제작 국가 정보 없음",
      whyMatched: "일치한 이유", relevant: "관련 있음", notRelevant: "관련 없음", feedbackSaved: "의견이 저장되었습니다. 감사합니다.",
      searchingButton: "검색 중...", searchingStatus: "영화를 검색하고 있습니다...", searchSlow: "검색 시간이 너무 오래 걸립니다. 다시 시도하세요.",
      footerAttribution: "이 제품은 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받은 것은 아닙니다.", commonsCredit: "포스터 이미지 출처: Wikimedia Commons",
      queryDebug: "검색 분석", userQuery: "검색어", normalized: "정규화", detectedIntent: "감지된 의도", detectedCountry: "감지된 국가",
      detectedGenre: "감지된 장르", detectedYear: "감지된 연도", negativeFilters: "제외 조건", negativeContent: "제외 콘텐츠",
      unsupportedMetadata: "정보 없음", semanticDescription: "내용 설명", ranking: "정렬", structuredQuery: "구조화 검색",
      filterResults: "필터 결과", noExplicitFilters: "메타데이터 필터가 없습니다.",
      genres: { action:"액션",adventure:"모험",adult:"성인",animation:"애니메이션",biography:"전기",comedy:"코미디",crime:"범죄",documentary:"다큐멘터리",drama:"드라마",family:"가족",fantasy:"판타지","game-show":"게임 쇼",history:"역사",horror:"공포",music:"음악",musical:"뮤지컬",mystery:"미스터리",news:"뉴스","reality-tv":"리얼리티 TV",romance:"로맨스","sci-fi":"SF",sport:"스포츠","talk-show":"토크 쇼",thriller:"스릴러",war:"전쟁",western:"서부극" },
    },
    zh: {
      pageTitle: "电影搜索", brand: "电影搜索", headerSubtitle: "按描述搜索，或按类型浏览电影。", interfaceLanguage: "界面与电影语言",
      searchRegion: "电影搜索", moviesRegion: "电影", findLabel: "查找电影", searchPlaceholder: "例如：一部紧张但非恐怖、时长少于两小时的悬疑片",
      findButton: "搜索电影", genreLabel: "类型", allGenres: "所有类型", recognitionLabel: "奖项", allFilms: "所有电影",
      awardWinners: "重要奖项获奖电影", perPageLabel: "每页数量", allLanguages: "所有电影语言",
      recommendedMovies: "推荐电影", allMovies: "所有电影", countExact: "{count} 部完全匹配", countClosest: "{count} 部相近电影",
      countBrowse: "{count} 部电影", exactMatch: "完全匹配", closestMatch: "相近推荐", unverified: "未验证",
      partialMessage: "没有完全匹配的电影，但找到了相近选项。此目录无法确认部分信息。", emptyMessage: "没有找到相近电影。试试输入类型、氛围、演员或简短剧情线索。",
      exactMessage: "找到了符合要求的电影。", filterDataMessage: "目录暂时没有该语言的数据，现显示所有可用电影。",
      emptyStateTitle: "未找到电影。", emptyStateBody: "请尝试其他搜索词或选择其他类型。",
      themeLight: "切换到浅色主题", themeDark: "切换到深色主题", previousPage: "上一页", nextPage: "下一页",
      posterAlt: "《{title}》海报", contentRating: "年龄分级", originLabel: "制片国家/地区", originUnavailable: "暂无制片国家/地区信息",
      whyMatched: "匹配原因", relevant: "相关", notRelevant: "不相关", feedbackSaved: "反馈已保存，谢谢。",
      searchingButton: "正在搜索...", searchingStatus: "正在搜索电影...", searchSlow: "搜索耗时过长，请重试。",
      footerAttribution: "本产品使用 TMDB API，但未获 TMDB 认可或认证。", commonsCredit: "海报图片来源：Wikimedia Commons",
      queryDebug: "搜索解析", userQuery: "搜索内容", normalized: "标准化", detectedIntent: "识别意图", detectedCountry: "识别国家",
      detectedGenre: "识别类型", detectedYear: "识别年份", negativeFilters: "排除条件", negativeContent: "排除内容",
      unsupportedMetadata: "暂无数据", semanticDescription: "语义描述", ranking: "排序", structuredQuery: "结构化搜索",
      filterResults: "筛选结果", noExplicitFilters: "未检测到明确的元数据筛选条件。",
      genres: { action:"动作",adventure:"冒险",adult:"成人",animation:"动画",biography:"传记",comedy:"喜剧",crime:"犯罪",documentary:"纪录片",drama:"剧情",family:"家庭",fantasy:"奇幻","game-show":"游戏节目",history:"历史",horror:"恐怖",music:"音乐",musical:"歌舞片",mystery:"悬疑",news:"新闻","reality-tv":"真人秀",romance:"爱情","sci-fi":"科幻",sport:"体育","talk-show":"访谈节目",thriller:"惊悚",war:"战争",western:"西部" },
    },
    hi: {
      pageTitle: "मूवी फ़ाइंडर", brand: "मूवी फ़ाइंडर", headerSubtitle: "विवरण से खोजें या शैली के अनुसार फ़िल्में देखें।", interfaceLanguage: "इंटरफ़ेस और फ़िल्म की भाषा",
      searchRegion: "फ़िल्म खोज", moviesRegion: "फ़िल्में", findLabel: "फ़िल्म खोजें", searchPlaceholder: "उदाहरण: दो घंटे से कम की रहस्य फ़िल्म, डरावनी नहीं",
      findButton: "फ़िल्में खोजें", genreLabel: "शैली", allGenres: "सभी शैलियाँ", recognitionLabel: "पुरस्कार", allFilms: "सभी फ़िल्में",
      awardWinners: "प्रमुख पुरस्कार विजेता", perPageLabel: "प्रति पृष्ठ", allLanguages: "सभी फ़िल्मों की भाषाएँ",
      recommendedMovies: "सुझाई गई फ़िल्में", allMovies: "सभी फ़िल्में", countExact: "{count} सटीक मेल", countClosest: "{count} क़रीबी विकल्प",
      countBrowse: "{count} फ़िल्में", exactMatch: "सटीक मेल", closestMatch: "क़रीबी विकल्प", unverified: "अपुष्ट",
      partialMessage: "सटीक मेल नहीं मिला, लेकिन कुछ क़रीबी विकल्प हैं। इस सूची में कुछ जानकारी की पुष्टि नहीं हो सकती।",
      emptyMessage: "कोई क़रीबी फ़िल्म नहीं मिली। शैली, मूड, अभिनेता या कहानी का छोटा संकेत आज़माएँ।",
      exactMessage: "आपकी पसंद से मेल खाती फ़िल्में मिलीं।", filterDataMessage: "इस भाषा की जानकारी अभी सूची में नहीं है। सभी उपलब्ध फ़िल्में दिखाई जा रही हैं।",
      emptyStateTitle: "कोई फ़िल्म नहीं मिली।", emptyStateBody: "दूसरी खोज या शैली आज़माएँ।",
      themeLight: "लाइट थीम पर जाएँ", themeDark: "डार्क थीम पर जाएँ", previousPage: "पिछला", nextPage: "अगला",
      posterAlt: "{title} का पोस्टर", contentRating: "आयु रेटिंग", originLabel: "निर्माण देश", originUnavailable: "निर्माण देश की जानकारी उपलब्ध नहीं",
      whyMatched: "यह क्यों मेल खाती है", relevant: "प्रासंगिक", notRelevant: "प्रासंगिक नहीं", feedbackSaved: "प्रतिक्रिया सहेजी गई। धन्यवाद।",
      searchingButton: "खोज जारी है...", searchingStatus: "फ़िल्में खोजी जा रही हैं...", searchSlow: "खोज में बहुत समय लगा। फिर से कोशिश करें।",
      footerAttribution: "यह उत्पाद TMDB API का उपयोग करता है, लेकिन TMDB द्वारा समर्थित या प्रमाणित नहीं है।", commonsCredit: "पोस्टर छवि स्रोत: Wikimedia Commons",
      queryDebug: "खोज विश्लेषण", userQuery: "खोज", normalized: "सामान्यीकृत", detectedIntent: "पहचाना गया आशय", detectedCountry: "पहचाना गया देश",
      detectedGenre: "पहचानी गई शैली", detectedYear: "पहचाना गया वर्ष", negativeFilters: "बहिष्करण", negativeContent: "बहिष्कृत सामग्री",
      unsupportedMetadata: "जानकारी उपलब्ध नहीं", semanticDescription: "विवरण", ranking: "क्रम", structuredQuery: "संरचित खोज",
      filterResults: "फ़िल्टर परिणाम", noExplicitFilters: "कोई मेटाडेटा फ़िल्टर नहीं मिला।",
      genres: { action:"एक्शन",adventure:"साहसिक",adult:"वयस्क",animation:"एनिमेशन",biography:"जीवनी",comedy:"कॉमेडी",crime:"अपराध",documentary:"वृत्तचित्र",drama:"नाटक",family:"पारिवारिक",fantasy:"काल्पनिक","game-show":"खेल कार्यक्रम",history:"इतिहास",horror:"डरावनी",music:"संगीत",musical:"संगीतमय",mystery:"रहस्य",news:"समाचार","reality-tv":"रियलिटी टीवी",romance:"रोमांस","sci-fi":"विज्ञान कथा",sport:"खेल","talk-show":"टॉक शो",thriller:"थ्रिलर",war:"युद्ध",western:"वेस्टर्न" },
    },
  };
  const extraTranslations = {
    en: {
      pageCount: "Page {page} of {total}",
      paginationLabel: "Movie pages",
      originalTitle: "Original title",
      genreFilterConflict: "The selected genre filter overrides the genre in your search.",
      countryFilterConflict: "The country in your search overrides the country filter.",
    },
    es: {
      pageCount: "Página {page} de {total}",
      paginationLabel: "Páginas de películas",
      originalTitle: "Título original",
      genreFilterConflict: "El filtro de género seleccionado tiene prioridad sobre el género de la búsqueda.",
      countryFilterConflict: "El país de la búsqueda tiene prioridad sobre el filtro de país.",
    },
    tl: {
      pageCount: "Pahina {page} ng {total}",
      paginationLabel: "Mga pahina ng pelikula",
      originalTitle: "Orihinal na pamagat",
      genreFilterConflict: "Masusunod ang napiling genre filter kaysa sa genre sa paghahanap.",
      countryFilterConflict: "Masusunod ang bansang nasa paghahanap kaysa sa country filter.",
    },
    fr: {
      pageCount: "Page {page} sur {total}",
      paginationLabel: "Pages des films",
      originalTitle: "Titre original",
      genreFilterConflict: "Le genre sélectionné remplace celui de la recherche.",
      countryFilterConflict: "Le pays de la recherche remplace le filtre de pays.",
    },
    de: {
      pageCount: "Seite {page} von {total}",
      paginationLabel: "Filmseiten",
      originalTitle: "Originaltitel",
      genreFilterConflict: "Das ausgewählte Genre ersetzt das Genre aus der Suche.",
      countryFilterConflict: "Das Land aus der Suche ersetzt den Länderfilter.",
    },
    pt: {
      pageCount: "Página {page} de {total}",
      paginationLabel: "Páginas de filmes",
      originalTitle: "Título original",
      genreFilterConflict: "O gênero selecionado substitui o gênero da busca.",
      countryFilterConflict: "O país da busca substitui o filtro de país.",
    },
    ja: {
      pageCount: "{total} ページ中 {page} ページ", paginationLabel: "映画ページ", originalTitle: "原題", genreFilterConflict: "選択したジャンルが検索のジャンルより優先されます。",
      countryFilterConflict: "検索で指定した国が国フィルターより優先されます。",
    },
    ko: {
      pageCount: "{total}페이지 중 {page}페이지", paginationLabel: "영화 페이지", originalTitle: "원제", genreFilterConflict: "선택한 장르가 검색어의 장르보다 우선합니다。",
      countryFilterConflict: "검색어의 국가가 국가 필터보다 우선합니다。",
    },
    zh: {
      pageCount: "第 {page} 页，共 {total} 页", paginationLabel: "电影分页", originalTitle: "原名", genreFilterConflict: "所选类型优先于搜索中的类型。",
      countryFilterConflict: "搜索中的国家优先于国家筛选条件。",
    },
    hi: {
      pageCount: "पृष्ठ {page} / {total}", paginationLabel: "फ़िल्म पृष्ठ", originalTitle: "मूल शीर्षक", genreFilterConflict: "चुनी गई शैली खोज की शैली पर प्राथमिकता रखती है।",
      countryFilterConflict: "खोज का देश देश फ़िल्टर पर प्राथमिकता रखता है।",
    },
  };
  const textDefaults = new WeakMap();
  const placeholderDefaults = new WeakMap();
  const ariaDefaults = new WeakMap();
  const altDefaults = new WeakMap();

  function applyTheme(theme) {
    const selected = theme === "dark" ? "dark" : "light";
    root.dataset.theme = selected;
    if (!themeButton || !themeIcon) return;
    themeIcon.textContent = selected === "dark" ? "☀" : "☾";
  }

  function applyLanguage(language) {
    const selected = translations[language] ? language : "en";
    const dictionary = translations[selected];
    const translate = (key) => dictionary[key] ?? extraTranslations[selected]?.[key] ?? translations.en[key] ?? extraTranslations.en[key];
    root.lang = selected;
    document.title = dictionary.pageTitle;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      if (!textDefaults.has(element)) textDefaults.set(element, element.textContent);
      const key = element.dataset.i18n;
      element.textContent = translate(key) ?? textDefaults.get(element);
    });
    document.querySelectorAll("[data-i18n-genre]").forEach((element) => {
      if (!textDefaults.has(element)) textDefaults.set(element, element.textContent);
      const genre = element.dataset.i18nGenre;
      element.textContent = dictionary.genres?.[genre] ?? translations.en.genres[genre] ?? textDefaults.get(element);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      if (!placeholderDefaults.has(element)) placeholderDefaults.set(element, element.placeholder);
      const key = element.dataset.i18nPlaceholder;
      element.placeholder = translate(key) ?? placeholderDefaults.get(element);
    });
    document.querySelectorAll("[data-result-kind]").forEach((element) => {
      const count = Number(element.dataset.resultCount || 0);
      const kind = element.dataset.resultKind;
      const key = kind === "exact" ? "countExact" : kind === "closest" ? "countClosest" : "countBrowse";
      element.textContent = translate(key).replace("{count}", String(count));
    });
    document.querySelectorAll("[data-i18n-page]").forEach((element) => {
      element.textContent = translate("pageCount")
        .replace("{page}", element.dataset.currentPage || "1")
        .replace("{total}", element.dataset.totalPages || "1");
    });
    document.querySelectorAll("[data-assistant-state]").forEach((element) => {
      const state = element.dataset.assistantState;
      const messageKey = state === "filter-data" || state === "unmapped" ? "filterDataMessage" : `${state}Message`;
      element.textContent = translate(messageKey);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
      if (!ariaDefaults.has(element)) ariaDefaults.set(element, element.getAttribute("aria-label") || "");
      const key = element.dataset.i18nAria;
      element.setAttribute("aria-label", translate(key) ?? ariaDefaults.get(element));
    });
    document.querySelectorAll("[data-i18n-alt]").forEach((element) => {
      if (!altDefaults.has(element)) altDefaults.set(element, element.alt);
      const key = element.dataset.i18nAlt;
      const title = element.dataset.posterTitle || "";
      element.alt = (translate(key) ?? altDefaults.get(element)).replace("{title}", title);
    });
    if (themeButton) {
      const themeLabel = root.dataset.theme === "dark" ? dictionary.themeLight : dictionary.themeDark;
      themeButton.setAttribute("aria-label", themeLabel);
      themeButton.title = themeLabel;
    }
    if (languageSelect) {
      languageSelect.setAttribute("aria-label", dictionary.interfaceLanguage);
    }
    window.movieFinderText = (key) => translate(key) ?? key;
  }

  const storedTheme = localStorage.getItem("movie-finder-theme");
  applyTheme(storedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  const languageInput = document.querySelector("#movie-language");
  const searchForm = document.querySelector(".search__form");
  const searchButton = searchForm?.querySelector(".search__button");
  const url = new URL(window.location.href);
  const storedLanguage = localStorage.getItem("movie-finder-language");
  let selectedLanguage = url.searchParams.has("language") ? url.searchParams.get("language") : (storedLanguage || "");
  if (languageSelect && !Array.from(languageSelect.options).some((option) => option.value === selectedLanguage)) {
    selectedLanguage = "";
  }
  if (languageSelect) languageSelect.value = selectedLanguage;
  if (languageInput) languageInput.value = selectedLanguage;
  applyLanguage(selectedLanguage || "en");

  if (!url.searchParams.has("language") && storedLanguage && storedLanguage === selectedLanguage && languageInput && searchForm) {
    languageInput.value = storedLanguage;
    searchForm.requestSubmit(searchButton || undefined);
  }

  themeButton?.addEventListener("click", () => {
    const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    localStorage.setItem("movie-finder-theme", nextTheme);
  });

  languageSelect?.addEventListener("change", () => {
    const selection = languageSelect.value;
    if (languageInput) languageInput.value = selection;
    localStorage.setItem("movie-finder-language", selection);
    applyLanguage(selection || "en");
    if (searchForm) searchForm.requestSubmit(searchButton || undefined);
  });
  window.addEventListener("movie-finder:content-updated", () => applyLanguage(languageSelect?.value || "en"));
})();
