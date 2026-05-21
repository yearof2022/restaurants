const PRICE_RANK = { "": 0, "$": 1, "$$": 2, "$$$": 3, "$$$$": 4 };

function ratingStars(s) {
  return (s || "").match(/⭐/g)?.length || 0;
}

function splitCuisine(s) {
  return (s || "")
    .split(",")
    .map(x => x.trim())
    .filter(Boolean);
}

function escapeHtml(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function pinSvg(fill, stroke) {
  return `
    <svg width="26" height="38" viewBox="0 0 26 38" xmlns="http://www.w3.org/2000/svg">
      <path d="M13 0C5.8 0 0 5.8 0 13c0 9 13 25 13 25s13-16 13-25C26 5.8 20.2 0 13 0z"
            fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>
      <circle cx="13" cy="13" r="5" fill="#ffffff"/>
    </svg>`;
}

function makeIcon(visited) {
  const fill = visited ? "#34a853" : "#ea4335";
  const stroke = visited ? "#1e7a36" : "#a52822";
  return L.divIcon({
    className: "pin-icon",
    html: pinSvg(fill, stroke),
    iconSize: [26, 38],
    iconAnchor: [13, 36],
    popupAnchor: [0, -32],
  });
}

async function loadData() {
  const resp = await fetch("restaurants.json", { cache: "no-cache" });
  if (!resp.ok) throw new Error("Failed to load restaurants.json — run geocode.py first");
  return resp.json();
}

async function loadZomato() {
  try {
    const resp = await fetch("zomato_data.json", { cache: "no-cache" });
    if (!resp.ok) return {};
    const arr = await resp.json();
    const byKey = {};
    for (const z of arr) {
      if (z.matched) byKey[z.csv_key.toLowerCase()] = z;
    }
    return byKey;
  } catch {
    return {};
  }
}

function populateMultiSelect(el, values) {
  const placeholder = el.dataset.placeholder || "Select…";
  const opts = [...new Set(values)].sort((a, b) => a.localeCompare(b));
  el.innerHTML =
    `<option value="" disabled>${placeholder}</option>` +
    opts.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
}

function getMultiValues(el) {
  return [...el.selectedOptions].map(o => o.value).filter(Boolean);
}

function buildInfoHtml(r, z) {
  const visited = r.Visited === "Yes";
  const stars = r.Rating || "";
  const metaBits = [r.City, r["Cuisine Type"], r.Price, stars].filter(Boolean);
  const meta = metaBits.map(escapeHtml).join('<span class="sep">·</span>');

  const links = [];
  if (r.URL) {
    const isInsta = /instagram\.com/i.test(r.URL);
    links.push(
      `<a href="${escapeHtml(r.URL)}" target="_blank" rel="noopener">${isInsta ? "📷 Instagram" : "🔗 Link"}</a>`
    );
  }
  if (z && z.matched) {
    const q = encodeURIComponent(`zomato ${z.name || r["Restaurant Name"]} ${r.City || ""}`.trim());
    links.push(`<a href="https://www.google.com/search?q=${q}&btnI=1" target="_blank" rel="noopener">🍴 Zomato</a>`);
  }
  if (r.lat != null && r.lng != null) {
    const osmUrl = `https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lng}#map=18/${r.lat}/${r.lng}`;
    links.push(`<a href="${osmUrl}" target="_blank" rel="noopener">📍 OSM</a>`);
  }

  let zomatoBlock = "";
  if (z && z.matched) {
    const ratingBit = z.rating ? `⭐ ${z.rating}` : "";
    const votesBit = z.votes ? `(${z.votes.toLocaleString()})` : "";
    const etaBit = z.eta && z.serviceable ? `🛵 ${escapeHtml(z.eta)}` : "";
    const zomatoMeta = [ratingBit, votesBit, etaBit].filter(Boolean).join(" ");
    const photo = z.image
      ? `<img class="zomato-photo" src="${escapeHtml(z.image)}" alt="${escapeHtml(z.name || "")}" loading="lazy"/>`
      : "";
    zomatoBlock = `
      <div class="zomato">
        ${photo}
        <div class="zomato-meta">${zomatoMeta}</div>
      </div>
    `;
  }

  return `
    <div class="iw">
      <h3>
        ${escapeHtml(r["Restaurant Name"])}
        <span class="badge ${visited ? "visited" : "wishlist"}">${visited ? "Visited" : "Wishlist"}</span>
      </h3>
      ${meta ? `<div class="meta">${meta}</div>` : ""}
      ${zomatoBlock}
      ${r.Comment ? `<div class="comment">${escapeHtml(r.Comment)}</div>` : ""}
      ${links.length ? `<div class="links">${links.join("")}</div>` : ""}
    </div>
  `;
}

async function init() {
  const [data, zomato] = await Promise.all([loadData(), loadZomato()]);

  const map = L.map("map", { zoomControl: true }).setView([19.076, 72.8777], 11);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  const visitedIcon = makeIcon(true);
  const wishlistIcon = makeIcon(false);

  const entries = data.map(r => {
    const visited = r.Visited === "Yes";
    const marker = L.marker([r.lat, r.lng], {
      icon: visited ? visitedIcon : wishlistIcon,
      title: r["Restaurant Name"],
    });
    const zKey = `${r["Restaurant Name"]}|${r.City || ""}`.toLowerCase();
    const z = zomato[zKey];
    marker.bindPopup(buildInfoHtml(r, z), { maxWidth: 340 });
    marker.addTo(map);
    return { row: r, marker, onMap: true };
  });

  // Populate filter dropdowns
  const cityEl = document.getElementById("f-city");
  const cuisineEl = document.getElementById("f-cuisine");
  populateMultiSelect(cityEl, data.map(r => r.City).filter(Boolean));
  populateMultiSelect(cuisineEl, data.flatMap(r => splitCuisine(r["Cuisine Type"])));

  const searchEl = document.getElementById("f-search");
  const priceEl = document.getElementById("f-price");
  const ratingEl = document.getElementById("f-rating");
  const visitedEl = document.getElementById("f-visited");
  const resetBtn = document.getElementById("f-reset");
  const countEl = document.getElementById("count");

  function applyFilters() {
    const q = searchEl.value.trim().toLowerCase();
    const cities = getMultiValues(cityEl);
    const cuisines = getMultiValues(cuisineEl);
    const priceCap = PRICE_RANK[priceEl.value] || 0;
    const minRating = parseInt(ratingEl.value, 10) || 0;
    const visitedFilter = visitedEl.value;

    let shown = 0;
    for (const entry of entries) {
      const { row, marker } = entry;
      let ok = true;

      if (q) {
        const hay = `${row["Restaurant Name"] || ""} ${row.Comment || ""}`.toLowerCase();
        if (!hay.includes(q)) ok = false;
      }
      if (ok && cities.length && !cities.includes(row.City)) ok = false;
      if (ok && cuisines.length) {
        const rowCuisines = splitCuisine(row["Cuisine Type"]);
        if (!cuisines.some(c => rowCuisines.includes(c))) ok = false;
      }
      if (ok && priceCap) {
        const rp = PRICE_RANK[row.Price] || 0;
        if (rp === 0 || rp > priceCap) ok = false;
      }
      if (ok && minRating) {
        if (ratingStars(row.Rating) < minRating) ok = false;
      }
      if (ok && visitedFilter) {
        if ((row.Visited || "No") !== visitedFilter) ok = false;
      }

      if (ok && !entry.onMap) {
        marker.addTo(map);
        entry.onMap = true;
      } else if (!ok && entry.onMap) {
        map.removeLayer(marker);
        entry.onMap = false;
      }
      if (ok) shown++;
    }

    countEl.textContent = `${shown} of ${entries.length} shown`;
  }

  let debounceTimer;
  function debouncedApply() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilters, 120);
  }

  [searchEl, cityEl, cuisineEl, priceEl, ratingEl, visitedEl].forEach(el => {
    el.addEventListener("input", debouncedApply);
    el.addEventListener("change", debouncedApply);
  });

  resetBtn.addEventListener("click", () => {
    searchEl.value = "";
    [cityEl, cuisineEl].forEach(el => {
      [...el.options].forEach(o => (o.selected = false));
    });
    priceEl.value = "";
    ratingEl.value = "0";
    visitedEl.value = "";
    applyFilters();
  });

  applyFilters();
}

init().catch(err => {
  console.error(err);
  document.getElementById("map").innerHTML =
    `<div style="padding:24px;font-family:sans-serif;color:#a52822">
       <strong>Failed to load map.</strong><br/>${escapeHtml(err.message)}
     </div>`;
});
