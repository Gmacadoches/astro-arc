import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Settings + status popup for Astro-Arc. Structurally mirrors the built-in
// weather panel (Panel base class, KeyboardPanel, FileView-backed state,
// Process-driven writes to a small owning script) but for astrology/theme
// config instead of a location + forecast.
Panel {
  id: root
  moduleName: "garrett.astro-arc"
  ipcTarget: "garrett.astro-arc"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property string home: Quickshell.env("HOME")
  readonly property string configBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-config"
  readonly property string generateBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-generate"

  // ---- Lifecycle -----------------------------------------------------
  function open() {
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    syncDraftsFromConfig()
    configFile.reload()
    lastRunFile.reload()
    reviewsIndexFile.reload()
    costsFile.reload()
  }

  function openFromHotkey() {
    open()
  }

  function close() {
    setCenterHoverRevealSuppressed(false)
    if (root.editingLocation) root.cancelEditingLocation()
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  // ---- Config state ----------------------------------------------------
  property var configState: Model.DEFAULT_CONFIG
  property var lastRun: null

  property FileView configFile: FileView {
    path: root.home + "/.local/state/omarchy/settings/astro-arc.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.configState = Model.parseConfig(text())
    onLoadFailed: root.configState = Model.parseConfig("")
  }

  property FileView lastRunFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/last-run.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.lastRun = Model.parseLastRun(text())
    onLoadFailed: root.lastRun = null
  }

  // ---- Birth data ------------------------------------------------------
  // Auto-saves on blur/Enter (onEditingFinished) — no Save button. Time is
  // optional: a blank field means "unknown" rather than needing an explicit
  // toggle for it.
  property string draftBirthDate: ""
  property string draftBirthTime: ""
  property string birthError: ""

  function syncDraftsFromConfig() {
    draftBirthDate = root.configState.birthDate
    draftBirthTime = root.configState.birthTimeUnknown ? "" : root.configState.birthTime
    birthError = ""
    // Fields are plain (unbound) TextFields — see the note on
    // birthDateField/birthTimeField below — so the displayed text needs an
    // imperative push here too, deferred until after the fields exist on
    // first load.
    Qt.callLater(function() {
      birthDateField.text = root.draftBirthDate
      birthTimeField.text = root.draftBirthTime
    })
  }

  function commitBirth() {
    if (draftBirthDate === "") { birthError = ""; return }
    if (!Model.isValidDate(draftBirthDate)) { birthError = "Enter a valid date (YYYY-MM-DD)"; return }
    if (draftBirthTime !== "" && !Model.isValidTime(draftBirthTime)) { birthError = "Enter a valid time (HH:MM), or leave it blank"; return }
    birthError = ""

    var timeArg = draftBirthTime === "" ? "unknown" : draftBirthTime
    var unchanged = draftBirthDate === root.configState.birthDate
      && (draftBirthTime !== "" ? draftBirthTime === root.configState.birthTime : root.configState.birthTimeUnknown)
    if (unchanged) return

    birthWriteProc.command = [root.configBin, "--set-birth", draftBirthDate, timeArg]
    birthWriteProc.running = true
  }

  Process {
    id: birthWriteProc
    onExited: function(exitCode) {
      if (exitCode === 0) root.configFile.reload()
    }
  }

  // ---- Location editing (mirrors weather's click-to-edit + geocode flow)
  property bool editingLocation: false
  property bool savingLocation: false
  property var locationSuggestions: []
  property int suggestionIndex: 0
  property string geocodePendingQuery: ""
  property string geocodeActiveQuery: ""

  function startEditingLocation() {
    editingLocation = true
    savingLocation = false
    locationSuggestions = []
    suggestionIndex = 0
    Qt.callLater(function() {
      locationField.text = root.configState.locationName
      locationField.selectAll()
      locationField.forceActiveFocus()
    })
  }

  function cancelEditingLocation() {
    editingLocation = false
    savingLocation = false
    locationSuggestions = []
    geocodeDebounce.stop()
  }

  function commitLocation() {
    var location = Model.locationCommit(locationField.text, locationSuggestions, suggestionIndex)
    if (location.name === "") {
      clearLocation()
      return
    }
    savingLocation = true
    persistLocation(location.name, location.latitude, location.longitude)
  }

  function clearLocation() {
    savingLocation = true
    persistLocation("", null, null)
  }

  function pickSuggestion(suggestion) {
    if (!suggestion) return
    savingLocation = true
    persistLocation(suggestion.name, suggestion.latitude, suggestion.longitude)
  }

  function persistLocation(name, latitude, longitude) {
    if (name && Model.isValidCoordinate(latitude, longitude))
      locationWriteProc.command = [root.configBin, "--set-location", name, latitude + "," + longitude]
    else if (name)
      locationWriteProc.command = [root.configBin, "--set-location", name]
    else
      locationWriteProc.command = [root.configBin, "--clear-location"]
    locationWriteProc.running = true
  }

  Process {
    id: locationWriteProc
    onExited: function(exitCode) {
      root.savingLocation = false
      if (exitCode === 0) root.configFile.reload()
      root.cancelEditingLocation()
    }
  }

  function requestGeocode() {
    var query = locationField.text.trim()
    if (query.length < 2) {
      locationSuggestions = []
      return
    }
    geocodePendingQuery = query
    if (!geocodeProc.running) startGeocode()
  }

  function startGeocode() {
    geocodeActiveQuery = geocodePendingQuery
    geocodeProc.command = ["curl", "-fsS", "--max-time", "5",
      "https://geocoding-api.open-meteo.com/v1/search?name=" + encodeURIComponent(geocodeActiveQuery) + "&count=5&language=en&format=json"]
    geocodeProc.running = true
  }

  Process {
    id: geocodeProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.locationSuggestions = root.editingLocation ? Model.parseGeocodingResults(text) : []
        root.suggestionIndex = 0
        if (root.geocodePendingQuery !== root.geocodeActiveQuery) Qt.callLater(root.startGeocode)
      }
    }
  }

  Timer {
    id: geocodeDebounce
    interval: 300
    onTriggered: root.requestGeocode()
  }

  // ---- Frequency ------------------------------------------------------
  function commitFrequency(value) {
    frequencyWriteProc.command = [root.configBin, "--set-frequency", value]
    frequencyWriteProc.running = true
  }

  Process {
    id: frequencyWriteProc
    onExited: function(exitCode) {
      if (exitCode === 0) root.configFile.reload()
    }
  }

  Connections {
    target: root
    function onConfigStateChanged() {
      if (frequencyDropdown.value !== root.configState.frequency)
        frequencyDropdown.value = root.configState.frequency
    }
  }

  // ---- Generation -------------------------------------------------------
  property bool generating: false
  property string generateError: ""

  function regenerateNow() {
    if (generating) return
    generating = true
    generateError = ""
    generateProc.running = true
  }

  Process {
    id: generateProc
    command: [root.generateBin]
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.generateError = String(text || "").trim() }
    onExited: function(exitCode) {
      root.generating = false
      if (exitCode === 0) {
        root.generateError = ""
        root.lastRunFile.reload()
      }
    }
  }

  // heroStatus: compact one-line summary shown under the title, matching
  // the built-in bluetooth/network panels' icon+title+status hero row.
  readonly property string heroStatus: root.generating
    ? "Generating…"
    : Model.frequencyLabel(root.configState.frequency) + (root.lastRun ? " — up to date" : " — not generated yet")

  // ---- Settings sub-view (image backend/model, Stage 1/2 models, API
  // keys) -------------------------------------------------------------------
  // A second state of this same panel (toggled by the hero's gear icon)
  // rather than a separate Panel.qml — everything about anchoring/focus/
  // popout-coordination is already solved for this panel, and duplicating
  // that just to host a handful of extra rows isn't worth it.
  property bool showSettings: false
  readonly property string apiKeyBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-apikey"
  readonly property string openaiImageGenBin: home + "/.local/share/omarchy/astro-arc/bin/openai_image_gen.py"
  readonly property string venvPython: home + "/.local/share/omarchy/astro-arc/venv/bin/python3"

  function toggleSettings() {
    showSettings = !showSettings
    if (showSettings) {
      refreshApiKeyStatus("stage1")
      refreshApiKeyStatus("stage2")
      refreshApiKeyStatus("image")
    } else {
      setApiKeyField("stage1", "replacing", false)
      setApiKeyField("stage2", "replacing", false)
      setApiKeyField("image", "replacing", false)
    }
  }

  // ---- API key status, three independent slots (stage1/stage2/image —
  // see astro-arc-apikey) — masked reference only, the real key is never
  // in any QML property, config file, or process argv; see the store/
  // lookup Processes below. Kept as one map property (rather than three
  // flat ones) so the reusable ApiKeySection component below can index it
  // by `slot` instead of needing three near-identical copies of itself.
  // QML's change notification is shallow, so every update replaces the
  // whole map (setApiKeyField) rather than mutating a nested field in
  // place, which wouldn't trigger bindings to refresh.
  property var apiKeyState: ({
    stage1: { present: false, masked: "", saving: false, validating: false, validation: null, replacing: false },
    stage2: { present: false, masked: "", saving: false, validating: false, validation: null, replacing: false },
    image: { present: false, masked: "", saving: false, validating: false, validation: null, replacing: false }
  })
  property string stage1Draft: ""
  property string stage2Draft: ""
  property string imageDraft: ""

  function setApiKeyField(slot, field, value) {
    var next = {}
    for (var k in root.apiKeyState) next[k] = root.apiKeyState[k]
    var patch = {}
    patch[field] = value
    next[slot] = Object.assign({}, next[slot], patch)
    root.apiKeyState = next
  }

  function refreshApiKeyStatus(slot) {
    // Dedicated Process per slot (not shared) because toggleSettings()
    // fires all three at once — a shared Process would have the second
    // and third calls clobber the first's in-flight command/slot tag.
    if (slot === "stage1") stage1StatusProc.running = true
    else if (slot === "stage2") stage2StatusProc.running = true
    else imageStatusProc.running = true
  }

  Process {
    id: stage1StatusProc
    command: [root.apiKeyBin, "status", "stage1"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus("stage1", text) }
  }
  Process {
    id: stage2StatusProc
    command: [root.apiKeyBin, "status", "stage2"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus("stage2", text) }
  }
  Process {
    id: imageStatusProc
    command: [root.apiKeyBin, "status", "image"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus("image", text) }
  }

  function applyStatus(slot, text) {
    var parsed = Model.parseApiKeyStatus(text)
    var next = {}
    for (var k in root.apiKeyState) next[k] = root.apiKeyState[k]
    next[slot] = Object.assign({}, next[slot], { present: parsed.present, masked: parsed.masked })
    root.apiKeyState = next
  }

  // The key goes over stdin straight into secret-tool, the same pattern
  // the built-in network panel uses for Wi-Fi/802.1X passphrases (see its
  // Panel.qml: "The password goes over stdin, never argv."). No shell, no
  // bash variable, no argv — QML hands secret-tool its stdin directly, and
  // the per-slot draft (the only QML property that ever holds the key) is
  // cleared in the same tick it's written.
  //
  // Shared across all three slots (unlike status above) since saving is a
  // single deliberate user click, not something all three fire at once —
  // a real collision would need two saves within the same ~second.
  property string activeKeySlot: ""

  function draftFor(slot) {
    if (slot === "stage1") return root.stage1Draft
    if (slot === "stage2") return root.stage2Draft
    return root.imageDraft
  }

  function clearDraft(slot) {
    if (slot === "stage1") root.stage1Draft = ""
    else if (slot === "stage2") root.stage2Draft = ""
    else root.imageDraft = ""
  }

  function accountForSlot(slot) {
    return slot === "stage1" ? "openai-api-key-stage1" : slot === "stage2" ? "openai-api-key-stage2" : "openai-api-key"
  }

  function saveApiKey(slot) {
    var draft = draftFor(slot)
    if (draft.length === 0) return
    root.activeKeySlot = slot
    root.setApiKeyField(slot, "saving", true)
    // This Process is shared across all three key slots. Its previous run
    // (if any) left stdinEnabled false after closing the pipe post-write
    // (see that onStarted handler) — per the Process type's own docs,
    // write() silently no-ops once stdinEnabled is false, "even if set
    // back to true" *while running*, but a fresh run reads it at start,
    // so it must be re-armed here, before this run starts. Confirmed by
    // reproducing both the hang and this fix in an isolated standalone
    // Quickshell process outside the real panel.
    apiKeyStoreProc.stdinEnabled = true
    apiKeyStoreProc.command = ["secret-tool", "store", "--label=Astro-Arc OpenAI API Key (" + slot + ")", "service", "astro-arc", "account", root.accountForSlot(slot)]
    apiKeyStoreProc.running = true
  }

  Process {
    id: apiKeyStoreProc
    stdinEnabled: true
    onStarted: {
      write(root.draftFor(root.activeKeySlot) + "\n")
      root.clearDraft(root.activeKeySlot)
      // secret-tool store reads stdin until EOF, not just until the
      // newline above — Quickshell's Process.write() never closes the
      // pipe on its own, so without this it hangs forever (reproduced
      // and confirmed in isolation: a standalone Process running
      // `secret-tool store` never exited until this was added). Setting
      // stdinEnabled false is what actually closes the write end — see
      // the Process type's own doc comment on that property.
      stdinEnabled = false
    }
    onExited: function(exitCode) {
      var slot = root.activeKeySlot
      root.setApiKeyField(slot, "saving", false)
      root.setApiKeyField(slot, "replacing", false)
      root.refreshApiKeyStatus(slot)
      if (exitCode === 0) root.validateApiKey(slot)
    }
  }

  function clearApiKey(slot) {
    root.activeKeySlot = slot
    apiKeyClearProc.command = [root.apiKeyBin, "clear", slot]
    apiKeyClearProc.running = true
  }

  Process {
    id: apiKeyClearProc
    onExited: function() {
      var slot = root.activeKeySlot
      root.setApiKeyField(slot, "validation", null)
      root.refreshApiKeyStatus(slot)
    }
  }

  // The one cheap validation call (GET /v1/models, free) confirming a
  // freshly-saved key actually authenticates. Never surfaces the key —
  // only ok/message, built entirely inside openai_image_gen.py.
  function validateApiKey(slot) {
    root.activeKeySlot = slot
    root.setApiKeyField(slot, "validating", true)
    apiKeyValidateProc.command = [root.venvPython, root.openaiImageGenBin, "--validate", slot]
    apiKeyValidateProc.running = true
  }

  Process {
    id: apiKeyValidateProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var slot = root.activeKeySlot
        root.setApiKeyField(slot, "validating", false)
        try { root.setApiKeyField(slot, "validation", JSON.parse(text)) }
        catch (e) { root.setApiKeyField(slot, "validation", { ok: false, message: "Validation call failed." }) }
      }
    }
  }

  // ---- Image backend + OpenAI model/quality ------------------------------
  function commitImageBackend(value) {
    imageBackendWriteProc.command = [root.configBin, "--set-image-backend", value]
    imageBackendWriteProc.running = true
  }

  Process {
    id: imageBackendWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  function commitOpenaiModel(compositeValue) {
    var parts = compositeValue.split("|")
    openaiModelWriteProc.command = [root.configBin, "--set-openai-model", parts[0], parts[1] || ""]
    openaiModelWriteProc.running = true
  }

  Process {
    id: openaiModelWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- Stage 1 / Stage 2 chat models — separate from the image-render
  // model above, and from each other, since interpreting a chart and
  // writing an image prompt are different tasks that may call for
  // different models (and different API keys — see apiKeyState). ---------
  function commitStage1Model(value) {
    if (!value) return
    stage1ModelWriteProc.command = [root.configBin, "--set-stage1-model", value]
    stage1ModelWriteProc.running = true
  }

  Process {
    id: stage1ModelWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  function commitStage2Model(value) {
    if (!value) return
    stage2ModelWriteProc.command = [root.configBin, "--set-stage2-model", value]
    stage2ModelWriteProc.running = true
  }

  Process {
    id: stage2ModelWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- Background size — "auto" (default) re-detects the focused
  // monitor at generation time; a fixed "WxH" pins it. Either way,
  // astro-arc-generate resolves this into the exact final background
  // size regardless of what either image backend natively renders at
  // (see image_fit.py's fit_cover). --------------------------------------
  property string backgroundSizeError: ""
  readonly property string detectResolutionBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-detect-resolution"

  function commitBackgroundSize(value) {
    if (!Model.isValidBackgroundSize(value)) {
      backgroundSizeError = "Enter \"auto\" or WIDTHxHEIGHT (e.g. 1920x1080)"
      return
    }
    backgroundSizeError = ""
    backgroundSizeWriteProc.command = [root.configBin, "--set-background-size", value]
    backgroundSizeWriteProc.running = true
  }

  Process {
    id: backgroundSizeWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  function detectBackgroundSize() {
    detectResolutionProc.running = true
  }

  Process {
    id: detectResolutionProc
    command: [root.detectResolutionBin]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var size = String(text || "").trim()
        if (Model.isValidBackgroundSize(size)) {
          backgroundSizeField.text = size
          root.commitBackgroundSize(size)
        } else {
          root.backgroundSizeError = "Couldn't detect the current resolution"
        }
      }
    }
  }

  // ---- Art style: which pipeline/styles.toml preset Stage 2's prompt is
  // suffixed with. -----------------------------------------------------
  function commitArtStyle(value) {
    artStyleWriteProc.command = [root.configBin, "--set-art-style", value]
    artStyleWriteProc.running = true
  }

  Process {
    id: artStyleWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- Theme generator: "built-in" (palette_extract.py, default) or
  // "aether" (shells out to Omarchy's own theme generator for a richer
  // theme — see palette_extract.py's icons.theme comment for why the
  // built-in one alone left the file manager's icon color never changing).
  // astro-arc-generate falls back to built-in automatically if aether is
  // missing or fails, so picking it here never risks a broken generation.
  function commitThemeGenerator(value) {
    themeGeneratorWriteProc.command = [root.configBin, "--set-theme-generator", value]
    themeGeneratorWriteProc.running = true
  }

  Process {
    id: themeGeneratorWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- History retention: how many days of "Themes Generated" entries
  // astro-arc-generate keeps before astro-arc-prune-history deletes them,
  // every run. Never affects a theme already exported via Save Selected
  // Theme — that's a separate, permanent copy this retention window has
  // no path to. -----------------------------------------------------------
  property string historyRetentionError: ""

  function commitHistoryRetentionDays(value) {
    if (!Model.isValidHistoryRetentionDays(value)) {
      historyRetentionError = "Enter a whole number of days, 1-3650"
      return
    }
    historyRetentionError = ""
    historyRetentionWriteProc.command = [root.configBin, "--set-history-retention-days", String(parseInt(value, 10))]
    historyRetentionWriteProc.running = true
  }

  Process {
    id: historyRetentionWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- API usage: a running total from astro-arc-generate's costs.json
  // (real token-based cost for every generation's 3 API calls — see
  // cost_estimate.py), plus a best-effort "available funds" check that's
  // expected to fail with a regular project key (that needs an Admin API
  // key's api.usage.read scope, confirmed empirically against OpenAI's
  // org endpoints — this isn't a bug, it's what OpenAI actually allows).
  property var costsSummary: ({ total: 0, unknownCount: 0, generationCount: 0 })
  property var fundsCheck: null
  property bool fundsChecking: false

  property FileView costsFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/pipeline/costs.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.costsSummary = Model.sumCosts(text())
    onLoadFailed: root.costsSummary = { total: 0, unknownCount: 0, generationCount: 0 }
  }

  function checkFunds() {
    fundsChecking = true
    fundsCheckProc.running = true
  }

  Process {
    id: fundsCheckProc
    command: [root.venvPython, root.openaiImageGenBin, "--check-funds", "image"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.fundsChecking = false
        root.fundsCheck = Model.parseFundsCheck(text)
      }
    }
  }

  // ---- Themes Generated: every real generation, browsable and — since
  // astro-arc-generate now snapshots the live theme dir into each entry —
  // exportable as a standalone theme (astro-arc-save-theme) before the
  // configured retention window prunes it (astro-arc-prune-history). -----
  property var reviewsIndex: []
  property string selectedReviewId: ""

  property FileView reviewsIndexFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/reviews/index.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.reviewsIndex = Model.parseReviewsIndex(text())
      // Auto-pruning can now remove the currently-selected entry out from
      // under the user — re-picking the newest survivor (or clearing to
      // "" when none are left) beats leaving selectedReviewId pointing at
      // deleted data, which is a case the original "only fill in when
      // empty" logic never had to consider.
      var stillExists = root.reviewsIndex.some(function(r) { return r.id === root.selectedReviewId })
      if (!stillExists) root.selectedReviewId = root.reviewsIndex.length > 0 ? root.reviewsIndex[0].id : ""
    }
    onLoadFailed: root.reviewsIndex = []
  }

  function reviewPathForId(id) {
    for (var i = 0; i < root.reviewsIndex.length; i++)
      if (root.reviewsIndex[i].id === id) return root.reviewsIndex[i].path
    return ""
  }

  function openReview(path) {
    if (path) Quickshell.execDetached(["xdg-open", path])
  }

  function openSelectedReview() {
    openReview(reviewPathForId(selectedReviewId))
  }

  function openLatestReview() {
    if (reviewsIndex.length > 0) openReview(reviewsIndex[0].path)
  }

  // ---- Save Selected Theme: exports the selected entry's theme snapshot
  // to a new, permanent Omarchy theme (astro-arc-save-theme) — outside
  // both the live astro-arc theme (overwritten every generation) and the
  // retention window (pruned by age), so it's the only way a specific
  // generation survives either. Prompts for a name first (pre-filled with
  // a suggestion from that generation's own concept tags via
  // Model.suggestThemeName) instead of silently auto-naming it
  // astro-arc-saved-<hash-looking-review-id>. -----------------------------
  property string themeActionStatus: ""
  property bool themeActionStatusIsError: false
  property bool themeNamePromptOpen: false
  property string themeNameDraft: ""
  property string _saveThemeStdout: ""
  property string _saveThemeStderr: ""
  readonly property string saveThemeBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-save-theme"
  readonly property string pruneHistoryBin: home + "/.local/share/omarchy/astro-arc/bin/astro-arc-prune-history"

  function reviewForId(id) {
    for (var i = 0; i < root.reviewsIndex.length; i++)
      if (root.reviewsIndex[i].id === id) return root.reviewsIndex[i]
    return null
  }

  function openSaveThemePrompt() {
    if (!root.selectedReviewId) return
    var review = root.reviewForId(root.selectedReviewId)
    root.themeNameDraft = review ? Model.suggestThemeName(review.conceptTags) : ""
    root.themeActionStatus = ""
    root.themeNamePromptOpen = true
  }

  function cancelSaveThemePrompt() {
    root.themeNamePromptOpen = false
    root.themeActionStatus = ""
  }

  function confirmSaveTheme() {
    if (!root.selectedReviewId || saveThemeProc.running) return
    var name = root.themeNameDraft.trim()
    if (name === "") {
      root.themeActionStatus = "Enter a name for the theme."
      root.themeActionStatusIsError = true
      return
    }
    root.themeActionStatus = ""
    root._saveThemeStdout = ""
    root._saveThemeStderr = ""
    saveThemeProc.command = [root.saveThemeBin, root.selectedReviewId, name]
    saveThemeProc.running = true
  }

  Process {
    id: saveThemeProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root._saveThemeStdout = String(text || "").trim() }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root._saveThemeStderr = String(text || "").trim() }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.themeNamePromptOpen = false
        root.themeActionStatus = "Saved as “" + root._saveThemeStdout + "” — pick it from Omarchy's own theme switcher any time."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = root._saveThemeStderr || "Save failed."
        root.themeActionStatusIsError = true
      }
    }
  }

  // ---- Clear History: deletes every "Themes Generated" entry right now
  // (astro-arc-prune-history --all), regardless of age. Two-click confirm
  // — arms on the first click, reverts on its own after 4s if not
  // confirmed — since this is real, unrecoverable deletion and the
  // existing "Remove" (API key) button has no equivalent safeguard to
  // mirror. Never touches a theme astro-arc-save-theme already exported;
  // that's a permanent copy outside this whole system. ---------------------
  property bool clearHistoryArmed: false

  function clearHistoryClicked() {
    if (clearHistoryProc.running) return
    if (root.clearHistoryArmed) {
      root.clearHistoryArmed = false
      clearHistoryArmTimer.stop()
      root.themeActionStatus = ""
      clearHistoryProc.running = true
    } else {
      root.clearHistoryArmed = true
      clearHistoryArmTimer.restart()
    }
  }

  Timer {
    id: clearHistoryArmTimer
    interval: 4000
    onTriggered: root.clearHistoryArmed = false
  }

  Process {
    id: clearHistoryProc
    command: [root.pruneHistoryBin, "--all"]
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.reviewsIndexFile.reload()
        root.themeActionStatus = "Themes Generated history cleared."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = "Clear failed."
        root.themeActionStatusIsError = true
      }
    }
  }

  // Shared label-column width so every label-left/control-right row lines
  // up, the same way a settings form's labels would.
  // Wide enough for "Schedule", the longest of the row labels — a narrower
  // column let that label's text run past its own box and butt straight
  // into the control beside it.
  readonly property int labelColW: Style.space(64)

  // Reusable key-management block for one API slot (stage1/stage2/image):
  // masked-reference display, replace/remove, password input, and
  // validation status. Used identically for all three settings sections
  // below instead of tripling ~90 lines of near-identical QML — that
  // duplication is exactly how the earlier null-guard bug (one copy fixed,
  // a second one wouldn't have been) would have happened again with three
  // copies instead of one. Inline `component` declarations resolve `root`
  // by id the same way the built-in agents panel's LimitRow/DayRow do
  // (confirmed by reading its source), so this can call straight back into
  // root's slot-parameterized functions without threading callbacks.
  component ApiKeySection: Column {
    id: section
    required property string slot
    readonly property var keyState: root.apiKeyState[slot]

    width: parent.width
    spacing: Style.space(6)

    // ---- Key present, not replacing: masked reference only. The full
    // key is never displayed. ------------------------------------------
    Item {
      width: parent.width
      visible: section.keyState.present && !section.keyState.replacing
      height: Math.max(statusText.implicitHeight, replaceBtn.implicitHeight)

      Text {
        id: statusText
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: section.keyState.masked
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.bodySmall
        color: root.bar.foreground
      }

      Row {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)

        Button {
          id: replaceBtn
          text: "Replace"
          bordered: true
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          onClicked: {
            root.setApiKeyField(section.slot, "replacing", true)
            root.setApiKeyField(section.slot, "validation", null)
          }
        }
        Button {
          text: "Remove"
          bordered: true
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          onClicked: root.clearApiKey(section.slot)
        }
      }
    }

    // ---- No key yet, or actively replacing it: masked/password input.
    // Field text is never bound to the draft the normal way, for the same
    // reason birthDateField isn't (see its comment) — onTextChanged
    // pushes one way, and the field is cleared imperatively once saved.
    Column {
      width: parent.width
      spacing: Style.space(6)
      visible: !section.keyState.present || section.keyState.replacing

      Row {
        width: parent.width
        spacing: Style.space(8)

        TextField {
          id: keyField
          width: parent.width - saveKeyBtn.width - Style.space(8)
          password: true
          enabled: !section.keyState.saving
          placeholderText: "sk-…"
          foreground: root.bar.foreground
          font.family: root.bar.fontFamily
          onTextChanged: {
            if (section.slot === "stage1") root.stage1Draft = text
            else if (section.slot === "stage2") root.stage2Draft = text
            else root.imageDraft = text
          }
          Keys.onReturnPressed: root.saveApiKey(section.slot)
        }

        Button {
          id: saveKeyBtn
          text: section.keyState.saving ? "Saving…" : "Save"
          bordered: true
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          enabled: !section.keyState.saving && root.draftFor(section.slot).length > 0
          onClicked: root.saveApiKey(section.slot)
        }
      }

      Button {
        visible: section.keyState.replacing
        text: "Cancel"
        bordered: true
        fontSize: Style.font.caption
        foreground: root.bar.foreground
        onClicked: {
          root.setApiKeyField(section.slot, "replacing", false)
          keyField.text = ""
          root.clearDraft(section.slot)
        }
      }
    }

    // ---- Validation status — never shows the key itself, only whether
    // the last save authenticated. ---------------------------------------
    Text {
      visible: section.keyState.validating || section.keyState.validation !== null
      width: parent.width
      wrapMode: Text.WordWrap
      text: section.keyState.validating
        ? "Validating…"
        : section.keyState.validation
          ? (section.keyState.validation.ok ? "✓ " : "✗ ") + section.keyState.validation.message
          : ""
      color: section.keyState.validating
        ? Qt.darker(root.bar.foreground, 1.5)
        : (section.keyState.validation && section.keyState.validation.ok ? "#a6e3a1" : (root.bar.urgent || "#f38ba8"))
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  KeyboardPanel {
    id: panel
    // No centerOnBar: Astro-Arc lives in the bar's right section, so the
    // panel should hang off its own icon there (like bluetooth/network/
    // audio) rather than center on the bar (which is how the built-in
    // weather widget — a center-section widget — behaves; copying that
    // default here is what put the popup at top-center instead of
    // top-right).
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    // Same 380px as the agents/bluetooth/network/audio panels, so Astro-Arc
    // reads as one of the family instead of a narrower one-off.
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.editingLocation
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          // No manual padding here: KeyboardPanel's card already reserves
          // its own padding (Style.spacing.popupPadding) around
          // contentHolder. Rows below use label-left/control-right (an
          // Item with the label anchored left, the control anchored
          // right) instead of stacking a header above its control —
          // there's horizontal room in a 320px panel that stacking wasted.
          width: scroll.width
          spacing: Style.space(8)

          // ---- Hero: icon · title · status — the shared PanelHero, same
          // as every other built-in popup uses ------------------------------
          PanelHero {
            width: parent.width
            title: root.showSettings ? "Settings" : "Astro-Arc"
            meta: root.showSettings ? "" : root.heroStatus
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            iconComponent: Component {
              Text {
                text: root.showSettings ? "‹" : (root.generating ? "✵" : "✦")
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.display

                // Was purely decorative — a "‹" that looks exactly like a
                // back button but did nothing when clicked. Same action as
                // the trailing ✕ below; only live while it's actually
                // showing the back chevron, not over the ✦/✵ status icon.
                MouseArea {
                  anchors.fill: parent
                  enabled: root.showSettings
                  cursorShape: root.showSettings ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: root.toggleSettings()
                }
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: root.showSettings ? "✕" : "⚙"
                fontSize: Style.font.body
                foreground: root.bar.foreground
                tooltipText: root.showSettings ? "Close settings" : "Settings"
                onClicked: root.toggleSettings()
              }
            }
          }

          PanelSeparator { foreground: root.bar.foreground }

          // ================== SETTINGS VIEW ==================
          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.showSettings

            // ---- Stage 1: interpretation (chart -> psychological reading)
            PanelSectionHeader { text: "STAGE 1 · INTERPRETATION"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(stage1ModelLabel.implicitHeight, stage1ModelField.implicitHeight)

              Text {
                id: stage1ModelLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Model"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              // Freeform, not a dropdown of presets: OpenAI's chat-model
              // catalog changes often enough that a hardcoded list here
              // would just as likely be stale as helpful.
              TextField {
                id: stage1ModelField
                anchors.left: stage1ModelLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "gpt-4o-mini"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onEditingFinished: root.commitStage1Model(text)
                Component.onCompleted: text = root.configState.stage1Model
                Connections {
                  target: root
                  function onConfigStateChanged() {
                    if (!stage1ModelField.activeFocus) stage1ModelField.text = root.configState.stage1Model
                  }
                }
              }
            }

            ApiKeySection { slot: "stage1" }

            PanelSeparator { foreground: root.bar.foreground }

            // ---- Stage 2: image prompt (reading -> imagery + concept tags)
            PanelSectionHeader { text: "STAGE 2 · IMAGE PROMPT"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(stage2ModelLabel.implicitHeight, stage2ModelField.implicitHeight)

              Text {
                id: stage2ModelLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Model"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              TextField {
                id: stage2ModelField
                anchors.left: stage2ModelLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "gpt-4o-mini"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onEditingFinished: root.commitStage2Model(text)
                Component.onCompleted: text = root.configState.stage2Model
                Connections {
                  target: root
                  function onConfigStateChanged() {
                    if (!stage2ModelField.activeFocus) stage2ModelField.text = root.configState.stage2Model
                  }
                }
              }
            }

            ApiKeySection { slot: "stage2" }

            PanelSeparator { foreground: root.bar.foreground }

            // ---- Image generation: the actual picture-rendering call,
            // independent of the two chat stages above -------------------
            PanelSectionHeader { text: "IMAGE GENERATION"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(backendLabel.implicitHeight, backendDropdown.implicitHeight)

              Text {
                id: backendLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Backend"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: backendDropdown
                anchors.left: backendLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(140)
                showLabel: false
                value: root.configState.imageBackend
                options: [
                  { value: "local", label: "Local (free)" },
                  { value: "openai", label: "OpenAI API" }
                ]
                foreground: root.bar.foreground
                onChanged: function(value) { root.commitImageBackend(value) }
              }
            }

            // ---- Background size: applies to whichever backend renders
            // the image — image_fit.py crops/scales either backend's
            // native output to this exact size afterward. -----------------
            Item {
              width: parent.width
              height: Math.max(backgroundSizeLabel.implicitHeight, backgroundSizeField.implicitHeight)

              Text {
                id: backgroundSizeLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Background"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              TextField {
                id: backgroundSizeField
                anchors.left: backgroundSizeLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: detectSizeBtn.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "auto"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onEditingFinished: root.commitBackgroundSize(text)
                Component.onCompleted: text = root.configState.backgroundSize
                Connections {
                  target: root
                  function onConfigStateChanged() {
                    if (!backgroundSizeField.activeFocus) backgroundSizeField.text = root.configState.backgroundSize
                  }
                }
              }

              Button {
                id: detectSizeBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Auto-detect"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                onClicked: root.detectBackgroundSize()
              }
            }

            Text {
              visible: root.backgroundSizeError !== ""
              width: parent.width
              text: root.backgroundSizeError
              color: root.bar.urgent || "#f38ba8"
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            Item {
              width: parent.width
              visible: root.configState.imageBackend === "openai"
              height: Math.max(modelLabel.implicitHeight, modelDropdown.implicitHeight)

              Text {
                id: modelLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Model"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: modelDropdown
                anchors.left: modelLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                showLabel: false
                value: Model.openaiModelDropdownValue(root.configState.openaiModel, root.configState.openaiQuality)
                options: Model.OPENAI_MODEL_CHOICES.map(function(c) {
                  return { value: Model.openaiModelDropdownValue(c.model, c.quality), label: c.label }
                })
                foreground: root.bar.foreground
                onChanged: function(value) { root.commitOpenaiModel(value) }
              }
            }

            // No separator/header here (unlike a section boundary) — this
            // is the OpenAI image model's own API key, same key slot the
            // model dropdown right above it configures, not a distinct
            // section. Stage 1/Stage 2's ApiKeySections directly follow
            // their model fields the same way, with nothing between them.
            ApiKeySection { slot: "image"; visible: root.configState.imageBackend === "openai" }

            PanelSeparator { foreground: root.bar.foreground }
            PanelSectionHeader { text: "THEME"; foreground: root.bar.foreground }

            // ---- Theme generator: which tool turns the rendered image
            // into colors.toml/icons.theme/etc. "Built-in" is
            // palette_extract.py (default, no external dependency);
            // "Aether" shells out to Omarchy's own richer generator and
            // silently falls back to built-in if it's missing or fails —
            // see astro-arc-generate's theming block. --------------------
            Item {
              width: parent.width
              height: Math.max(themeGeneratorLabel.implicitHeight, themeGeneratorDropdown.implicitHeight)

              Text {
                id: themeGeneratorLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Generator"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: themeGeneratorDropdown
                anchors.left: themeGeneratorLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                showLabel: false
                value: root.configState.themeGenerator
                options: Model.THEME_GENERATOR_CHOICES.map(function(c) {
                  return { value: c.key, label: c.label }
                })
                foreground: root.bar.foreground
                onChanged: function(value) { root.commitThemeGenerator(value) }
              }
            }

            // ---- History retention: days of "Themes Generated" entries
            // to keep (astro-arc-prune-history runs automatically every
            // generation with this value). Estimate is real-data-based —
            // the average size of entries that actually have a recorded
            // sizeBytes, times how many the window is projected to hold at
            // the current frequency — never a guessed number before
            // there's any size history to average (see
            // Model.estimateHistorySpace). --------------------------------
            Item {
              width: parent.width
              height: Math.max(historyRetentionLabel.implicitHeight, historyRetentionField.implicitHeight)

              Text {
                id: historyRetentionLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "History"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              TextField {
                id: historyRetentionField
                anchors.left: historyRetentionLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: historyRetentionDaysLabel.left
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "30"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onEditingFinished: root.commitHistoryRetentionDays(text)
                Component.onCompleted: text = String(root.configState.historyRetentionDays)
                Connections {
                  target: root
                  function onConfigStateChanged() {
                    if (!historyRetentionField.activeFocus) historyRetentionField.text = String(root.configState.historyRetentionDays)
                  }
                }
              }

              Text {
                id: historyRetentionDaysLabel
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "days"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }

            Text {
              visible: root.historyRetentionError !== ""
              width: parent.width
              text: root.historyRetentionError
              color: root.bar.urgent || "#f38ba8"
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              readonly property var estimate: Model.estimateHistorySpace(root.reviewsIndex, root.configState.historyRetentionDays, root.configState.frequency)
              text: estimate.estimatedBytes !== null
                ? "Est. space for " + root.configState.historyRetentionDays + " days: ~" + Model.formatBytes(estimate.estimatedBytes)
                  + " (based on " + estimate.knownCount + " saved generation" + (estimate.knownCount === 1 ? "" : "s") + ")"
                : "Est. space: not enough history yet to estimate"
              color: Qt.darker(root.bar.foreground, 1.5)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            PanelSeparator { foreground: root.bar.foreground }
            PanelSectionHeader { text: "API USAGE"; foreground: root.bar.foreground }

            // ---- Running total, computed locally from costs.json — real
            // token-based cost for every generation's 3 API calls (see
            // cost_estimate.py). Always available, no API call needed. -----
            Item {
              width: parent.width
              height: spentText.implicitHeight

              Text {
                id: spentLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Spent"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Text {
                id: spentText
                anchors.left: spentLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: "$" + root.costsSummary.total.toFixed(4)
                  + " across " + root.costsSummary.generationCount + " generation" + (root.costsSummary.generationCount === 1 ? "" : "s")
                  + (root.costsSummary.unknownCount > 0 ? " (+" + root.costsSummary.unknownCount + " w/ unrecognized-model cost)" : "")
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }

            // ---- Available funds: best-effort, expected to fail with a
            // regular project key — see checkFunds()'s comment. -------------
            Item {
              width: parent.width
              height: Math.max(fundsLabel.implicitHeight, checkFundsBtn.implicitHeight)

              Text {
                id: fundsLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Funds"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Button {
                id: checkFundsBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.fundsChecking ? "Checking…" : "Check"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                enabled: !root.fundsChecking
                onClicked: root.checkFunds()
              }
            }

            Text {
              visible: root.fundsCheck !== null
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.fundsCheck ? root.fundsCheck.message : ""
              color: root.fundsCheck && root.fundsCheck.available ? "#a6e3a1" : Qt.darker(root.bar.foreground, 1.5)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          // ================== MAIN VIEW ==================
          // ---- Birth + location: the only inputs a reading needs,
          // one section instead of two ------------------------------------
          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: !root.showSettings

            PanelSectionHeader { text: "BIRTH & LOCATION"; foreground: root.bar.foreground }

            // Born: label left, date + time fields right. Blank time =
            // unknown (no separate toggle); both auto-save on blur/Enter.
            Item {
              width: parent.width
              height: Math.max(bornLabel.implicitHeight, birthDateField.implicitHeight)

              // Pure anchors (no Row) so birthTimeField's width falls
              // straight out of anchors.left/right instead of a
              // cross-reference to a sibling Row's own anchor-derived
              // width, which is harder to reason about for no benefit here.
              Text {
                id: bornLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Born"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              // text is intentionally not bound to root.draftBirthDate: a
              // persistent binding would be severed the moment the user
              // types (Qt Quick sets `text` imperatively as you edit,
              // which drops any declarative binding on it for good).
              // syncDraftsFromConfig pushes text in on load/reopen
              // instead; onTextChanged is the only path the other way.
              //
              // onTextChanged also re-formats as-you-type: only digits are
              // meaningful input, and Model.formatDateDigits re-inserts the
              // "-" separators, so typing "19900615" displays as
              // "1990-06-15" without the user typing the dashes. Reassigning
              // `text` re-enters onTextChanged; the `formatted !== text`
              // guard makes that second pass a no-op instead of a loop.
              TextField {
                id: birthDateField
                anchors.left: bornLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(118)
                maximumLength: 10
                placeholderText: "YYYY-MM-DD"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onTextChanged: {
                  var formatted = Model.formatDateDigits(text)
                  if (formatted !== text) { text = formatted; return }
                  root.draftBirthDate = text
                }
                onEditingFinished: root.commitBirth()
              }

              TextField {
                id: birthTimeField
                anchors.left: birthDateField.right
                anchors.leftMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(64)
                maximumLength: 5
                placeholderText: "HH:MM"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                onTextChanged: {
                  var formatted = Model.formatTimeDigits(text)
                  if (formatted !== text) { text = formatted; return }
                  root.draftBirthTime = text
                }
                onEditingFinished: root.commitBirth()
              }
            }

            Text {
              visible: root.birthError !== ""
              width: parent.width
              text: root.birthError
              color: root.bar.urgent || "#f38ba8"
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            // Where: label left, current location + Change on the right;
            // swaps to a search field in place while editing.
            Item {
              width: parent.width
              visible: !root.editingLocation
              height: Math.max(whereLabel.implicitHeight, changeBtn.implicitHeight)

              Text {
                id: whereLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Where"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Button {
                id: changeBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Change"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                onClicked: root.startEditingLocation()
              }

              Text {
                anchors.left: whereLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: changeBtn.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                text: root.configState.locationName || "Not set"
                elide: Text.ElideRight
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.editingLocation

              Row {
                width: parent.width
                spacing: Style.space(8)

                TextField {
                  id: locationField
                  width: parent.width - cancelBtn.width - Style.space(8)
                  enabled: !root.savingLocation
                  placeholderText: "Search city, state, country"
                  foreground: root.bar.foreground
                  font.family: root.bar.fontFamily
                  onTextChanged: if (root.editingLocation && !root.savingLocation) geocodeDebounce.restart()
                  Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Escape) {
                      root.cancelEditingLocation(); event.accepted = true
                    } else if (event.key === Qt.Key_Down) {
                      if (root.suggestionIndex < root.locationSuggestions.length - 1) root.suggestionIndex++
                      event.accepted = true
                    } else if (event.key === Qt.Key_Up) {
                      if (root.suggestionIndex > 0) root.suggestionIndex--
                      event.accepted = true
                    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                      root.commitLocation(); event.accepted = true
                    }
                  }
                }

                Button {
                  id: cancelBtn
                  text: root.savingLocation ? "…" : "✕"
                  bordered: true
                  foreground: root.bar.foreground
                  fontSize: Style.font.bodySmall
                  enabled: !root.savingLocation
                  onClicked: root.cancelEditingLocation()
                }
              }

              Repeater {
                model: root.locationSuggestions

                Rectangle {
                  required property var modelData
                  required property int index
                  width: parent.width
                  height: suggestionRow.implicitHeight + Style.space(8)
                  radius: Style.cornerRadius
                  color: index === root.suggestionIndex ? Style.hoverFillFor(root.bar.foreground, Color.accent) : "transparent"

                  Row {
                    id: suggestionRow
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(8)

                    Text {
                      text: modelData.name
                      color: root.bar.foreground
                      font.family: root.bar.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      visible: text !== ""
                      text: modelData.description
                      color: Qt.darker(root.bar.foreground, 1.5)
                      font.family: root.bar.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onPositionChanged: root.suggestionIndex = index
                    onClicked: root.pickSuggestion(modelData)
                  }
                }
              }

              Text {
                visible: root.configState.latitude !== null
                text: root.configState.latitude !== null
                  ? ("Coordinates: " + root.configState.latitude.toFixed(4) + ", " + root.configState.longitude.toFixed(4))
                  : ""
                color: Qt.darker(root.bar.foreground, 1.5)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }

          // ================== MAIN VIEW, continued ==================
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: !root.showSettings

          // ---- Schedule: label left, compact dropdown right — the
          // Dropdown's own default width (240px) is most of the panel,
          // so it's overridden down to fit a label beside it -------------
          Item {
            width: parent.width
            height: Math.max(scheduleLabel.implicitHeight, frequencyDropdown.implicitHeight)

            Text {
              id: scheduleLabel
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: root.labelColW
              text: "Schedule"
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Dropdown {
              id: frequencyDropdown
              anchors.left: scheduleLabel.right
              anchors.leftMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(100)
              showLabel: false
              value: root.configState.frequency
              options: [
                { value: "daily", label: "Daily" },
                { value: "weekly", label: "Weekly" },
                { value: "monthly", label: "Monthly" }
              ]
              foreground: root.bar.foreground
              onChanged: function(value) { root.commitFrequency(value) }
            }
          }

          // ---- Style: label left, dropdown right — no longer locked;
          // options come from pipeline/styles.toml (mirrored in Model.js
          // as ART_STYLE_CHOICES). -----------------------------------------
          Item {
            width: parent.width
            height: Math.max(styleLabel.implicitHeight, styleDropdown.implicitHeight)

            Text {
              id: styleLabel
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: root.labelColW
              text: "Style"
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Dropdown {
              id: styleDropdown
              anchors.left: styleLabel.right
              anchors.leftMargin: Style.space(8)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              showLabel: false
              value: root.configState.artStyle
              options: Model.ART_STYLE_CHOICES.map(function(s) {
                return { value: s.key, label: s.label }
              })
              foreground: root.bar.foreground
              onChanged: function(value) { root.commitArtStyle(value) }
            }
          }

          PanelSeparator { foreground: root.bar.foreground }

          // ---- Regenerate, beside the last-generated status -------------
          Item {
            width: parent.width
            height: Math.max(statusText.implicitHeight, regenerateBtn.implicitHeight)

            Text {
              id: statusText
              anchors.left: parent.left
              anchors.right: regenerateBtn.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              elide: Text.ElideRight
              text: root.lastRun
                ? Model.frequencyLabel(root.lastRun.frequency) + " · " + Model.formatGeneratedAt(root.lastRun.generatedAt, function(d) {
                    return Qt.formatDateTime(d, "MMM d, h:mm AP")
                  })
                : "Not generated yet"
              color: Qt.darker(root.bar.foreground, 1.5)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            Button {
              id: regenerateBtn
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.generating ? "Generating…" : "Regenerate"
              iconText: root.generating ? "↻" : ""
              bordered: true
              foreground: root.bar.foreground
              fontSize: Style.font.caption
              enabled: !root.generating
              iconSpinning: root.generating
              onClicked: root.regenerateNow()
            }
          }

          Text {
            visible: root.generateError !== ""
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Last run error: " + root.generateError
            color: root.bar.urgent || "#f38ba8"
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          // ---- Distillation: the one-line psychological core of the last
          // reading, with its narrative-position badge above it — same two
          // fields the review-history HTML shows as a badge over an
          // italicized quote (build_review.py's .badge/.distillation). -----
          Column {
            width: parent.width
            spacing: Style.space(2)
            visible: !!root.lastRun && root.lastRun.distillation !== ""

            Text {
              visible: root.lastRun ? root.lastRun.narrativePosition !== "" : false
              text: root.lastRun ? root.lastRun.narrativePosition : ""
              color: Color.accent
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.capitalization: Font.AllUppercase
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.lastRun ? "“" + root.lastRun.distillation + "”" : ""
              font.family: root.bar.fontFamily
              font.italic: true
              font.pixelSize: Style.font.bodySmall
              color: root.bar.foreground
            }
          }

          // ---- Themes Generated: browse every real generation (was
          // "Reviews" — renamed since every entry is a real iteration now,
          // not just the Phase 3 style-comparison batches this started as).
          // Each carries its own on-disk size; Save Selected Theme exports
          // one as a permanent Omarchy theme (astro-arc-save-theme); Clear
          // History deletes all of them right now regardless of age
          // (astro-arc-prune-history --all) — the automatic age-based
          // pruning runs on its own every generation, see the THEME
          // settings section's History retention field. -------------------
          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.reviewsIndex.length > 0

            PanelSeparator { foreground: root.bar.foreground }
            PanelSectionHeader { text: "THEMES GENERATED"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(reviewsLabel.implicitHeight, reviewsDropdown.implicitHeight)

              Text {
                id: reviewsLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "History"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: reviewsDropdown
                anchors.left: reviewsLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: openReviewBtn.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                showLabel: false
                value: root.selectedReviewId
                options: root.reviewsIndex.map(function(r) {
                  // cardCount was meaningful when a review could bundle
                  // several style variants for one reading (the Phase 3
                  // exploration batches); every review since is one real
                  // generation, so it's redundant noise now. Size is null
                  // (omitted) on an entry built before that field existed.
                  var sizeSuffix = r.sizeBytes !== null ? " · " + Model.formatBytes(r.sizeBytes) : ""
                  return { value: r.id, label: r.label + sizeSuffix }
                })
                foreground: root.bar.foreground
                onChanged: function(value) { root.selectedReviewId = value }
              }

              Button {
                id: openReviewBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Open"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                onClicked: root.openSelectedReview()
              }
            }

            Item {
              width: parent.width
              height: Math.max(saveThemeBtn.implicitHeight, clearHistoryBtn.implicitHeight)
              visible: !root.themeNamePromptOpen

              Button {
                id: saveThemeBtn
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "Save Selected Theme"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                enabled: root.selectedReviewId !== ""
                onClicked: root.openSaveThemePrompt()
              }

              Button {
                id: clearHistoryBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.clearHistoryArmed ? "Confirm Clear?" : "Clear History"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.clearHistoryArmed ? (root.bar.urgent || "#f38ba8") : root.bar.foreground
                onClicked: root.clearHistoryClicked()
              }
            }

            // ---- Name prompt: shown in place of the row above once Save
            // Selected Theme is clicked. Pre-filled with
            // Model.suggestThemeName's guess from that generation's own
            // concept tags — editable before it's actually saved. ----------
            Column {
              width: parent.width
              spacing: Style.space(6)
              visible: root.themeNamePromptOpen

              Text {
                text: "Theme name"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              TextField {
                id: themeNameField
                width: parent.width
                placeholderText: "e.g. Geological Rootedness"
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                text: root.themeNameDraft
                onTextChanged: root.themeNameDraft = text
                Keys.onReturnPressed: root.confirmSaveTheme()
                Keys.onEnterPressed: root.confirmSaveTheme()
                Component.onCompleted: forceActiveFocus()
              }

              Item {
                width: parent.width
                height: Math.max(confirmSaveThemeBtn.implicitHeight, cancelSaveThemeBtn.implicitHeight)

                Button {
                  id: cancelSaveThemeBtn
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Cancel"
                  bordered: true
                  fontSize: Style.font.caption
                  foreground: root.bar.foreground
                  onClicked: root.cancelSaveThemePrompt()
                }

                Button {
                  id: confirmSaveThemeBtn
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Save"
                  bordered: true
                  fontSize: Style.font.caption
                  foreground: root.bar.foreground
                  onClicked: root.confirmSaveTheme()
                }
              }
            }

            Text {
              visible: root.themeActionStatus !== ""
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.themeActionStatus
              color: root.themeActionStatusIsError ? (root.bar.urgent || "#f38ba8") : Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
          }
        }
      }
    }
  }

  IpcHandler {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
  }
}
