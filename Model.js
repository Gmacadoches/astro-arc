// Pure logic for the Astro-Arc panel: last-run parsing, geocoding
// parsing (mirrors the weather plugin's Model.js), and small validators.
// Kept side-effect free and module.exports-able so it can be unit tested
// the same way weather/Model.js is.

// The Style dropdown is built from the theme files themselves
// (backend/bin/style_catalog.py): shipped themes in backend/pipeline/styles/,
// the user's own in ~/.config/astro-arc/styles/. Only the default key is
// known here; it is what a config with no style, or with a key whose theme is
// gone, falls back to (llm_pipeline.DEFAULT_STYLE says the same).
var DEFAULT_ART_STYLE = "symbolist"

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

// "Every N hours" under Hourly. Divisors of 24 only, mirroring
// period_key.py's INTERVAL_HOURS: the slots then line up with the clock
// (every 3 hours is 00, 03, 06 ...) and the last one of the day is as long as
// the rest. 1 is plain hourly, with the same keys it always had.
var INTERVAL_HOURS_CHOICES = [1, 2, 3, 4, 6, 8, 12]

function intervalHours(value) {
  var n = parseInt(value, 10)
  return INTERVAL_HOURS_CHOICES.indexOf(n) >= 0 ? n : 1
}

// The cadence in words, for text like "/month at every 3 hours".
function scheduleLabel(frequency, hours) {
  if (frequency !== "hourly") return String(frequency)
  var n = intervalHours(hours)
  return n === 1 ? "hourly" : "every " + n + " hours"
}

// "aether" shells out to Omarchy's own theme generator for a richer theme
// (it's what fixes the file-manager icon color, among other things — see
// palette_extract.py's icons.theme comment) and silently falls back to
// "built-in" if aether is missing or fails, so picking it never risks a
// broken generation.
// The global provider. Everything else in the panel is scoped to it: which key
// you paste, which models are offered, what they cost. OpenRouter fronts
// OpenAI's models and many others behind one key; see backend/bin/providers.py.
// (Supersedes the old imageBackend picker, which offered local Stable
// Diffusion; that path is gone.)
var PROVIDER_CHOICES = [
  { key: "openai", label: "OpenAI" },
  { key: "openrouter", label: "OpenRouter" }
]

function providerLabel(key) {
  var match = PROVIDER_CHOICES.filter(function(p) { return p.key === key })[0]
  return match ? match.label : "OpenAI"
}

var THEME_GENERATOR_CHOICES = [
  // Labelled "Astro-Arc" rather than "Built-in": from the panel the choice is
  // between this project's own extractor and Omarchy's Aether, and "Built-in"
  // reads as "built into Omarchy", which is the opposite of what it means. The
  // config VALUE stays "built-in" — it is what astro-arc-generate branches on.
  { key: "built-in", label: "Astro-Arc" },
  { key: "aether", label: "Aether (Omarchy)" }
]

// One catalog row's dropdown label. A user theme says so; a theme that fails
// validation stays listed, so a broken file is visible rather than missing.
function styleOptionLabel(row) {
  if (!row) return ""
  var label = row.label || row.key
  if (row.source === "user") label += " (custom)"
  if (!row.valid) label += " (Failed Validation)"
  return label
}

// Shipped themes first, then the user's, each alphabetical by label.
function sortStyleCatalog(rows) {
  return (rows || []).slice().sort(function(a, b) {
    var sa = a.source === "user" ? 1 : 0
    var sb = b.source === "user" ? 1 : 0
    if (sa !== sb) return sa - sb
    return String(a.label).localeCompare(String(b.label))
  })
}

function findStyle(rows, key) {
  for (var i = 0; i < (rows || []).length; i++)
    if (rows[i].key === key) return rows[i]
  return null
}

// The warning a run prints for the same case (llm_pipeline.missing_style_warning).
function missingStyleWarning(key) {
  return "art style '" + key + "' is not an installed theme; using '" + DEFAULT_ART_STYLE + "'"
}

// The Style dropdown's two actions, listed after the themes. Their values can
// never be a theme key (keys are lowercase letters, digits and hyphens).
var STYLE_CREATE = "__create__"
var STYLE_IMPORT = "__import__"

function styleDropdownOptions(rows) {
  return (rows || []).map(function(s) { return { value: s.key, label: styleOptionLabel(s) } })
    .concat([{ value: STYLE_CREATE, label: "Create your own style…" },
             { value: STYLE_IMPORT, label: "Import theme…" }])
}

// A failed theme whose only error is the missing preview: it needs an image,
// not a fix.
function styleOnlyNeedsPreview(row) {
  var errors = (row && row.errors) || []
  return errors.length > 0 && errors.every(function(e) { return e.indexOf(": preview: ") !== -1 })
}

// omarchy-default-agent's names, as Omarchy's own menu shows them.
var AGENT_NAMES = {
  pi: "Pi", omp: "Oh My Pi", opencode: "OpenCode", claude: "Claude Code", codex: "Codex",
  crush: "Crush", grok: "Grok", gemini: "Gemini", openclaw: "OpenClaw", hermes: "Hermes",
  copilot: "GitHub Copilot", muse: "Muse Code", "cursor-agent": "Cursor CLI"
}

// What every label calls the agent: its display name, the raw name for one
// this table doesn't know, and "your agent" until one is detected — so a label
// never names an agent the user doesn't have.
function agentLabel(agent) {
  return AGENT_NAMES[agent] || agent || "your agent"
}

// The agents Astro-Arc will start: those with a mode that asks the user before
// each action, which is how it starts them. Mirrors ASKING_MODE in
// backend/bin/astro-arc-style; any other default agent gets Copy prompt.
var AGENTS_THAT_ASK = ["claude", "codex", "gemini", "grok"]

function agentAsks(agent) {
  return AGENTS_THAT_ASK.indexOf(agent) >= 0
}

// The same at the start of a sentence: "Your agent opens…".
function agentLabelStart(agent) {
  var label = agentLabel(agent)
  return label.charAt(0).toUpperCase() + label.slice(1)
}

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
  if (row.costState === "unmeasured") return row.label + "  · cost known after first render"
  return row.label + "  · " + costSuffix(row.cost, row.costState) + "/image"
}

// What the NEXT generation will charge, given what is already cached. Stage 1
// and Stage 1.5's meaning are cached per period, so a
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
function projectedMonthlyCost(nextRun, frequency, hours) {
  if (!nextRun || typeof nextRun.full !== "number") return null
  // Note `in` rather than a falsy check: manual is 0 runs a month, and `||`
  // would have quietly turned that into the daily default — the one frequency
  // where the answer is "nothing" would have reported the largest number.
  var runs = (frequency in RUNS_PER_MONTH) ? RUNS_PER_MONTH[frequency] : RUNS_PER_MONTH.daily
  if (frequency === "hourly") runs = runs / intervalHours(hours)

  // Hourly is the one cadence that runs more than once inside a single period
  // of the READING. astro_engine keys its arc by local date whatever the
  // schedule says, and Stage 1 and Stage 1.5's meaning both cache
  // against that key — so the first run of each day pays for a fresh reading
  // and the rest (23 of them at every hour) only re-roll the material, Stage 2's imagery and the render. Charging
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
      && (p.reasoning || "default") === (config.chatReasoning || "default")
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
// — 0 means no ceiling. Rows with no known cost are
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

// The theme a generation was made in, as a catalog key, or "".
function reviewStyleKey(review, rows) {
  if (!review) return ""
  if (review.artStyle) return review.artStyle
  var matches = (rows || []).filter(function(r) { return r.label === review.artStyleLabel })
  return matches.length === 1 ? matches[0].key : ""
}

function parseReviewsIndex(raw) {
  try {
    var data = JSON.parse(String(raw || "[]"))
    if (!Array.isArray(data)) return []
    return data.filter(function(r) { return r && r.id && r.path }).map(function(r) {
      var size = Number.isInteger(r.sizeBytes) ? r.sizeBytes : parseInt(r.sizeBytes, 10)
      // thumb is a bare filename beside the entry's index.html (build_review.py
      // writes "thumb.jpg"). Anything with a slash in it is ignored rather than
      // resolved, so the panel never reads outside the entry's own directory.
      // "" on an entry built before thumbnails existed.
      var thumb = typeof r.thumb === "string" && r.thumb !== "" && r.thumb.indexOf("/") === -1 ? r.thumb : ""
      var entryDir = String(r.path).replace(/\/[^\/]*$/, "")
      return {
        id: String(r.id),
        thumbPath: thumb ? entryDir + "/" + thumb : "",
        label: normalizeReviewLabel(r.label || "Untitled"),
        createdAt: String(r.createdAt || ""),
        path: String(r.path),
        cardCount: parseInt(r.cardCount, 10) || 0,
        periodKey: typeof r.periodKey === "string" ? r.periodKey : null,
        hasThemeSnapshot: !!r.hasThemeSnapshot,
        sizeBytes: isNaN(size) ? null : size,
        conceptTags: Array.isArray(r.conceptTags) ? r.conceptTags.filter(function(t) { return typeof t === "string" }) : [],
        // artStyle (the key) is only in entries built after 2026-10-17; older
        // ones have the label alone, which reviewStyleKey matches instead.
        artStyle: typeof r.artStyle === "string" ? r.artStyle : "",
        artStyleLabel: typeof r.artStyleLabel === "string" ? r.artStyleLabel : "",
        // The reading saved with this generation, so browsing back shows the
        // reading that went with each image. "" on entries built before
        // generations.json carried it.
        distillation: typeof r.distillation === "string" ? r.distillation : "",
        narrativePosition: typeof r.narrativePosition === "string" ? r.narrativePosition : "",
        imagePath: r.hasThemeSnapshot ? entryDir + "/theme/backgrounds/background.png" : ""
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
function estimateHistorySpace(reviewsIndex, retentionDays, frequency, hours) {
  var known = (reviewsIndex || []).filter(function(r) { return typeof r.sizeBytes === "number" })
  if (known.length === 0) return { estimatedBytes: null, knownCount: 0 }

  var avgBytes = known.reduce(function(sum, r) { return sum + r.sizeBytes }, 0) / known.length
  var intervalDays = FREQUENCY_INTERVAL_DAYS[frequency]
  if (frequency === "hourly") intervalDays = intervalDays * intervalHours(hours)
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
        longitude: r.longitude,
        // The place's IANA zone. The birth time is civil time THERE, so the
        // chart needs it — and Open-Meteo already knows it, which is what let
        // the timezonefinder package (and its 63 MB polygon database) go.
        timezone: typeof r.timezone === "string" ? r.timezone : ""
      })
    }
    return out
  } catch (e) {
    return []
  }
}

function locationCommit(text, suggestions, selectedIndex) {
  var name = String(text || "").replace(/^\s+|\s+$/g, "")
  if (name === "") return { name: "", latitude: null, longitude: null, timezone: "" }

  var choices = suggestions || []
  var index = Math.max(0, Math.min(parseInt(selectedIndex, 10) || 0, choices.length - 1))
  var suggestion = choices[index]
  if (suggestion) return suggestion

  return { name: name, latitude: null, longitude: null, timezone: "" }
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
    OPENAI_MODEL_CHOICES: OPENAI_MODEL_CHOICES,
    DEFAULT_ART_STYLE: DEFAULT_ART_STYLE,
    styleOptionLabel: styleOptionLabel,
    sortStyleCatalog: sortStyleCatalog,
    findStyle: findStyle,
    reviewStyleKey: reviewStyleKey,
    missingStyleWarning: missingStyleWarning,
    STYLE_CREATE: STYLE_CREATE,
    STYLE_IMPORT: STYLE_IMPORT,
    styleDropdownOptions: styleDropdownOptions,
    styleOnlyNeedsPreview: styleOnlyNeedsPreview,
    agentLabel: agentLabel,
    agentLabelStart: agentLabelStart,
    AGENTS_THAT_ASK: AGENTS_THAT_ASK,
    agentAsks: agentAsks,
    THEME_GENERATOR_CHOICES: THEME_GENERATOR_CHOICES,
    PROVIDER_CHOICES: PROVIDER_CHOICES,
    providerLabel: providerLabel,
    FREQUENCY_CHOICES: FREQUENCY_CHOICES,
    INTERVAL_HOURS_CHOICES: INTERVAL_HOURS_CHOICES,
    intervalHours: intervalHours,
    scheduleLabel: scheduleLabel,
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
    formatGeneratedAt: formatGeneratedAt,
    sumCosts: sumCosts,
    formatBytes: formatBytes,
    estimateHistorySpace: estimateHistorySpace,
    suggestThemeName: suggestThemeName
  }
}
