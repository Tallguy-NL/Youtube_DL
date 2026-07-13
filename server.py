import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_file, send_from_directory
from yt_dlp import YoutubeDL

_AMS = ZoneInfo("Europe/Amsterdam")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_ROOT, "web")
STATIC_DIR = os.path.join(WEB_DIR, "static")
DATA_DIR = os.path.join(APP_ROOT, "data")
EXPORTS_FILE = os.path.join(DATA_DIR, "exports.json")
DOWNLOADS_FILE = os.path.join(DATA_DIR, "downloads.json")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)

_exports_lock = threading.Lock()
_downloads_lock = threading.Lock()


# =====================================================================================
# Exports/downloads opslag
# =====================================================================================

def _load_json_list(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_json_list(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def load_exports():
    return _load_json_list(EXPORTS_FILE)


def save_exports(entries):
    _save_json_list(EXPORTS_FILE, entries)


def exported_video_ids():
    return {entry["video_id"] for entry in load_exports()}


def load_downloads():
    return _load_json_list(DOWNLOADS_FILE)


def save_downloads(entries):
    _save_json_list(DOWNLOADS_FILE, entries)


def downloaded_video_ids():
    return {entry["video_id"] for entry in load_downloads()}


def record_downloads(records):
    if not records:
        return
    downloaded_at = datetime.now(_AMS).isoformat()
    with _downloads_lock:
        entries = load_downloads()
        entries.extend({**record, "downloaded_at": downloaded_at} for record in records)
        save_downloads(entries)


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
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        # Video kan inmiddels verwijderd/privé/regio-geblokkeerd zijn; gewoon
        # overslaan in plaats van de hele zoekopdracht te laten falen.
        return None


def search_youtube(
    query,
    max_results,
    min_duration_minutes=None,
    exclude_exported=False,
    exclude_downloaded=False,
):
    # Stap 1: goedkope "flat" zoekopdracht (1 request) geeft al duur, titel en
    # uploader terug, zodat we op lengte (en op al-geëxporteerd/gedownload)
    # kunnen filteren zonder elke video individueel te bevragen. Er wordt een
    # buffer bovenop max_results opgevraagd zodat er nog marge is als een deel
    # wordt weggefilterd of inmiddels niet meer beschikbaar blijkt te zijn.
    # Deze filtering gebeurt VOORDAT we tot max_results beperken, zodat je bij
    # "uitsluiten geëxporteerd/gedownload" ook echt max_results NIEUWE clips
    # terugkrijgt.
    needs_buffer = bool(min_duration_minutes) or exclude_exported or exclude_downloaded
    fetch_count = min(max_results * 5, 200) if needs_buffer else min(max_results + 10, 200)

    excluded_ids = set()
    if exclude_exported:
        excluded_ids |= exported_video_ids()
    if exclude_downloaded:
        excluded_ids |= downloaded_video_ids()

    flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with YoutubeDL(flat_opts) as ydl:
        flat_info = ydl.extract_info(f"ytsearch{fetch_count}:{query}", download=False)

    candidates = []
    for entry in flat_info.get("entries") or []:
        if entry is None:
            continue
        if entry.get("id") in excluded_ids:
            continue
        duration_minutes = format_duration_minutes(entry.get("duration"))
        if min_duration_minutes and (duration_minutes is None or duration_minutes < min_duration_minutes):
            continue
        candidates.append(entry)

    # Stap 2: de volledige metadata (kwaliteit/formats, exacte uploaddatum)
    # parallel ophalen, in batches, totdat we genoeg resultaten hebben of geen
    # kandidaten meer over zijn. Zo vangen we onbeschikbare video's
    # (verwijderd/privé/regio-geblokkeerd) op zonder de hele zoekopdracht te
    # laten mislukken of vroegtijdig te weinig resultaten terug te geven.
    results = []
    batch_size = max_results + 5
    offset = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        while len(results) < max_results and offset < len(candidates):
            batch = candidates[offset : offset + batch_size]
            offset += batch_size

            for entry, full in zip(batch, pool.map(lambda e: _extract_full(e["url"]), batch)):
                if len(results) >= max_results:
                    break
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
# Downloaden (H.264, beste beschikbare kwaliteit — geen transcodering)
# =====================================================================================

# YouTube levert vaak alleen hogere resoluties (>1080p) in VP9/AV1 aan, niet in
# H.264/avc1. Deze format-string pakt de beste beschikbare H.264-videotrack +
# beste audio, en valt terug op de beste H.264-combinatie of anders gewoon de
# beste beschikbare stream als er geen H.264 aanwezig is.
H264_FORMAT = "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[vcodec^=avc1]/best"


def _download_video(url, dest_dir):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": H264_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(dest_dir, "%(title).150B [%(id)s].%(ext)s"),
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if not os.path.exists(filename):
            # video+audio zijn gemerged; de extensie is dan veranderd naar mp4
            base, _ = os.path.splitext(filename)
            merged = base + ".mp4"
            if os.path.exists(merged):
                filename = merged
        return filename, info


def _info_to_record(info):
    video_id = info.get("id")
    return {
        "video_id": video_id,
        "title": info.get("title"),
        "upload_date": format_upload_date(info.get("upload_date")),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration_minutes": format_duration_minutes(info.get("duration")),
        "quality": best_quality_label(info),
        "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
    }


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

    exclude_exported = bool(data.get("exclude_exported"))
    exclude_downloaded = bool(data.get("exclude_downloaded"))

    try:
        results = search_youtube(
            query, max_results, min_duration_minutes, exclude_exported, exclude_downloaded
        )
    except Exception as exc:
        return jsonify({"error": f"Zoeken mislukt: {exc}"}), 500

    already_exported = exported_video_ids()
    already_downloaded = downloaded_video_ids()
    for r in results:
        r["already_exported"] = r["video_id"] in already_exported
        r["already_downloaded"] = r["video_id"] in already_downloaded

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


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is verplicht"}), 400

    tmp_dir = tempfile.mkdtemp(prefix="ytdl_")
    try:
        filepath, info = _download_video(url, tmp_dir)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": f"Download mislukt: {exc}"}), 500

    record_downloads([_info_to_record(info)])

    response = send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))
    response.call_on_close(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
    return response


@app.route("/api/download/bulk", methods=["POST"])
def api_download_bulk():
    data = request.get_json(force=True) or {}
    urls = [u.strip() for u in (data.get("urls") or []) if u and u.strip()]
    if not urls:
        return jsonify({"error": "Geen URL's opgegeven"}), 400

    tmp_dir = tempfile.mkdtemp(prefix="ytdl_bulk_")

    def _safe_download(video_url):
        video_dir = tempfile.mkdtemp(dir=tmp_dir)
        try:
            return _download_video(video_url, video_dir)
        except Exception:
            return None

    downloaded = []  # lijst van (filepath, info)
    failed = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(_safe_download, urls):
            if result:
                downloaded.append(result)
            else:
                failed += 1

    if not downloaded:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "Geen van de video's kon gedownload worden"}), 500

    record_downloads([_info_to_record(info) for _, info in downloaded])

    zip_path = os.path.join(tmp_dir, "youtube-clips.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        used_names = set()
        for filepath, _ in downloaded:
            name = os.path.basename(filepath)
            unique_name = name
            i = 1
            while unique_name in used_names:
                base, ext = os.path.splitext(name)
                unique_name = f"{base} ({i}){ext}"
                i += 1
            used_names.add(unique_name)
            zf.write(filepath, arcname=unique_name)

    response = send_file(zip_path, as_attachment=True, download_name="youtube-clips.zip")
    response.headers["X-Downloaded-Count"] = str(len(downloaded))
    response.headers["X-Failed-Count"] = str(failed)
    response.call_on_close(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
    return response


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=debug_mode, threaded=True)
