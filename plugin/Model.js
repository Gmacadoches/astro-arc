// Pure logic for the Astro-Arc panel: config/last-run parsing, geocoding
// parsing (mirrors the weather plugin's Model.js), and small validators.
// Kept side-effect free and module.exports-able so it can be unit tested
// the same way weather/Model.js is.

var DEFAULT_CONFIG = {
  birthDate: "",
  birthTime: "",
  birthTimeUnknown: false,
  locationName: "",
  latitude: null,
  longitude: null,
  frequency: "daily",
  imageBackend: "local",
  openaiModel: "",
  openaiQuality: "",
  stage1Model: "gpt-4o-mini",
  stage2Model: "gpt-4o-mini",
  backgroundSize: "auto",
  artStyle: "symbolist",
  themeGenerator: "built-in",
  historyRetentionDays: 30
}

// How many days one "Themes Generated" entry represents, by frequency —
// used only to estimate how many entries a retention window will hold
// (real generations may skip a period; this is a projection, not a count
// of what's actually on disk).
var FREQUENCY_INTERVAL_DAYS = { daily: 1, weekly: 7, monthly: 30 }

// "aether" shells out to Omarchy's own theme generator for a richer theme
// (it's what fixes the file-manager icon color, among other things — see
// palette_extract.py's icons.theme comment) and silently falls back to
// "built-in" if aether is missing or fails, so picking it never risks a
// broken generation.
var THEME_GENERATOR_CHOICES = [
  { key: "built-in", label: "Built-in" },
  { key: "aether", label: "Aether (Omarchy)" }
]

// Kept in sync by hand with pipeline/styles.toml — same reasoning as
// OPENAI_MODEL_CHOICES below: QML can't read TOML directly, and this list
// changes rarely enough that hand-sync is simpler than a live bridge.
// The first five are the directions explored during Phase 3's style
// review; add more here (and in styles.toml) freely.
var ART_STYLE_CHOICES = [
  { key: "symbolist", label: "Symbolist / Visionary" },
  { key: "engraving", label: "Antique Engraving" },
  { key: "artdeco", label: "Art Deco" },
  { key: "cosmic", label: "Cosmic / Nebula" },
  { key: "surreal", label: "Surreal Painting" },
  { key: "ghibli", label: "Studio Ghibli" }
]

var SIZE_PATTERN = /^[0-9]{2,5}x[0-9]{2,5}$/

// Kept in sync by hand with openai_image_gen.py's MODEL_CHOICES/MODEL_COSTS
// — QML can't import that module directly, and duplicating four short rows
// is simpler than round-tripping through a subprocess just to populate a
// dropdown. Costs are estimates (see the note in openai_image_gen.py on
// why): applying gpt-image-1's published per-quality token counts to each
// model's own per-token rate, not a number OpenAI has confirmed for these
// specific models.
var OPENAI_MODEL_CHOICES = [
  { model: "gpt-image-2", quality: "auto", label: "GPT Image 2 (~$0.008–$0.12 est.)" },
  { model: "gpt-image-1-mini", quality: "low", label: "GPT Image 1 Mini — Low (~$0.002 est.)" },
  { model: "gpt-image-1-mini", quality: "medium", label: "GPT Image 1 Mini — Medium (~$0.008 est.)" },
  { model: "gpt-image-1-mini", quality: "high", label: "GPT Image 1 Mini — High (~$0.03 est.)" }
]

function openaiModelDropdownValue(model, quality) {
  return String(model || "") + "|" + String(quality || "")
}

function parseConfig(raw) {
  try {
    var data = JSON.parse(String(raw || "{}"))
    if (!data || typeof data !== "object") return Object.assign({}, DEFAULT_CONFIG)

    var lat = parseFloat(data.latitude)
    var lon = parseFloat(data.longitude)
    var hasCoords = !isNaN(lat) && !isNaN(lon)

    return {
      birthDate: typeof data.birthDate === "string" ? data.birthDate : "",
      birthTime: typeof data.birthTime === "string" ? data.birthTime : "",
      birthTimeUnknown: !!data.birthTimeUnknown,
      locationName: typeof data.locationName === "string" ? data.locationName : "",
      latitude: hasCoords ? lat : null,
      longitude: hasCoords ? lon : null,
      frequency: ["daily", "weekly", "monthly"].indexOf(data.frequency) >= 0 ? data.frequency : "daily",
      imageBackend: data.imageBackend === "openai" ? "openai" : "local",
      openaiModel: typeof data.openaiModel === "string" ? data.openaiModel : "",
      openaiQuality: typeof data.openaiQuality === "string" ? data.openaiQuality : "",
      stage1Model: typeof data.stage1Model === "string" && data.stage1Model !== "" ? data.stage1Model : "gpt-4o-mini",
      stage2Model: typeof data.stage2Model === "string" && data.stage2Model !== "" ? data.stage2Model : "gpt-4o-mini",
      backgroundSize: data.backgroundSize === "auto" || (typeof data.backgroundSize === "string" && SIZE_PATTERN.test(data.backgroundSize)) ? data.backgroundSize : "auto",
      artStyle: typeof data.artStyle === "string" && data.artStyle !== "" ? data.artStyle : "symbolist",
      themeGenerator: data.themeGenerator === "aether" ? "aether" : "built-in",
      historyRetentionDays: Number.isInteger(data.historyRetentionDays) && data.historyRetentionDays >= 1 && data.historyRetentionDays <= 3650
        ? data.historyRetentionDays : 30
    }
  } catch (e) {
    return Object.assign({}, DEFAULT_CONFIG)
  }
}

// astro-arc-apikey status's output: {"present":true,"masked":"sk-...ab12"}
// or {"present":false}. Never carries the actual key.
function parseApiKeyStatus(raw) {
  try {
    var data = JSON.parse(String(raw || "{}"))
    return { present: !!data.present, masked: typeof data.masked === "string" ? data.masked : "" }
  } catch (e) {
    return { present: false, masked: "" }
  }
}

// build_review.py's index.json: newest-first array of past review builds
// ("Themes Generated" in the widget). sizeBytes/periodKey/hasThemeSnapshot
// are absent on entries built before that feature existed — null/false,
// not a guessed number, so a mixed-age list never shows a fabricated size.
function parseReviewsIndex(raw) {
  try {
    var data = JSON.parse(String(raw || "[]"))
    if (!Array.isArray(data)) return []
    return data.filter(function(r) { return r && r.id && r.path }).map(function(r) {
      var size = Number.isInteger(r.sizeBytes) ? r.sizeBytes : parseInt(r.sizeBytes, 10)
      return {
        id: String(r.id),
        label: String(r.label || "Untitled"),
        createdAt: String(r.createdAt || ""),
        path: String(r.path),
        cardCount: parseInt(r.cardCount, 10) || 0,
        periodKey: typeof r.periodKey === "string" ? r.periodKey : null,
        hasThemeSnapshot: !!r.hasThemeSnapshot,
        sizeBytes: isNaN(size) ? null : size,
        conceptTags: Array.isArray(r.conceptTags) ? r.conceptTags.filter(function(t) { return typeof t === "string" }) : []
      }
    })
  } catch (e) {
    return []
  }
}

// "nature/rootedness" -> "Nature Rootedness" — splits on the register/
// specific "/" convention concept tags use (see llm_pipeline.py's
// Conventions note in CONTEXT.md) and on -/_, then title-cases each word.
function humanizeConceptTag(tag) {
  return String(tag || "")
    .split("/").join(" ")
    .replace(/[-_]+/g, " ")
    .split(" ")
    .filter(function(w) { return w.length > 0 })
    .map(function(w) { return w.charAt(0).toUpperCase() + w.slice(1) })
    .join(" ")
}

// Save Selected Theme's name-prompt default: up to 2 of that generation's
// own concept tags, humanized — real content from the reading that
// produced it, not a hash-looking id. "" (never a placeholder guess) when
// the entry has no concept tags to draw from (built before this field
// existed, or Stage 2 returned none) — the caller falls back to its own
// placeholder text in that case.
function suggestThemeName(conceptTags) {
  var tags = (conceptTags || []).filter(function(t) { return typeof t === "string" && t.trim() !== "" })
  if (tags.length === 0) return ""
  return tags.slice(0, 2).map(humanizeConceptTag).join(" ")
}

// astro-arc-generate's running costs.json (newest first): sums whatever
// totalCost values are known into a running total, and separately counts
// how many entries had at least one unknown-cost call (an unrecognized
// stage1/stage2 model, most likely) — shown as "+N unknown" rather than
// silently folding into a total that would then undercount.
function sumCosts(rawCostsArray) {
  var entries
  try {
    entries = JSON.parse(String(rawCostsArray || "[]"))
    if (!Array.isArray(entries)) entries = []
  } catch (e) {
    entries = []
  }
  var total = 0
  var unknownCount = 0
  var generationCount = entries.length
  for (var i = 0; i < entries.length; i++) {
    var e = entries[i]
    if (e && typeof e.totalCost === "number") total += e.totalCost
    else unknownCount++
  }
  return { total: total, unknownCount: unknownCount, generationCount: generationCount }
}

// "512 KB" / "4.3 MB" — one decimal above 1 MB, whole numbers below (a
// decimal KB reads as false precision for a handful of small files).
function formatBytes(bytes) {
  if (typeof bytes !== "number" || isNaN(bytes) || bytes < 0) return "unknown"
  if (bytes < 1024) return bytes + " B"
  var kb = bytes / 1024
  if (kb < 1024) return Math.round(kb) + " KB"
  return (kb / 1024).toFixed(1) + " MB"
}

// Real average size of every "Themes Generated" entry that actually has a
// recorded sizeBytes (pre-feature entries don't), times how many entries
// the retention window is projected to hold at the configured frequency —
// never a guessed number when there's no size history yet to average, per
// this project's cost-estimate convention: null/"unknown" beats a
// fabricated figure. Returns { estimatedBytes, knownCount } — knownCount
// lets the caller show "estimate" vs. "no data yet" language.
function estimateHistorySpace(reviewsIndex, retentionDays, frequency) {
  var known = (reviewsIndex || []).filter(function(r) { return typeof r.sizeBytes === "number" })
  if (known.length === 0) return { estimatedBytes: null, knownCount: 0 }

  var avgBytes = known.reduce(function(sum, r) { return sum + r.sizeBytes }, 0) / known.length
  var intervalDays = FREQUENCY_INTERVAL_DAYS[frequency] || 1
  var projectedEntries = Math.max(1, Math.round((retentionDays || 30) / intervalDays))
  return { estimatedBytes: avgBytes * projectedEntries, knownCount: known.length }
}


function parseLastRun(raw) {
  try {
    var data = JSON.parse(String(raw || "{}"))
    if (!data || typeof data !== "object" || !data.generatedAt) return null
    return {
      periodKey: String(data.periodKey || ""),
      generatedAt: String(data.generatedAt || ""),
      frequency: String(data.frequency || ""),
      // The one-line distillation of this run's psychological reading, and
      // its narrative-position badge (opening/peak/release/threshold/...) —
      // same two fields the review-history HTML shows as a badge over an
      // italicized quote (build_review.py's .badge/.distillation).
      distillation: String(data.distillation || ""),
      narrativePosition: String(data.narrativePosition || "")
    }
  } catch (e) {
    return null
  }
}

// Open-Meteo geocoding response -> suggestion rows for the location picker.
// Identical shape to the weather plugin's Model.js so behavior stays
// consistent between the two location-search UIs.
function parseGeocodingResults(raw) {
  try {
    var data = JSON.parse(String(raw || "{}"))
    var results = data.results
    if (!results || !results.length) return []

    var out = []
    for (var i = 0; i < results.length; i++) {
      var r = results[i]
      if (!r || !r.name || r.latitude === undefined || r.longitude === undefined) continue
      var region = [r.admin1, r.country].filter(function(part) { return !!part }).join(", ")
      out.push({
        name: String(r.name),
        description: region,
        latitude: r.latitude,
        longitude: r.longitude
      })
    }
    return out
  } catch (e) {
    return []
  }
}

function locationCommit(text, suggestions, selectedIndex) {
  var name = String(text || "").replace(/^\s+|\s+$/g, "")
  if (name === "") return { name: "", latitude: null, longitude: null }

  var choices = suggestions || []
  var index = Math.max(0, Math.min(parseInt(selectedIndex, 10) || 0, choices.length - 1))
  var suggestion = choices[index]
  if (suggestion) return suggestion

  return { name: name, latitude: null, longitude: null }
}

// Digit-only input mask helpers: strip everything but digits, cap the
// length, then re-insert the format's literal separators. Used from
// onTextChanged so a field always displays YYYY-MM-DD / HH:MM as the user
// types plain numbers, without requiring them to type the separators
// themselves.
function formatDateDigits(raw) {
  var digits = String(raw || "").replace(/[^0-9]/g, "").slice(0, 8)
  if (digits.length <= 4) return digits
  if (digits.length <= 6) return digits.slice(0, 4) + "-" + digits.slice(4)
  return digits.slice(0, 4) + "-" + digits.slice(4, 6) + "-" + digits.slice(6)
}

function formatTimeDigits(raw) {
  var digits = String(raw || "").replace(/[^0-9]/g, "").slice(0, 4)
  if (digits.length <= 2) return digits
  return digits.slice(0, 2) + ":" + digits.slice(2)
}

function isValidDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false
  var d = new Date(value + "T00:00:00")
  return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value
}

function isValidTime(value) {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(String(value || ""))
}

function isValidBackgroundSize(value) {
  return value === "auto" || SIZE_PATTERN.test(String(value || ""))
}

function isValidHistoryRetentionDays(value) {
  var n = parseInt(value, 10)
  return String(n) === String(value).trim() && n >= 1 && n <= 3650
}

function isValidCoordinate(lat, lon) {
  var la = parseFloat(lat)
  var lo = parseFloat(lon)
  return !isNaN(la) && !isNaN(lo) && la >= -90 && la <= 90 && lo >= -180 && lo <= 180
}

function frequencyLabel(value) {
  switch (value) {
    case "weekly": return "Weekly"
    case "monthly": return "Monthly"
    default: return "Daily"
  }
}

// Formats a generatedAt timestamp for display. Callers own the surrounding
// wording (e.g. "Daily · " + this) so this only ever returns the date/time
// part; `formatter` lets a caller pick a compact pattern instead of the
// (often very long, timezone-name-included) default toLocaleString().
function formatGeneratedAt(iso, formatter) {
  if (!iso) return "unknown time"
  var d = new Date(iso)
  if (isNaN(d.getTime())) return "unknown time"
  return formatter ? formatter(d) : d.toLocaleString()
}

if (typeof module !== "undefined") {
  module.exports = {
    DEFAULT_CONFIG: DEFAULT_CONFIG,
    OPENAI_MODEL_CHOICES: OPENAI_MODEL_CHOICES,
    ART_STYLE_CHOICES: ART_STYLE_CHOICES,
    THEME_GENERATOR_CHOICES: THEME_GENERATOR_CHOICES,
    openaiModelDropdownValue: openaiModelDropdownValue,
    formatDateDigits: formatDateDigits,
    formatTimeDigits: formatTimeDigits,
    parseConfig: parseConfig,
    parseLastRun: parseLastRun,
    parseApiKeyStatus: parseApiKeyStatus,
    parseReviewsIndex: parseReviewsIndex,
    parseGeocodingResults: parseGeocodingResults,
    locationCommit: locationCommit,
    isValidDate: isValidDate,
    isValidTime: isValidTime,
    isValidBackgroundSize: isValidBackgroundSize,
    isValidHistoryRetentionDays: isValidHistoryRetentionDays,
    isValidCoordinate: isValidCoordinate,
    frequencyLabel: frequencyLabel,
    formatGeneratedAt: formatGeneratedAt,
    sumCosts: sumCosts,
    formatBytes: formatBytes,
    estimateHistorySpace: estimateHistorySpace,
    suggestThemeName: suggestThemeName
  }
}
