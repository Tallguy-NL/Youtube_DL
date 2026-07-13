(function () {
  "use strict";

  const searchForm = document.getElementById("search-form");
  const searchBtn = document.getElementById("search-btn");
  const searchStatus = document.getElementById("search-status");
  const searchStatusText = document.getElementById("search-status-text");
  const searchSpinner = document.getElementById("search-spinner");
  const resultsCard = document.getElementById("results-card");
  const resultsBody = document.getElementById("results-body");
  const selectAll = document.getElementById("select-all");
  const selectionCount = document.getElementById("selection-count");
  const exportBtn = document.getElementById("export-btn");
  const downloadZipBtn = document.getElementById("download-zip-btn");
  const downloadStatus = document.getElementById("download-status");
  const downloadStatusText = document.getElementById("download-status-text");
  const downloadSpinner = document.getElementById("download-spinner");
  const filterHideExported = document.getElementById("filter-hide-exported");
  const filterHideDownloaded = document.getElementById("filter-hide-downloaded");
  const excludeExportedCheckbox = document.getElementById("exclude-exported-checkbox");
  const excludeDownloadedCheckbox = document.getElementById("exclude-downloaded-checkbox");

  const filterTitle = document.getElementById("filter-title");
  const filterDateFrom = document.getElementById("filter-date-from");
  const filterDateTo = document.getElementById("filter-date-to");
  const filterUploader = document.getElementById("filter-uploader");
  const filterDurationMin = document.getElementById("filter-duration-min");
  const filterDurationMax = document.getElementById("filter-duration-max");
  const filterQuality = document.getElementById("filter-quality");
  const filterResetBtn = document.getElementById("filter-reset-btn");

  let currentResults = [];

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function visibleRows() {
    return Array.from(resultsBody.querySelectorAll("tr"));
  }

  function matchesFilters(item) {
    const titleQuery = filterTitle.value.trim().toLowerCase();
    if (titleQuery && !(item.title || "").toLowerCase().includes(titleQuery)) {
      return false;
    }

    const uploaderQuery = filterUploader.value.trim().toLowerCase();
    if (uploaderQuery && !(item.uploader || "").toLowerCase().includes(uploaderQuery)) {
      return false;
    }

    const dateFrom = filterDateFrom.value;
    if (dateFrom && (!item.upload_date || item.upload_date < dateFrom)) {
      return false;
    }
    const dateTo = filterDateTo.value;
    if (dateTo && (!item.upload_date || item.upload_date > dateTo)) {
      return false;
    }

    const durationMin = filterDurationMin.value;
    if (durationMin !== "" && (item.duration_minutes == null || item.duration_minutes < Number(durationMin))) {
      return false;
    }
    const durationMax = filterDurationMax.value;
    if (durationMax !== "" && (item.duration_minutes == null || item.duration_minutes > Number(durationMax))) {
      return false;
    }

    const minQuality = filterQuality.value;
    if (minQuality) {
      const itemHeight = parseInt(item.quality, 10) || 0;
      const minHeight = parseInt(minQuality, 10) || 0;
      if (itemHeight < minHeight) {
        return false;
      }
    }

    return true;
  }

  function applyFilter() {
    const hideExported = filterHideExported.checked;
    const hideDownloaded = filterHideDownloaded.checked;
    visibleRows().forEach((row) => {
      const item = currentResults[Number(row.dataset.index)];
      const alreadyExported = row.dataset.alreadyExported === "true";
      const alreadyDownloaded = row.dataset.alreadyDownloaded === "true";
      const shouldHide =
        (hideExported && alreadyExported) ||
        (hideDownloaded && alreadyDownloaded) ||
        !matchesFilters(item);
      row.hidden = shouldHide;
      if (shouldHide) {
        row.querySelector('input[type="checkbox"]').checked = false;
      }
    });
    updateSelectionCount();
  }

  function populateQualityOptions(results) {
    const qualities = Array.from(new Set(results.map((r) => r.quality).filter(Boolean)));
    qualities.sort((a, b) => (parseInt(b, 10) || 0) - (parseInt(a, 10) || 0));

    filterQuality.innerHTML = '<option value="">Alle</option>';
    qualities.forEach((q) => {
      const option = document.createElement("option");
      option.value = q;
      option.textContent = q;
      filterQuality.appendChild(option);
    });
  }

  function resetFilters() {
    filterTitle.value = "";
    filterDateFrom.value = "";
    filterDateTo.value = "";
    filterUploader.value = "";
    filterDurationMin.value = "";
    filterDurationMax.value = "";
    filterQuality.value = "";
    filterHideExported.checked = false;
    filterHideDownloaded.checked = false;
    applyFilter();
  }

  function updateSelectionCount() {
    const checked = resultsBody.querySelectorAll('input[type="checkbox"]:checked');
    selectionCount.textContent = checked.length
      ? `${checked.length} geselecteerd`
      : "";
    exportBtn.disabled = checked.length === 0;
    downloadZipBtn.disabled = checked.length === 0;

    const visible = visibleRows().filter((r) => !r.hidden);
    const visibleChecked = visible.filter((r) =>
      r.querySelector('input[type="checkbox"]').checked
    );
    selectAll.checked = visible.length > 0 && visibleChecked.length === visible.length;
    selectAll.indeterminate =
      visibleChecked.length > 0 && visibleChecked.length < visible.length;
  }

  function buildStatusBadges(item) {
    const badges = [];
    if (item.already_exported) {
      badges.push('<span class="badge">eerder geëxporteerd</span>');
    }
    if (item.already_downloaded) {
      badges.push('<span class="badge">eerder gedownload</span>');
    }
    if (badges.length === 0) {
      badges.push('<span class="badge badge-muted">nieuw</span>');
    }
    return badges.join(" ");
  }

  function renderResults(results) {
    currentResults = results;
    resultsBody.innerHTML = "";

    results.forEach((item, index) => {
      const row = document.createElement("tr");
      row.dataset.index = String(index);
      row.dataset.alreadyExported = item.already_exported ? "true" : "false";
      row.dataset.alreadyDownloaded = item.already_downloaded ? "true" : "false";

      const statusBadge = buildStatusBadges(item);

      row.innerHTML = `
        <td class="col-check"><input type="checkbox" class="row-check"></td>
        <td class="title-cell">${escapeHtml(item.title)}</td>
        <td>${escapeHtml(item.upload_date || "-")}</td>
        <td>${escapeHtml(item.uploader || "-")}</td>
        <td>${item.duration_minutes != null ? item.duration_minutes : "-"}</td>
        <td>${escapeHtml(item.quality || "-")}</td>
        <td><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.url)}</a></td>
        <td class="status-cell">${statusBadge}</td>
        <td>
          <button type="button" class="btn btn-outline btn-tiny row-download-btn">Download</button>
        </td>
      `;

      resultsBody.appendChild(row);
    });

    resultsCard.hidden = false;
    selectAll.checked = false;
    populateQualityOptions(results);
    resetFilters();
  }

  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const query = document.getElementById("query").value.trim();
    const maxResults = document.getElementById("max_results").value;
    const minDuration = document.getElementById("min_duration").value;
    const excludeExported = excludeExportedCheckbox.checked;
    const excludeDownloaded = excludeDownloadedCheckbox.checked;

    if (!query) return;

    searchBtn.disabled = true;
    searchStatusText.textContent = "Bezig met zoeken op YouTube...";
    searchStatus.classList.remove("error");
    searchSpinner.hidden = false;

    try {
      const resp = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          max_results: maxResults ? Number(maxResults) : 20,
          min_duration_minutes: minDuration ? Number(minDuration) : null,
          exclude_exported: excludeExported,
          exclude_downloaded: excludeDownloaded,
        }),
      });

      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.error || "Onbekende fout");
      }

      renderResults(data.results);
      searchStatusText.textContent = data.results.length
        ? `${data.results.length} resultaten gevonden.`
        : "Geen resultaten gevonden die aan de criteria voldoen.";
    } catch (err) {
      searchStatusText.textContent = err.message;
      searchStatus.classList.add("error");
      resultsCard.hidden = true;
    } finally {
      searchBtn.disabled = false;
      searchSpinner.hidden = true;
    }
  });

  resultsBody.addEventListener("change", (e) => {
    if (e.target.classList.contains("row-check")) {
      updateSelectionCount();
    }
  });

  selectAll.addEventListener("change", () => {
    const checked = selectAll.checked;
    visibleRows()
      .filter((r) => !r.hidden)
      .forEach((row) => {
        row.querySelector('input[type="checkbox"]').checked = checked;
      });
    updateSelectionCount();
  });

  [filterHideExported, filterHideDownloaded].forEach((el) => el.addEventListener("change", applyFilter));

  [filterTitle, filterUploader].forEach((el) => el.addEventListener("input", applyFilter));
  [filterDateFrom, filterDateTo, filterDurationMin, filterDurationMax, filterQuality].forEach((el) =>
    el.addEventListener("change", applyFilter)
  );
  filterResetBtn.addEventListener("click", resetFilters);

  exportBtn.addEventListener("click", async () => {
    const selectedItems = [];
    visibleRows().forEach((row) => {
      const checkbox = row.querySelector('input[type="checkbox"]');
      if (checkbox.checked) {
        selectedItems.push(currentResults[Number(row.dataset.index)]);
      }
    });

    if (selectedItems.length === 0) return;

    exportBtn.disabled = true;

    try {
      // 1. Server-side JSON export-geschiedenis bijwerken
      const resp = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: selectedItems }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Export mislukt");
      }

      // 2. Client-side .txt bestand met URL's genereren en downloaden
      const urlsText = selectedItems.map((item) => item.url).join("\n");
      const blob = new Blob([urlsText], { type: "text/plain" });
      const link = document.createElement("a");
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      link.href = URL.createObjectURL(blob);
      link.download = `youtube-urls-${timestamp}.txt`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);

      // 3. Rijen in de UI meteen markeren als geëxporteerd
      selectedItems.forEach((item) => {
        item.already_exported = true;
      });
      visibleRows().forEach((row) => {
        const checkbox = row.querySelector('input[type="checkbox"]');
        if (checkbox.checked) {
          row.dataset.alreadyExported = "true";
          const item = currentResults[Number(row.dataset.index)];
          row.querySelector(".status-cell").innerHTML = buildStatusBadges(item);
        }
      });

      searchStatus.classList.remove("error");
      searchStatusText.textContent = `${selectedItems.length} URL('s) geëxporteerd.`;
      applyFilter();
    } catch (err) {
      searchStatus.classList.add("error");
      searchStatusText.textContent = err.message;
    } finally {
      exportBtn.disabled = false;
    }
  });

  function sanitizeFilename(name) {
    return (name || "clip").replace(/[\\/:*?"<>|]/g, "_").trim() || "clip";
  }

  function triggerBlobDownload(blob, filename) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
  }

  resultsBody.addEventListener("click", async (e) => {
    const btn = e.target.closest(".row-download-btn");
    if (!btn) return;

    const row = btn.closest("tr");
    const item = currentResults[Number(row.dataset.index)];
    const originalText = btn.textContent;

    btn.disabled = true;
    btn.textContent = "Bezig...";

    try {
      const resp = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: item.url }),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || "Download mislukt");
      }

      const blob = await resp.blob();
      triggerBlobDownload(blob, `${sanitizeFilename(item.title)} [${item.video_id}].mp4`);

      item.already_downloaded = true;
      row.dataset.alreadyDownloaded = "true";
      row.querySelector(".status-cell").innerHTML = buildStatusBadges(item);
    } catch (err) {
      searchStatus.classList.add("error");
      searchStatusText.textContent = err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  downloadZipBtn.addEventListener("click", async () => {
    const selectedItems = [];
    visibleRows().forEach((row) => {
      const checkbox = row.querySelector('input[type="checkbox"]');
      if (checkbox.checked) {
        selectedItems.push(currentResults[Number(row.dataset.index)]);
      }
    });

    if (selectedItems.length === 0) return;

    downloadZipBtn.disabled = true;
    downloadSpinner.hidden = false;
    downloadStatus.classList.remove("error");
    downloadStatusText.classList.add("neon-pulse");
    downloadStatusText.textContent = `Bezig met downloaden van ${selectedItems.length} clip(s) in H.264 en inpakken als ZIP — dit kan enige tijd duren...`;

    try {
      const resp = await fetch("/api/download/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls: selectedItems.map((item) => item.url) }),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || "Download mislukt");
      }

      const downloadedCount = resp.headers.get("X-Downloaded-Count");
      const failedCount = resp.headers.get("X-Failed-Count");

      const blob = await resp.blob();
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      triggerBlobDownload(blob, `youtube-clips-${timestamp}.zip`);

      // Alle geselecteerde items markeren als gedownload. We weten niet welke
      // van de individuele downloads eventueel mislukt zijn (de zip bevat
      // alleen de gelukte), maar dat beïnvloedt hooguit de badge van een
      // enkele mislukte clip totdat er opnieuw gezocht wordt.
      selectedItems.forEach((item) => {
        item.already_downloaded = true;
      });
      visibleRows().forEach((row) => {
        const checkbox = row.querySelector('input[type="checkbox"]');
        if (checkbox.checked) {
          row.dataset.alreadyDownloaded = "true";
          const item = currentResults[Number(row.dataset.index)];
          row.querySelector(".status-cell").innerHTML = buildStatusBadges(item);
        }
      });

      downloadStatusText.textContent =
        failedCount && Number(failedCount) > 0
          ? `${downloadedCount} clip(s) gedownload, ${failedCount} mislukt (niet meer beschikbaar).`
          : `${downloadedCount} clip(s) gedownload en als ZIP opgeslagen.`;
    } catch (err) {
      downloadStatus.classList.add("error");
      downloadStatusText.textContent = err.message;
    } finally {
      downloadZipBtn.disabled = false;
      downloadSpinner.hidden = true;
      downloadStatusText.classList.remove("neon-pulse");
    }
  });
})();
