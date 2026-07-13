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
- `POST /api/search` — body `{query, max_results, min_duration_minutes}`.
  1. Doet eerst een **flat** yt-dlp zoekopdracht (`ytsearchN:query`,
     `extract_flat=True`). Dit is één goedkope HTTP-call en levert al titel,
     kanaal/uploader en duur op — genoeg om direct op minimale lengte te filteren
     zonder dure per-video calls.
  2. Voert daarna **volledige** yt-dlp-extractie uit (nodig voor `formats`/kwaliteit
     en de exacte uploaddatum), maar alléén voor de kandidaten die de duurfilter
     doorstaan, en maximaal `max_results + 5` stuks. Dit gebeurt parallel via een
     `ThreadPoolExecutor` (8 workers) omdat het I/O-bound netwerkcalls zijn.
  3. "Kwaliteit" = hoogste beschikbare resolutie uit `formats` (bv. `"1080p"`).
  4. Markeert elk resultaat met `already_exported` door `video_id` te vergelijken
     met alle eerder opgeslagen exports in `data/exports.json`.

  Deze tweetraps-aanpak (flat filteren → gericht volledig extraheren) is een
  bewuste keuze: alles in één keer volledig extraheren (zoals de eerste versie
  deed) was met een minimale-lengte-filter en 20 resultaten 60+ seconden traag
  omdat dan tot 4-5x zoveel video's als nodig volledig bevraagd werden.

- `POST /api/export` — body `{items: [...]}`. Append't elk item + een
  `exported_at` ISO-timestamp (Europe/Amsterdam) aan `data/exports.json`. Dit
  bestand is de bron van waarheid voor "al eerder geëxporteerd" — er wordt niet
  gededupliceerd, dus eenzelfde video kan meerdere exportregels hebben als hij
  vaker geëxporteerd wordt (geeft een volledige geschiedenis).

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

## Draaien

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

App draait op `http://127.0.0.1:5001`.
