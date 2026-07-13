import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory
from yt_dlp import YoutubeDL

_AMS = ZoneInfo("Europe/Amsterdam")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_ROOT, "web")
STATIC_DIR = os.path.join(WEB_DIR, "static")
DATA_DIR = os.path.join(APP_ROOT, "data")
EXPORTS_FILE = os.path.join(DATA_DIR, "exports.json")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)

_exports_lock = threading.Lock()


# =====================================================================================
# Exports opslag
# =====================================================================================

def load_exports():
    if not os.path.exists(EXPORTS_FILE):
        return []
    with open(EXPORTS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_exports(entries):
    with open(EXPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def exported_video_ids():
    return {entry["video_id"] for entry in load_exports()}


# =====================================================================================
# YouTube zoeken via yt-dlp
# =====================================================================================

def format_duration_minutes(seconds):
    if seconds is None:
        return None
    return round(seconds / 60, 1)


def format_upload_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def best_quality_label(entry):
    formats = entry.get("formats") or []
    best_height = None
    for fmt in formats:
        if fmt.get("vcodec") in (None, "none"):
            continue
        height = fmt.get("height")
        if height and (best_height is None or height > best_height):
            best_height = height
    if best_height:
        return f"{best_height}p"
    # fallback op top-level info als er geen formats-lijst is
    height = entry.get("height")
    if height:
        return f"{height}p"
    return entry.get("resolution") or "onbekend"


def _extract_full(url):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def search_youtube(query, max_results, min_duration_minutes=None):
    # Stap 1: goedkope "flat" zoekopdracht (1 request) geeft al duur, titel en
    # uploader terug, zodat we op lengte kunnen filteren zonder elke video
    # individueel te bevragen.
    fetch_count = min(max_results * 5, 200) if min_duration_minutes else max_results

    flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with YoutubeDL(flat_opts) as ydl:
        flat_info = ydl.extract_info(f"ytsearch{fetch_count}:{query}", download=False)

    candidates = []
    for entry in flat_info.get("entries") or []:
        if entry is None:
            continue
        duration_minutes = format_duration_minutes(entry.get("duration"))
        if min_duration_minutes and (duration_minutes is None or duration_minutes < min_duration_minutes):
            continue
        candidates.append(entry)

    # Stap 2: alleen voor de kandidaten die we ook echt nodig hebben (plus een
    # kleine marge voor eventuele mislukte extracties) de volledige metadata
    # (kwaliteit/formats, exacte uploaddatum) parallel ophalen.
    to_extract = candidates[: max_results + 5]

    results = []
    if to_extract:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for entry, full in zip(to_extract, pool.map(lambda e: _extract_full(e["url"]), to_extract)):
                if full is None:
                    continue
                duration_minutes = format_duration_minutes(full.get("duration"))
                if min_duration_minutes and (duration_minutes is None or duration_minutes < min_duration_minutes):
                    continue
                video_id = full.get("id") or entry.get("id")
                results.append(
                    {
                        "video_id": video_id,
                        "title": full.get("title"),
                        "upload_date": format_upload_date(full.get("upload_date")),
                        "uploader": full.get("uploader") or full.get("channel"),
                        "duration_minutes": duration_minutes,
                        "quality": best_quality_label(full),
                        "url": full.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                    }
                )

    return results[:max_results]


# =====================================================================================
# Routes
# =====================================================================================

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Zoekterm is verplicht"}), 400

    try:
        max_results = int(data.get("max_results") or 20)
    except (TypeError, ValueError):
        max_results = 20
    max_results = max(1, min(max_results, 100))

    min_duration_minutes = data.get("min_duration_minutes")
    try:
        min_duration_minutes = float(min_duration_minutes) if min_duration_minutes not in (None, "") else None
    except (TypeError, ValueError):
        min_duration_minutes = None

    try:
        results = search_youtube(query, max_results, min_duration_minutes)
    except Exception as exc:
        return jsonify({"error": f"Zoeken mislukt: {exc}"}), 500

    already_exported = exported_video_ids()
    for r in results:
        r["already_exported"] = r["video_id"] in already_exported

    return jsonify({"results": results})


@app.route("/api/export", methods=["POST"])
def api_export():
    data = request.get_json(force=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "Geen items geselecteerd"}), 400

    exported_at = datetime.now(_AMS).isoformat()

    with _exports_lock:
        entries = load_exports()
        for item in items:
            entries.append(
                {
                    "video_id": item.get("video_id"),
                    "title": item.get("title"),
                    "upload_date": item.get("upload_date"),
                    "uploader": item.get("uploader"),
                    "duration_minutes": item.get("duration_minutes"),
                    "quality": item.get("quality"),
                    "url": item.get("url"),
                    "exported_at": exported_at,
                }
            )
        save_exports(entries)

    return jsonify({"exported_count": len(items), "exported_at": exported_at})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
