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
  artStyle: "symbolist"
}

// Kept in sync by hand with pipeline/styles.toml — same reasoning as
// OPENAI_MODEL_CHOICES below: QML can't read TOML directly, and this list
// changes rarely enough that hand-sync is simpler than a live bridge.
// These five are the directions explored during Phase 3's style review.
var ART_STYLE_CHOICES = [
  { key: "symbolist", label: "Symbolist / Visionary" },
  { key: "engraving", label: "Antique Engraving" },
  { key: "artdeco", label: "Art Deco" },
  { key: "cosmic", label: "Cosmic / Nebula" },
  { key: "surreal", label: "Surreal Painting" }
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
      artStyle: typeof data.artStyle === "string" && data.artStyle !== "" ? data.artStyle : "symbolist"
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

// build_review.py's index.json: newest-first array of past review builds.
function parseReviewsIndex(raw) {
  try {
    var data = JSON.parse(String(raw || "[]"))
    if (!Array.isArray(data)) return []
    return data.filter(function(r) { return r && r.id && r.path }).map(function(r) {
      return {
        id: String(r.id),
        label: String(r.label || "Untitled"),
        createdAt: String(r.createdAt || ""),
        path: String(r.path),
        cardCount: parseInt(r.cardCount, 10) || 0
      }
    })
  } catch (e) {
    return []
  }
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

function parseFundsCheck(raw) {
  try {
    var data = JSON.parse(String(raw || "{}"))
    return { available: !!data.available, message: typeof data.message === "string" ? data.message : "" }
  } catch (e) {
    return { available: false, message: "Funds check failed." }
  }
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
    isValidCoordinate: isValidCoordinate,
    frequencyLabel: frequencyLabel,
    formatGeneratedAt: formatGeneratedAt,
    sumCosts: sumCosts,
    parseFundsCheck: parseFundsCheck
  }
}
