# YouTube Clip Finder

Webapp om op YouTube te zoeken naar clips, resultaten te selecteren en de URL's van
geselecteerde clips in bulk te exporteren. Bijhoudt welke clips al eerder
geëxporteerd zijn, en laat je clips permanent verbergen (handmatig of
automatisch bij een mislukte download) zodat ze niet meer terugkomen in
zoekresultaten.

## Architectuur

Zelfde opzet als de `padel`-app: een Flask-backend die een map met statische
HTML/CSS/JS bedient, met data in platte JSON-bestanden op schijf (geen database).

```
server.py              Flask app: routes + yt-dlp zoeklogica + export-/download-opslag
requirements.txt        Flask, yt-dlp
data/
  exports.json          Geschiedenis van alle geëxporteerde clips (append-only)
  downloads.json         Geschiedenis van alle gedownloade clips (append-only)
  hidden.json            Permanent uitgesloten clips (muteerbaar: regels worden
                         verwijderd zodra een clip wordt teruggezet)
web/
  index.html             Paginastructuur (zoekformulier + resultatentabel)
  static/
    style.css             Dark theme, Manrope-font, lime accentkleur (padel-stijl)
    app.js                 Alle frontend-logica (fetch, selectie, export, filter)
```

Geen build-stap, geen frontend-framework: vanilla JS/CSS, rechtstreeks door Flask
geserveerd via `send_from_directory`.

## Backend (`server.py`)

- `GET /` en `GET /static/<file>` — serveren de frontend.
- `POST /api/search` — body `{query, max_results, min_duration_minutes, exclude_exported, exclude_downloaded}`.
  1. Doet eerst een **flat** yt-dlp zoekopdracht (`ytsearchN:query`,
     `extract_flat=True`). Dit is één goedkope HTTP-call en levert al titel,
     kanaal/uploader en duur op — genoeg om direct op minimale lengte (en, als
     `exclude_exported`/`exclude_downloaded` aanstaan, op al-geëxporteerd/
     al-gedownload) te filteren zonder dure per-video calls. Beide uitsluitingen
     zijn onafhankelijk te combineren (`excluded_ids` in `search_youtube()` is de
     unie van beide sets als beide aanstaan).
  2. Deze filtering gebeurt **vóórdat** de resultatenlijst tot `max_results`
     wordt beperkt. Dat is bewust: met `exclude_exported`/`exclude_downloaded`
     wil je bv. bij "volgende 20" ook echt 20 nieuwe clips terugkrijgen, niet 20
     resultaten waarvan er een deel al eerder geëxporteerd/gedownload bleek te
     zijn en wegvalt.
  3. Voert daarna **volledige** yt-dlp-extractie uit (nodig voor `formats`/kwaliteit
     en de exacte uploaddatum), maar alléén voor de kandidaten die de filters
     doorstaan, in batches van `max_results + 5`. Dit gebeurt parallel via een
     `ThreadPoolExecutor` (8 workers) omdat het I/O-bound netwerkcalls zijn, en
     mislukte extracties (video inmiddels verwijderd/privé/regio-geblokkeerd)
     worden overgeslagen in plaats van de hele zoekopdracht te laten falen.
  4. "Kwaliteit" = hoogste beschikbare resolutie uit `formats` (bv. `"1080p"`).
  5. Markeert elk resultaat met `already_exported` en `already_downloaded` door
     `video_id` te vergelijken met alle eerder opgeslagen exports/downloads —
     ook als de bijbehorende uitsluiting aanstond (dan zijn ze allemaal `false`,
     want al weggefilterd), zodat de client-side filters en badges blijven
     werken voor een zoekopdracht zonder die uitsluiting.

  Verborgen clips (`data/hidden.json`, zie hieronder) worden — in tegenstelling
  tot `exclude_exported`/`exclude_downloaded` — **altijd onvoorwaardelijk**
  uitgesloten, zonder dat daar een checkbox voor nodig is: `hidden_video_ids()`
  zit standaard in `excluded_ids` in `search_youtube()`. `needs_buffer` houdt
  daar ook rekening mee (fetcht een grotere batch zodra er verborgen clips
  bestaan), zodat een zoekopdracht na het verbergen van clips niet zomaar
  minder dan `max_results` resultaten teruggeeft.

  Deze tweetraps-aanpak (flat filteren → gericht volledig extraheren) is een
  bewuste keuze: alles in één keer volledig extraheren (zoals de eerste versie
  deed) was met een minimale-lengte-filter en 20 resultaten 60+ seconden traag
  omdat dan tot 4-5x zoveel video's als nodig volledig bevraagd werden.

- `POST /api/export` — body `{items: [...]}`. Append't elk item + een
  `exported_at` ISO-timestamp (Europe/Amsterdam) aan `data/exports.json`. Dit
  bestand is de bron van waarheid voor "al eerder geëxporteerd" — er wordt niet
  gededupliceerd, dus eenzelfde video kan meerdere exportregels hebben als hij
  vaker geëxporteerd wordt (geeft een volledige geschiedenis).

- `POST /api/download` — body is het volledige resultaat-item (`url`,
  `video_id`, `title`, `upload_date`, `uploader`, `duration_minutes`,
  `quality`, ...), niet slechts een kale `url` — de overige velden zijn nodig
  om de clip te kunnen registreren in `data/hidden.json` als de download
  mislukt (zie onder). Downloadt één video via yt-dlp in **H.264/AVC**
  (`H264_FORMAT` in `server.py`), gemuxt naar mp4, en stuurt het bestand terug
  als attachment. Geen transcodering: YouTube levert dit codec meestal al
  native, dus dit is puur downloaden + samenvoegen van de losse video-/
  audiotrack (snel, `ffmpeg -c copy`). Hogere resoluties (>1080p) zijn op
  YouTube vaak alleen in VP9/AV1 beschikbaar, dus de H.264-download kan lager
  uitvallen dan de "kwaliteit" die in de resultatentabel getoond wordt (die
  reflecteert de beste kwaliteit over alle codecs heen). Download gaat naar een
  tijdelijke map (`tempfile.mkdtemp`) die na het versturen van de response
  wordt opgeruimd via `response.call_on_close` (bewust niet `after_this_request`
  — die callback vuurt af vóórdat het bestand daadwerkelijk naar de client is
  gestreamd, wat het bestand voortijdig zou verwijderen). Na een geslaagde
  download wordt de video geregistreerd in `data/downloads.json` (via
  `record_downloads()` + `_info_to_record()`, die de al door yt-dlp opgehaalde
  `info`-dict hergebruikt — geen extra request nodig). Bij een **mislukte**
  download wordt de clip juist automatisch aan `data/hidden.json` toegevoegd
  (via `_item_to_hidden_record()` + `add_hidden()`, met de metadata uit de
  request body) — zo'n clip is doorgaans blijvend niet-downloadbaar (bv. een
  verlopen streaming-URL door YouTube-anti-bot-maatregelen) en hoeft dan ook
  niet in latere zoekresultaten terug te komen.

- `POST /api/download/bulk` — body `{items: [...]}` (volledige resultaat-items,
  om dezelfde reden als bij `/api/download`). Download alle opgegeven video's
  parallel (`ThreadPoolExecutor`, max 3 workers — beperkt om de bandbreedte/CPU
  van de host niet te overbelasten), registreert alleen de **gelukte**
  downloads (met dezelfde `downloaded_at`-timestamp) in `data/downloads.json`,
  zipt de resultaten (`ZIP_STORED`, geen compressie — video is al
  gecomprimeerd) en stuurt de zip terug. Video's die niet meer beschikbaar
  zijn, of waarbij de download een fout gaf (bv. een verlopen streaming-URL
  door YouTube-anti-bot-maatregelen — geeft een `HTTP 403`), worden
  overgeslagen, dus expliciet **niet** in `data/downloads.json` geregistreerd,
  maar **wel** (net als bij de enkele download) automatisch aan
  `data/hidden.json` toegevoegd. Het aantal gelukt/mislukt komt terug in de
  `X-Downloaded-Count`/`X-Failed-Count` response-headers, en de mislukte
  URL's zelf in `X-Failed-Urls` (JSON-array) zodat de UI kan tonen welke
  specifieke clips het niet gehaald hebben. Alleen als **alle** downloads
  mislukken geeft de route een 500-foutmelding (zonder `X-Failed-Urls`, want
  die situatie heeft geen zip-response).

- `POST /api/hide` — body is het volledige resultaat-item. Voegt de clip toe
  aan `data/hidden.json` (met een `hidden_at`-timestamp), tenzij hij al
  verborgen was (`add_hidden()` dedupliceert op `video_id`). Wordt aangeroepen
  door de "Verbergen"-knop per rij.

- `GET /api/hidden` — geeft alle verborgen clips terug (nieuwste eerst, op
  `hidden_at`), voor het "Verborgen clips"-overzicht.

- `POST /api/hide/remove` — body `{video_id}`. Verwijdert de clip weer uit
  `data/hidden.json` (`remove_hidden()`), zodat hij bij een volgende
  zoekopdracht weer gewoon meegenomen wordt. Wordt aangeroepen door de
  "Terugzetten"-knop in het "Verborgen clips"-overzicht.

`data/downloads.json` heeft dezelfde rol voor downloads als `data/exports.json`
voor exports: bron van waarheid voor "al eerder gedownload", gebruikt door
zowel `exclude_downloaded` in `/api/search` als de `already_downloaded`-badge.
`data/hidden.json` is anders dan die twee: geen append-only geschiedenis, maar
een muteerbare set van permanent uitgesloten clips (regels worden echt
verwijderd bij het terugzetten via `remove_hidden()`).

`ffmpeg` is vereist (voor het muxen van losse video-/audiotracks) en zit in het
Docker-image (`apt-get install ffmpeg` in de `Dockerfile`); lokaal via
`brew install ffmpeg`.

Geen YouTube API-key nodig: alle zoek- en metadata-ophaal gebeurt via yt-dlp
(scraping), niet via de officiële YouTube Data API. Dat is kwetsbaarder voor
wijzigingen aan YouTube's website dan een officiële API, maar heeft geen quota
en geeft rijkere metadata (exacte resolutie in plaats van enkel hd/sd).

## Frontend (`web/static/app.js`)

- Eén zoekformulier (zoekterm, aantal resultaten, minimale lengte in minuten) →
  `POST /api/search`, resultaten worden client-side in een tabel gerenderd.
- Selectie: checkbox per rij + "Selecteer alles" (werkt alleen op de *zichtbare*
  rijen, dus met "Verberg geëxporteerde"/"Verberg gedownloade" actief selecteert
  het niet de verborgen rijen).
- Twee onafhankelijke checkboxes "Verberg geëxporteerde" / "Verberg gedownloade"
  filteren de tabel client-side op de `already_exported`/`already_downloaded`-
  velden (los combineerbaar, geen nieuwe serverroundtrip nodig) — niet te
  verwarren met de gelijknamige checkboxes in het zoekformulier
  (`exclude_exported`/`exclude_downloaded`), die vóór het zoeken al filteren
  op de server (zie hierboven).
- Filterbalk boven de tabel (titel, datum van/tot-en-met, uploader, duur
  vanaf/tot, kwaliteit) filtert eveneens volledig client-side op de al
  opgehaalde resultaten (`matchesFilters()` in `app.js`); de kwaliteit-dropdown
  wordt na elke zoekopdracht dynamisch gevuld met de kwaliteiten die in de
  resultatenset voorkomen. Deze filters en de verberg-checkboxes werken samen
  (AND) om te bepalen welke rijen zichtbaar zijn; "Selecteer alles" en de
  export-/downloadknoppen werken alleen op de zichtbare rijen.
- "Bulk export URL's"-knop doet twee dingen bij klikken:
  1. `POST /api/export` met de geselecteerde items → persisteert de
     exportgeschiedenis server-side.
  2. Genereert client-side (via `Blob` + verborgen `<a download>`) een
     `.txt`-bestand met per regel de URL van de geselecteerde clips, en
     start de download in de browser.
  Na afloop worden de betrokken rijen direct in de UI gemarkeerd als
  "eerder geëxporteerd", zonder dat de pagina opnieuw hoeft te zoeken.
- Elke rij heeft een eigen "Download"-knop (`POST /api/download`) die het
  bestand als blob ophaalt en client-side wegschrijft met een bestandsnaam
  opgebouwd uit titel + video-id (dezelfde conventie als de server gebruikt,
  maar hier client-side gereconstrueerd om header-parsing te vermijden). Na een
  geslaagde download wordt de rij direct als "eerder gedownload" gemarkeerd
  (`buildStatusBadges()` toont geëxporteerd/gedownload-badges naast elkaar als
  beide van toepassing zijn).
  "Download geselecteerde (ZIP)" in de toolbar doet hetzelfde voor alle
  geselecteerde rijen ineens via `POST /api/download/bulk`, met een statusregel
  die waarschuwt dat dit lang kan duren (en pulseert in de lime accentkleur
  zolang de download loopt, via de `.neon-pulse`-class). Na afloop wordt de
  `X-Failed-Urls`-header gebruikt om alleen de daadwerkelijk gelukte rijen als
  "eerder gedownload" te markeren (niet de mislukte, ook al waren die wel
  geselecteerd) — als er mislukte clips zijn, verschijnt een pop-up
  (`showFailedModal()`, `#failed-modal` in `index.html`) met per mislukte clip
  de titel en URL. Mislukte downloads (zowel enkel als bulk) worden
  server-side automatisch aan `data/hidden.json` toegevoegd (zie hierboven), dus
  die rijen worden ook meteen client-side uit de huidige resultatentabel
  verwijderd (`row.remove()`) in plaats van enkel gemarkeerd — een nieuwe poging
  via de individuele Download-knop heeft geen zin, want de clip komt toch niet
  meer terug in een zoekopdracht tenzij je hem terugzet via "Verborgen clips".
- Elke rij heeft ook een "Verbergen"-knop (`POST /api/hide`, naast de
  Download-knop in de "Acties"-kolom) om een clip handmatig permanent uit te
  sluiten van toekomstige zoekopdrachten. Na een geslaagde aanroep verdwijnt de
  rij direct uit de tabel (`row.remove()`) — in tegenstelling tot exporteren/
  downloaden is er geen badge nodig, want de clip komt sowieso niet meer terug.
- De navbar-knop "Verborgen clips" (`#hidden-clips-btn`) opent een modal
  (`#hidden-modal`) die `GET /api/hidden` ophaalt en per clip een
  "Terugzetten"-knop toont (`POST /api/hide/remove`). Na het terugzetten
  verdwijnt de regel uit de modal-lijst; de clip wordt vanaf dat moment weer
  gewoon meegenomen in zoekopdrachten. Dit is bewust een modal (zoals
  `#failed-modal`) in plaats van een aparte pagina, om build-stap-vrij te
  blijven.

## Draaien

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

App draait op `http://127.0.0.1:5001`.
