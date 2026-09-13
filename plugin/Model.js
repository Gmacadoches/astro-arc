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
  // "manual" — a fresh install generates nothing until asked. Every
  // generation spends real money on an image API, so the cadence that
  // costs nothing is the only safe thing to pick on someone's behalf;
  // an explicit "daily" in an existing config.json still wins over this.
  frequency: "manual",
  // "openai" — the legacy key `provider` superseded. It defaulted to "local"
  // (Stable Diffusion) long after that path stopped being offered in the UI,
  // which meant the default named a backend a new install could not reach.
  // astro-arc-generate reads `provider` first and this only as a fallback, so
  // the two must at least agree.
  imageBackend: "openai",
  openaiModel: "",
  openaiQuality: "",
  stage1Model: "gpt-4o-mini",
  stage2Model: "gpt-4o-mini",
  backgroundSize: "auto",
  artStyle: "symbolist",
  themeGenerator: "built-in",
  historyRetentionDays: 30,
  maxCostPerImage: 0,
  provider: "openai"
}

// How many days one "Themes Generated" entry represents, by frequency —
// used only to estimate how many entries a retention window will hold
// (real generations may skip a period; this is a projection, not a count
// of what's actually on disk).
// `manual` has no interval by definition. Left out rather than given a number,
// so anything projecting from a cadence has to decide what to do about its
// absence instead of silently treating it as daily.
var FREQUENCY_INTERVAL_DAYS = { hourly: 1 / 24, daily: 1, weekly: 7, monthly: 30 }

var FREQUENCY_CHOICES = [
  { key: "hourly", label: "Hourly" },
  { key: "daily", label: "Daily" },
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
  { key: "manual", label: "None" }
]

// "aether" shells out to Omarchy's own theme generator for a richer theme
// (it's what fixes the file-manager icon color, among other things — see
// palette_extract.py's icons.theme comment) and silently falls back to
// "built-in" if aether is missing or fails, so picking it never risks a
// broken generation.
// The global provider. Everything else in the panel is scoped to it: which key
// you paste, which models are offered, what they cost. Only OpenAI today — the
// list exists so adding a second provider is a data change rather than a
// redesign of the settings tab. (Supersedes the old imageBackend picker, which
// offered local Stable Diffusion; that path still works if set by hand.)
var PROVIDER_CHOICES = [
  { key: "openai", label: "OpenAI" }
]

var THEME_GENERATOR_CHOICES = [
  // Labelled "Astro-Arc" rather than "Built-in": from the panel the choice is
  // between this project's own extractor and Omarchy's Aether, and "Built-in"
  // reads as "built into Omarchy", which is the opposite of what it means. The
  // config VALUE stays "built-in" — it is what astro-arc-generate branches on.
  { key: "built-in", label: "Astro-Arc" },
  { key: "aether", label: "Aether (Omarchy)" }
]

// Kept in sync by hand with pipeline/styles.toml — same reasoning as
// OPENAI_MODEL_CHOICES below: QML can't read TOML directly, and this list
// changes rarely enough that hand-sync is simpler than a live bridge.
// Curated down to 5 then back up to 7 on 2026-09-07 — see styles.toml's
// own header comment and CHANGELOG.md for what changed and why. Add more
// here (and in styles.toml) freely.
var ART_STYLE_CHOICES = [
  { key: "symbolist", label: "Symbolist / Visionary" },
  { key: "engraving", label: "Antique Engraving" },
  { key: "cosmic", label: "Cosmic / Nebula" },
  { key: "ghibli", label: "Studio Ghibli" },
  { key: "cyberpunk", label: "Cyberpunk" },
  { key: "ukiyoe", label: "Ukiyo-e" },
  { key: "illuminated", label: "Illuminated Manuscript" }
]

// ---- Model catalog labels -------------------------------------------------
// Rows come from backend/bin/model_catalog.py. Cost is deliberately allowed to
// be absent: a model with no hand-entered rate, or one never yet rendered, says
// so instead of showing a number, because this project's standing rule is that
// a cost figure is real tokens at a known rate or nothing at all.

function formatUsd(n) {
  if (typeof n !== "number") return ""
  return n < 0.01 ? "$" + n.toFixed(4) : "$" + n.toFixed(3)
}

// Chat cost can't be a single number the way an image render can: it depends on
// how many tokens the prompt and reply turn out to be. The honest label is the
// rate itself, per 1M tokens in/out.
function chatModelLabel(row) {
  if (!row) return ""
  if (!row.hasRate) return row.label + "  · cost unknown (no rate)"
  return row.label + "  · " + costSuffix(row.chatRunCost, row.chatRunSource) + "/run (all 3 stages)"
}

function imageModelLabel(row) {
  if (!row) return ""
  if (row.costState === "no-rate") return row.label + "  · cost unknown (no rate)"
  return row.label + "  · " + costSuffix(row.cost, row.costState) + "/image"
}

// What the NEXT generation will charge, given what is already cached. Stage 1
// and Stage 1.5 are cached per period and the signature per birth chart, so a
// same-day re-run is far cheaper than a fresh day — putting that number on the
// button is the difference between an informed click and a surprise.
function nextRunLabel(nextRun) {
  if (!nextRun || typeof nextRun.next !== "number") return "Regenerate"
  return "Regenerate · " + formatUsd(nextRun.next)
}

// Recurring spend, projected from a FRESH period rather than from the cost log.
// The log averages real runs, and same-day regenerations in it are mostly
// cache hits, so averaging them reported a daily cost far below the truth —
// $0.89/mo against an actual $5.60/mo in the case that prompted this.
function projectedMonthlyCost(nextRun, frequency) {
  if (!nextRun || typeof nextRun.full !== "number") return null
  // Note `in` rather than a falsy check: manual is 0 runs a month, and `||`
  // would have quietly turned that into the daily default — the one frequency
  // where the answer is "nothing" would have reported the largest number.
  var runs = (frequency in RUNS_PER_MONTH) ? RUNS_PER_MONTH[frequency] : RUNS_PER_MONTH.daily

  // Hourly is the one cadence that runs more than once inside a single period
  // of the READING. astro_engine keys its arc by local date whatever the
  // schedule says, and Stage 1, Stage 1.5 and the visual signature all cache
  // against that key — so the first run of each day pays for a fresh reading
  // and the other 23 only re-roll Stage 2's imagery and the render. Charging
  // all 730 runs at the full rate overstated the month by about 40% at the
  // rates measured here ($19.57 against a real $13.77), which is exactly the
  // kind of invented number this project's cost rule exists to prevent.
  var fullRuns = runs
  var cachedRuns = 0
  if (frequency === "hourly" && typeof nextRun.next === "number") {
    fullRuns = RUNS_PER_MONTH.daily
    cachedRuns = runs - fullRuns
  }

  return {
    perRun: nextRun.full,
    perMonth: fullRuns * nextRun.full + cachedRuns * nextRun.next,
    runsPerMonth: runs,
    scheduled: runs > 0,
    source: nextRun.source
  }
}

// Runs per month for the monthly projection. Months vary; 30.44 is the mean
// Gregorian month, which keeps a "monthly" cadence from reading as 1.0.
// Monthly is exactly 1, not 30.44/30 — the cadence IS one run per month. Manual
// is 0: nothing is scheduled, so there is no recurring cost to project.
var RUNS_PER_MONTH = { hourly: 730.56, daily: 30.44, weekly: 4.35, monthly: 1, manual: 0 }

// ---- Quality presets ------------------------------------------------------
// Which tier the current settings correspond to, or "custom" when they match
// none. DERIVED rather than stored: a stored preset name would drift out of
// sync the moment any individual picker was touched, which is exactly the
// situation this has to detect.
function resolvePresetKey(config, presets) {
  if (!config) return "custom"
  var match = (presets || []).filter(function(p) {
    return p.chat === config.stage1Model && p.chat === config.stage2Model
      && p.image === config.openaiModel && p.quality === config.openaiQuality
  })[0]
  return match ? match.key : "custom"
}

// A tier's label carries the money, because that is the actual decision being
// made — an adjective alone hides the only number that differs between tiers.
// "(est)" vs "(actual)" is never dropped: the cost of a tier nobody has run is
// a projection from published rates and borrowed token counts, and saying so is
// the difference between an estimate and a claim.
function presetLabel(preset) {
  if (!preset) return ""
  var base = preset.label
  if (!preset.available) return base + "  · not available on this key"
  if (typeof preset.runCost !== "number") return base + "  · cost unknown"
  var perMonth = preset.runCost * 30.44
  return base + "  · " + formatUsd(preset.runCost) + "/run  ~" + formatUsd(perMonth) + "/mo ("
    + (preset.runSource === "actual" ? "actual" : "est") + ")"
}

// Cost suffix for an individual model row, with the same est/actual honesty.
function costSuffix(cost, source) {
  if (typeof cost !== "number") return "cost unknown"
  return formatUsd(cost) + " (" + (source === "actual" || source === "measured" ? "actual" : "est") + ")"
}

// The shortlist is the default view; everything else is one toggle away. Never
// a hard filter — hiding a model the key can reach is the dependency this whole
// change exists to remove.
function visibleRows(rows, showAll) {
  if (showAll) return rows || []
  var short = (rows || []).filter(function(r) { return r.shortlisted })
  return short.length > 0 ? short : (rows || [])
}

// Which image rows fit a per-image ceiling. A ceiling of 0 means "no ceiling"
// — the same convention maxCostPerRun already uses. Rows with no known cost are
// KEPT: excluding them would hide every new model behind a number the API will
// never provide, which is the dependency this whole change exists to remove.
function imageRowsWithinCeiling(rows, ceiling) {
  if (!ceiling || ceiling <= 0) return rows || []
  return (rows || []).filter(function(r) {
    return typeof r.cost !== "number" || r.cost <= ceiling
  })
}

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
      frequency: ["hourly", "daily", "weekly", "monthly", "manual"].indexOf(data.frequency) >= 0 ? data.frequency : "manual",
      imageBackend: data.imageBackend === "local" ? "local" : "openai",
      openaiModel: typeof data.openaiModel === "string" ? data.openaiModel : "",
      openaiQuality: typeof data.openaiQuality === "string" ? data.openaiQuality : "",
      stage1Model: typeof data.stage1Model === "string" && data.stage1Model !== "" ? data.stage1Model : "gpt-4o-mini",
      stage2Model: typeof data.stage2Model === "string" && data.stage2Model !== "" ? data.stage2Model : "gpt-4o-mini",
      backgroundSize: data.backgroundSize === "auto" || (typeof data.backgroundSize === "string" && SIZE_PATTERN.test(data.backgroundSize)) ? data.backgroundSize : "auto",
      artStyle: typeof data.artStyle === "string" && data.artStyle !== "" ? data.artStyle : "symbolist",
      themeGenerator: data.themeGenerator === "aether" ? "aether" : "built-in",
      historyRetentionDays: Number.isInteger(data.historyRetentionDays) && data.historyRetentionDays >= 1 && data.historyRetentionDays <= 3650
        ? data.historyRetentionDays : 30,
      // 0 (or anything unparseable) means no ceiling, matching maxCostPerRun.
      maxCostPerImage: typeof data.maxCostPerImage === "number" && data.maxCostPerImage >= 0
        ? data.maxCostPerImage : 0,
      // Falls back to the legacy imageBackend so an existing config keeps
      // working, including one that still says "local".
      provider: typeof data.provider === "string" && data.provider !== ""
        ? data.provider : (typeof data.imageBackend === "string" && data.imageBackend !== "" ? data.imageBackend : "openai")
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
// Legacy "Themes Generated" titles led with the cadence and trailed with the
// image backend — "Daily · Sep 12, 5:16 PM (openai)". New entries carry
// neither (see astro-arc-generate's review_label), so this trims them off the
// entries already on disk: an existing history then reads exactly like
// anything generated from now on, without rewriting index.json underneath
// someone. Matches only the four known cadence words and the two known
// backends, so it can never bite a chunk out of a real style name.
function normalizeReviewLabel(label) {
  var out = String(label)
    .replace(/^(Daily|Weekly|Monthly|Manual)\s*\u00b7\s*/, "")
    .replace(/\s*\((openai|local)\)\s*$/, "")
    .trim()
  return out === "" ? String(label) : out
}

function parseReviewsIndex(raw) {
  try {
    var data = JSON.parse(String(raw || "[]"))
    if (!Array.isArray(data)) return []
    return data.filter(function(r) { return r && r.id && r.path }).map(function(r) {
      var size = Number.isInteger(r.sizeBytes) ? r.sizeBytes : parseInt(r.sizeBytes, 10)
      return {
        id: String(r.id),
        label: normalizeReviewLabel(r.label || "Untitled"),
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
  var mb = kb / 1024
  // GB became reachable with the Hourly schedule: 30 days of hourly entries
  // projects to ~5GB, and "5068.7 MB" is a number a reader has to stop and
  // convert before it means anything.
  if (mb < 1024) return mb.toFixed(1) + " MB"
  return (mb / 1024).toFixed(1) + " GB"
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
  var intervalDays = FREQUENCY_INTERVAL_DAYS[frequency]
  // No cadence means nothing to project from: on "manual" the number of entries
  // depends entirely on how often you click, so report the average size and say
  // the total is unknown rather than answering as if it were daily — which is
  // what `|| 1` used to do, and it was the most alarming possible answer.
  if (!intervalDays) {
    return { estimatedBytes: null, knownCount: known.length, avgBytes: avgBytes, noCadence: true }
  }
  var projectedEntries = Math.max(1, Math.round((retentionDays || 30) / intervalDays))
  return { estimatedBytes: avgBytes * projectedEntries, knownCount: known.length, avgBytes: avgBytes }
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

function pad2(n) {
  return (n < 10 ? "0" : "") + n
}

// ISO 8601 week-numbering date's "%G-W%V" — same key astro_engine.py's
// `date +%G-W%V` derives for a weekly period. ISO weeks start Monday, and
// the year is whichever one owns that week's Thursday, so late-December/
// early-January dates can belong to a week numbered under the other
// calendar year (e.g. 2027-01-01 is still ISO week 2026-W53).
function isoWeekKey(date) {
  var d = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  var dayNum = (d.getDay() + 6) % 7 // Mon=0 ... Sun=6
  d.setDate(d.getDate() - dayNum + 3) // Thursday of this ISO week
  var isoYear = d.getFullYear()
  var jan4 = new Date(isoYear, 0, 4)
  var jan4DayNum = (jan4.getDay() + 6) % 7
  var week1Monday = new Date(jan4)
  week1Monday.setDate(jan4.getDate() - jan4DayNum)
  var week = Math.floor(Math.round((d - week1Monday) / 86400000) / 7) + 1
  return isoYear + "-W" + pad2(week)
}

// The period key "now" resolves to for a given frequency, on the *local*
// calendar — mirrors astro-arc-generate's `date +%Y-%m-%d` / `+%G-W%V` /
// `+%Y-%m`, which also run in local time. This is what makes "daily" mean
// "the calendar date changed", not "24 hours since the last run": comparing
// this against lastRun.periodKey is a plain string check with no elapsed-
// time math at all, so 11pm on one date and 1am the next both resolve to
// their own date's key regardless of how few hours separate them.
function currentPeriodKey(frequency, now) {
  var d = now || new Date()
  if (frequency === "weekly") return isoWeekKey(d)
  if (frequency === "monthly") return d.getFullYear() + "-" + pad2(d.getMonth() + 1)
  var day = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
  if (frequency === "hourly") return day + "T" + pad2(d.getHours())
  return day
}

// Does this run already cover the period the given frequency is in right now?
//
// The run's own stored periodKey CANNOT answer that, and comparing against it
// was a real bug: the key was written under whatever frequency was set at the
// time, so a run filed as "2026-09-13" under Daily, compared against "2026-09"
// once the dropdown moved to Monthly, looks like a period that has never been
// generated — and a paid render started as the immediate consequence of
// changing a setting. Re-keying the run's TIMESTAMP under the frequency in
// effect now is the comparison that actually means "already covered".
//
// Falls back to the stored key for a run old enough to predate generatedAt, or
// one carrying a timestamp that won't parse.
function runCoversPeriod(lastRun, frequency, now) {
  if (!lastRun) return false
  var currentKey = currentPeriodKey(frequency, now)
  var generated = lastRun.generatedAt ? new Date(lastRun.generatedAt) : null
  if (generated && !isNaN(generated.getTime()))
    return currentPeriodKey(frequency, generated) === currentKey
  return lastRun.periodKey === currentKey
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
    PROVIDER_CHOICES: PROVIDER_CHOICES,
    FREQUENCY_CHOICES: FREQUENCY_CHOICES,
    chatModelLabel: chatModelLabel,
    imageModelLabel: imageModelLabel,
    formatUsd: formatUsd,
    nextRunLabel: nextRunLabel,
    projectedMonthlyCost: projectedMonthlyCost,
    imageRowsWithinCeiling: imageRowsWithinCeiling,
    resolvePresetKey: resolvePresetKey,
    presetLabel: presetLabel,
    costSuffix: costSuffix,
    visibleRows: visibleRows,
    openaiModelDropdownValue: openaiModelDropdownValue,
    formatDateDigits: formatDateDigits,
    formatTimeDigits: formatTimeDigits,
    parseConfig: parseConfig,
    parseLastRun: parseLastRun,
    parseApiKeyStatus: parseApiKeyStatus,
    parseReviewsIndex: parseReviewsIndex,
    normalizeReviewLabel: normalizeReviewLabel,
    parseGeocodingResults: parseGeocodingResults,
    locationCommit: locationCommit,
    isValidDate: isValidDate,
    isValidTime: isValidTime,
    isValidBackgroundSize: isValidBackgroundSize,
    isValidHistoryRetentionDays: isValidHistoryRetentionDays,
    isValidCoordinate: isValidCoordinate,
    currentPeriodKey: currentPeriodKey,
    runCoversPeriod: runCoversPeriod,
    formatGeneratedAt: formatGeneratedAt,
    sumCosts: sumCosts,
    formatBytes: formatBytes,
    estimateHistorySpace: estimateHistorySpace,
    suggestThemeName: suggestThemeName
  }
}
