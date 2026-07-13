# YouTube Clip Finder

Webapp om op YouTube te zoeken naar clips, resultaten te selecteren en de URL's van
geselecteerde clips in bulk te exporteren. Bijhoudt welke clips al eerder
geëxporteerd zijn.

## Architectuur

Zelfde opzet als de `padel`-app: een Flask-backend die een map met statische
HTML/CSS/JS bedient, met data in platte JSON-bestanden op schijf (geen database).

```
server.py              Flask app: routes + yt-dlp zoeklogica + export-opslag
requirements.txt        Flask, yt-dlp
data/
  exports.json          Geschiedenis van alle geëxporteerde clips (append-only)
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
- `POST /api/search` — body `{query, max_results, min_duration_minutes, exclude_exported}`.
  1. Doet eerst een **flat** yt-dlp zoekopdracht (`ytsearchN:query`,
     `extract_flat=True`). Dit is één goedkope HTTP-call en levert al titel,
     kanaal/uploader en duur op — genoeg om direct op minimale lengte (en, als
     `exclude_exported` aanstaat, op al-geëxporteerd) te filteren zonder dure
     per-video calls.
  2. Deze filtering gebeurt **vóórdat** de resultatenlijst tot `max_results`
     wordt beperkt. Dat is bewust: met `exclude_exported=true` wil je bv. bij
     "volgende 20" ook echt 20 nieuwe clips terugkrijgen, niet 20 resultaten
     waarvan er een deel al eerder geëxporteerd bleek te zijn en wegvalt.
  3. Voert daarna **volledige** yt-dlp-extractie uit (nodig voor `formats`/kwaliteit
     en de exacte uploaddatum), maar alléén voor de kandidaten die de filters
     doorstaan, in batches van `max_results + 5`. Dit gebeurt parallel via een
     `ThreadPoolExecutor` (8 workers) omdat het I/O-bound netwerkcalls zijn, en
     mislukte extracties (video inmiddels verwijderd/privé/regio-geblokkeerd)
     worden overgeslagen in plaats van de hele zoekopdracht te laten falen.
  4. "Kwaliteit" = hoogste beschikbare resolutie uit `formats` (bv. `"1080p"`).
  5. Markeert elk resultaat met `already_exported` door `video_id` te vergelijken
     met alle eerder opgeslagen exports in `data/exports.json` — ook als
     `exclude_exported` aanstond (dan zijn ze allemaal `false`, want al
     weggefilterd), zodat de client-side "niet geëxporteerd"-toggle en badges
     blijven werken voor een zoekopdracht zonder die uitsluiting.

  Deze tweetraps-aanpak (flat filteren → gericht volledig extraheren) is een
  bewuste keuze: alles in één keer volledig extraheren (zoals de eerste versie
  deed) was met een minimale-lengte-filter en 20 resultaten 60+ seconden traag
  omdat dan tot 4-5x zoveel video's als nodig volledig bevraagd werden.

- `POST /api/export` — body `{items: [...]}`. Append't elk item + een
  `exported_at` ISO-timestamp (Europe/Amsterdam) aan `data/exports.json`. Dit
  bestand is de bron van waarheid voor "al eerder geëxporteerd" — er wordt niet
  gededupliceerd, dus eenzelfde video kan meerdere exportregels hebben als hij
  vaker geëxporteerd wordt (geeft een volledige geschiedenis).

- `POST /api/download` — body `{url}`. Downloadt één video via yt-dlp in
  **H.264/AVC** (`H264_FORMAT` in `server.py`), gemuxt naar mp4, en stuurt het
  bestand terug als attachment. Geen transcodering: YouTube levert dit codec
  meestal al native, dus dit is puur downloaden + samenvoegen van de losse
  video-/audiotrack (snel, `ffmpeg -c copy`). Hogere resoluties (>1080p) zijn op
  YouTube vaak alleen in VP9/AV1 beschikbaar, dus de H.264-download kan lager
  uitvallen dan de "kwaliteit" die in de resultatentabel getoond wordt (die
  reflecteert de beste kwaliteit over alle codecs heen). Download gaat naar een
  tijdelijke map (`tempfile.mkdtemp`) die na het versturen van de response
  wordt opgeruimd via `response.call_on_close` (bewust niet `after_this_request`
  — die callback vuurt af vóórdat het bestand daadwerkelijk naar de client is
  gestreamd, wat het bestand voortijdig zou verwijderen).

- `POST /api/download/bulk` — body `{urls: [...]}`. Download alle opgegeven
  video's parallel (`ThreadPoolExecutor`, max 3 workers — beperkt om de
  bandbreedte/CPU van de host niet te overbelasten), zipt de resultaten
  (`ZIP_STORED`, geen compressie — video is al gecomprimeerd) en stuurt de zip
  terug. Video's die niet meer beschikbaar zijn worden overgeslagen; het aantal
  gelukt/mislukt komt terug in de `X-Downloaded-Count`/`X-Failed-Count`
  response-headers zodat de UI dat kan tonen. Alleen als **alle** downloads
  mislukken geeft de route een 500-foutmelding.

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
  rijen, dus met filter "Niet geëxporteerd" actief selecteert het niet de
  verborgen/al-geëxporteerde rijen).
- Radiogroep "Alle" / "Niet geëxporteerd" filtert de tabel client-side op het
  `already_exported`-veld — geen nieuwe serverroundtrip nodig.
- Filterbalk boven de tabel (titel, datum van/tot-en-met, uploader, duur
  vanaf/tot, kwaliteit) filtert eveneens volledig client-side op de al
  opgehaalde resultaten (`matchesFilters()` in `app.js`); de kwaliteit-dropdown
  wordt na elke zoekopdracht dynamisch gevuld met de kwaliteiten die in de
  resultatenset voorkomen. Deze filters en de "niet geëxporteerd"-radio werken
  samen (AND) om te bepalen welke rijen zichtbaar zijn; "Selecteer alles" en de
  exportknop werken alleen op de zichtbare rijen.
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
  maar hier client-side gereconstrueerd om header-parsing te vermijden).
  "Download geselecteerde (ZIP)" in de toolbar doet hetzelfde voor alle
  geselecteerde rijen ineens via `POST /api/download/bulk`, met een statusregel
  die waarschuwt dat dit lang kan duren en die na afloop meldt hoeveel clips
  gelukt/mislukt zijn.

## Draaien

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

App draait op `http://127.0.0.1:5001`.
