import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
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
  // Where this plugin actually lives, asked of QML rather than assumed. The
  // backend ships beside these .qml files (backend/bin, backend/pipeline), so a
  // clone into ~/.config/omarchy/plugins/astro-arc — the way every other
  // Omarchy plugin is installed — needs no symlinks and no install path baked
  // in anywhere. Qt.resolvedUrl(".") is the directory holding Panel.qml.
  readonly property string pluginRoot: {
    var u = String(Qt.resolvedUrl("."))
    if (u.indexOf("file://") === 0) u = u.substring(7)
    return u.replace(/\/+$/, "")
  }
  readonly property string binDir: pluginRoot + "/backend/bin"

  readonly property string configBin: binDir + "/astro-arc-config"
  readonly property string generateBin: binDir + "/astro-arc-generate"

  // ---- Lifecycle -----------------------------------------------------
  function open() {
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    syncDraftsFromConfig()
    configFile.reload()
    lastRunFile.reload()
    reviewsIndexFile.reload()
    costsFile.reload()
    loadModelCatalogIfStale()
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

  // Omarchy 4.0.4 hands plugins a bar facade whose property is read-only;
  // older shells expose the bar itself, so fall back to assigning.
  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && typeof root.bar.setCenterHoverRevealSuppressed === "function")
      root.bar.setCenterHoverRevealSuppressed(value)
    else if (root.bar && "centerHoverRevealSuppressed" in root.bar)
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
    onLoaded: {
      root.configState = Model.parseConfig(text())
      root.checkAutoGenerate()
    }
    onLoadFailed: root.configState = Model.parseConfig("")
  }

  property FileView lastRunFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/last-run.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.lastRun = Model.parseLastRun(text())
      root.lastRunLoaded = true
      root.checkAutoGenerate()
    }
    onLoadFailed: {
      root.lastRun = null
      root.lastRunLoaded = true
      root.checkAutoGenerate()
    }
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

  // ifDue selects astro-arc-generate's `--if-due` flag: the manual
  // Regenerate button always forces a run (ifDue: false), while the
  // widget's own auto-check (below) asks the script to first re-check
  // last-run.json itself and no-op if the period's already covered —
  // closing the race where the systemd timer (see below) finished a
  // generation for this period in the moment between this widget's own
  // JS-side check and this process actually starting.
  function startGenerate(ifDue) {
    if (generating) return
    generating = true
    generateError = ""
    generateProc.command = ifDue ? [root.generateBin, "--if-due"] : [root.generateBin]
    generateProc.running = true
  }

  function regenerateNow() {
    startGenerate(false)
  }

  // ---- Automatic scheduling ---------------------------------------------
  // Two independent, redundant triggers keep this from depending on any one
  // thing staying alive: `systemd/astro-arc-generate.timer` (installed by
  // install.sh) fires `astro-arc-generate --if-due` on its own schedule
  // regardless of whether the bar/shell is even running, and — as long as
  // the widget *is* loaded — this Panel also polls on its own Timer below,
  // so a change made in the panel (e.g. switching frequency) is picked up
  // without waiting on the system timer's own interval. Both funnel through
  // the same `--if-due` flag and the script's own flock, so whichever one
  // notices the new period first wins and the other one's next tick is
  // just a no-op.
  //
  // "New period" is a plain string comparison against Model.currentPeriodKey
  // (which mirrors astro-arc-generate's own `date +%Y-%m-%d`/`+%G-W%V`/
  // `+%Y-%m`) — never elapsed-time math — so "daily" means "the calendar
  // date changed", not "24 hours since the last run". 11pm and 1am the next
  // morning are different dates the moment the clock crosses midnight, so
  // the very next timer tick (at most autoGenerateCheckInterval later)
  // starts that day's generation, however few hours actually separated them.
  property bool lastRunLoaded: false
  property string autoAttemptedPeriodKey: ""
  readonly property int autoGenerateCheckInterval: 5 * 60 * 1000

  function checkAutoGenerate() {
    if (!root.lastRunLoaded || root.generating) return
    // Nothing to generate from yet (fresh install, birth data never set) —
    // don't nag the API with a doomed request every interval.
    if (root.configState.birthDate === "") return

    // Nothing is scheduled on "manual" — astro-arc-generate --if-due would
    // exit immediately anyway, but there is no reason to spawn it to be told
    // that.
    if (root.configState.frequency === "manual") return

    var currentKey = Model.currentPeriodKey(root.configState.frequency)
    // Model.runCoversPeriod, not a comparison against lastRun.periodKey: that
    // key was filed under whatever frequency was set when the run happened, so
    // comparing it across a frequency CHANGE made switching Daily -> Monthly
    // generate on the spot. See its comment in Model.js.
    if (Model.runCoversPeriod(root.lastRun, root.configState.frequency)) return
    // Already tried this exact period this session (regardless of whether
    // it succeeded) — avoid hammering a misconfigured setup (e.g. no API
    // key) every autoGenerateCheckInterval; the next real period, or a
    // manual "Regenerate" click, will try again.
    if (root.autoAttemptedPeriodKey === currentKey) return

    root.autoAttemptedPeriodKey = currentKey
    root.startGenerate(true)
  }

  Timer {
    interval: root.autoGenerateCheckInterval
    running: true
    repeat: true
    onTriggered: root.checkAutoGenerate()
  }

  Process {
    id: generateProc
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
  // Deliberately says nothing about the schedule — the Frequency dropdown is
  // the one place that reports it, and having the status line repeat it only
  // made a cadence look like something this line was reporting *on*.
  readonly property string heroStatus: root.generating
    ? "Generating…"
    : (root.lastRun ? "Up to date" : "Not generated yet")

  // ---- Settings sub-view (image backend/model, Stage 1/2 models, API
  // keys) -------------------------------------------------------------------
  // A second state of this same panel (toggled by the hero's gear icon)
  // rather than a separate Panel.qml — everything about anchoring/focus/
  // popout-coordination is already solved for this panel, and duplicating
  // that just to host a handful of extra rows isn't worth it.
  property bool showSettings: false
  readonly property string apiKeyBin: binDir + "/astro-arc-apikey"
  readonly property string openaiImageGenBin: binDir + "/openai_image_gen.py"
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
    // The simple view shows ONE key and writes it to every slot; the advanced
    // window is where slots get split. `store-all` fans stdin into three stores
    // with tee, so the key still never lands in a shell variable — the same
    // guarantee the per-slot path gives. See astro-arc-apikey's header.
    apiKeyStoreProc.command = root.storeAllSlots
      ? [root.apiKeyBin, "store-all"]
      : ["secret-tool", "store", "--label=Astro-Arc OpenAI API Key (" + slot + ")", "service", "astro-arc", "account", root.accountForSlot(slot)]
    apiKeyStoreProc.running = true
  }

  // Set for the duration of one save. Not a per-slot property because the store
  // Process is shared across slots and a save is a single deliberate click.
  property bool storeAllSlots: false

  function saveApiKeyEverywhere() {
    root.storeAllSlots = true
    // Any slot's draft would do — the simple view only ever fills this one.
    root.saveApiKey("image")
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
      var wasAll = root.storeAllSlots
      root.storeAllSlots = false
      root.setApiKeyField(slot, "saving", false)
      root.setApiKeyField(slot, "replacing", false)
      if (wasAll) {
        // Every slot changed, so every slot's masked reference is now stale.
        var slots = ["stage1", "stage2", "image"]
        for (var i = 0; i < slots.length; i++) {
          root.setApiKeyField(slots[i], "saving", false)
          root.setApiKeyField(slots[i], "replacing", false)
          root.refreshApiKeyStatus(slots[i])
        }
      } else {
        root.refreshApiKeyStatus(slot)
      }
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
  // ---- Provider: the one global setting. -----------------------------
  function commitProvider(value) {
    providerWriteProc.command = [root.configBin, "--set-provider", value]
    providerWriteProc.running = true
  }

  Process {
    id: providerWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- The popout. The panel itself stays rudimentary — provider, one key,
  // one quality preset — and everything that makes this configurable rather
  // than merely usable lives here: every model the key can reach, the two chat
  // stages split apart, a separate key per slot, and the cost ceiling.
  property bool advancedOpen: false

  // ---- Quality preset: one control that sets all four model settings, in
  // the spirit of a game's graphics presets. The individual pickers below stay
  // fully live; touching any of them makes the settings match no tier, and
  // resolvePresetKey reports "Custom" — derived, never stored, so it cannot
  // drift out of sync with the thing it describes.
  property bool showAllModels: false

  function commitPreset(key) {
    if (key === "custom") return
    var preset = (root.modelCatalog.presets || []).filter(function(p) { return p.key === key })[0]
    if (!preset) return
    // ONE write for all four. Writing them separately would put the config
    // through intermediate states that match no preset, so the picker would
    // flicker through "Custom" on its way to the tier just chosen.
    presetWriteProc.command = [root.configBin, "--set-models",
      preset.chat, preset.chat, preset.image, preset.quality]
    presetWriteProc.running = true
  }

  Process {
    id: presetWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- Per-image cost ceiling (dollars; 0 = none). -------------------
  property string maxCostPerImageError: ""

  function commitMaxCostPerImage(value) {
    var trimmed = String(value || "").trim()
    if (trimmed === "") trimmed = "0"
    if (!/^[0-9]+(\.[0-9]+)?$/.test(trimmed)) {
      root.maxCostPerImageError = "Enter a dollar amount, e.g. 0.03 (0 = no ceiling)"
      return
    }
    root.maxCostPerImageError = ""
    maxCostPerImageWriteProc.command = [root.configBin, "--set-max-cost-per-image", trimmed]
    maxCostPerImageWriteProc.running = true
  }

  Process {
    id: maxCostPerImageWriteProc
    onExited: function(exitCode) { if (exitCode === 0) root.configFile.reload() }
  }

  // ---- Updates: the plugin is its own git checkout, so "update" is a fetch
  // and a fast-forward. astro-arc-update does the work and the safety checks
  // and answers in JSON; this only drives it and shows what it said. Exists
  // because installing no longer requires the terminal, so updating should not
  // either. -----------------------------------------------------------------
  readonly property string updateBin: binDir + "/astro-arc-update"
  property string updateStatus: ""
  property string updateMessage: ""
  property var updateFiles: []
  property bool updateBusy: false

  function checkForUpdates(apply) {
    if (root.updateBusy) return
    root.updateBusy = true
    root.updateMessage = ""
    updateProc.command = apply ? [root.updateBin, "--apply"] : [root.updateBin]
    updateProc.running = true
  }

  Process {
    id: updateProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(String(text || "")) } catch (e) { parsed = null }
        if (parsed) {
          root.updateStatus = String(parsed.status || "")
          root.updateMessage = String(parsed.message || "")
          root.updateFiles = parsed.files || []
        } else {
          // The script answers in JSON on every path, so unparseable output
          // means it did not run at all — a missing file, a broken PATH.
          root.updateStatus = "error"
          root.updateMessage = "Could not run the update check."
          root.updateFiles = []
        }
      }
    }
    onExited: root.updateBusy = false
  }

  Process { id: restartShellProc; command: ["omarchy-restart-shell"] }

  // ---- Model catalog: which models this key can actually reach, what they
  // cost, and what they have been measured consuming. Replaces the hardcoded
  // four-row list that used to live in Model.js — that list offered 2 image
  // models while the key could reach 10, so most of what had been paid for was
  // unreachable from here. See backend/bin/model_catalog.py for why
  // availability is discovered but price has to be hand-entered.
  readonly property string modelCatalogBin: binDir + "/model_catalog.py"
  property var modelCatalog: ({ chat: [], image: [], stale: true, everFetched: false, fetchedAt: null })
  property bool modelCatalogLoading: false
  property string modelCatalogError: ""

  // `refresh` hits GET /v1/models (free, no tokens billed, ~1s); without it
  // this only reads the on-disk cache, so opening the panel is never gated on
  // the network.
  function loadModelCatalog(refresh) {
    if (root.modelCatalogLoading) return
    root.modelCatalogLoading = true
    root.modelCatalogError = ""
    var cmd = [root.venvPython, root.modelCatalogBin, "--size", root.configState.backgroundSize || "1024x1024"]
    if (refresh) cmd.push("--refresh")
    modelCatalogProc.command = cmd
    modelCatalogProc.running = true
  }

  // Auto-refresh only when the cache has actually gone stale (24h, enforced
  // backend-side), so the common case is a disk read and the network is only
  // touched about once a day.
  function loadModelCatalogIfStale() {
    loadModelCatalog(root.modelCatalog.everFetched === true && root.modelCatalog.stale === true)
  }

  Process {
    id: modelCatalogProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.modelCatalogLoading = false
        try {
          var parsed = JSON.parse(String(text || ""))
          if (parsed && parsed.ok) {
            root.modelCatalog = parsed
            // A cache that has never been fetched yields empty lists; fetch
            // once so a fresh install isn't stuck with no models to pick.
            if (!parsed.everFetched) root.loadModelCatalog(true)
          } else {
            root.modelCatalogError = (parsed && parsed.error) ? parsed.error : "Couldn't read the model list"
          }
        } catch (e) {
          root.modelCatalogError = "Couldn't read the model list"
        }
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var err = String(text || "").trim()
        if (err !== "" && root.modelCatalogError === "") root.modelCatalogError = err.split("\n").pop()
      }
    }
  }

  readonly property string detectResolutionBin: binDir + "/astro-arc-detect-resolution"

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

  // ---- Background only (default on): each generation changes the wallpaper
  // and nothing else. Off, it also applies the derived palette as the
  // astro-arc theme — which repaints every app a theme touches, and replaces
  // whatever theme the user was running. -----------------------------------
  function commitBackgroundOnly(value) {
    backgroundOnlyWriteProc.command = [root.configBin, "--set-background-only", value ? "true" : "false"]
    backgroundOnlyWriteProc.running = true
  }

  Process {
    id: backgroundOnlyWriteProc
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
  // cost_estimate.py). Used to carry a "Check funds" button too
  // (openai_image_gen.py --check-funds) — removed, since it was expected
  // to fail with a regular project key (needs an Admin API key's
  // api.usage.read scope) and, in practice, always did. The cost summary
  // here is the real, always-available substitute; check_available_funds
  // stays in openai_image_gen.py itself for now, just not wired to the UI
  // — see Conventions.
  property var costsSummary: ({ total: 0, unknownCount: 0, generationCount: 0 })

  property FileView costsFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/pipeline/costs.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.costsSummary = Model.sumCosts(text())
    onLoadFailed: root.costsSummary = { total: 0, unknownCount: 0, generationCount: 0 }
  }

  // ---- Themes Generated: every real generation, browsable and — since
  // astro-arc-generate now snapshots the live theme dir into each entry —
  // exportable as a standalone theme (astro-arc-save-theme) before the
  // configured retention window prunes it (astro-arc-prune-history). -----
  property var reviewsIndex: []
  property string selectedReviewId: ""
  // The newest id as of the last load, so onLoaded can tell a genuinely new
  // generation apart from any other rewrite of index.json. Empty until the
  // first load, which makes that first load count as "new" and seeds the
  // selection with the latest entry.
  property string newestReviewId: ""

  property FileView reviewsIndexFile: FileView {
    path: root.home + "/.local/state/omarchy/astro-arc/reviews/index.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.reviewsIndex = Model.parseReviewsIndex(text())
      var newestId = root.reviewsIndex.length > 0 ? root.reviewsIndex[0].id : ""

      // Follow the newest generation. The previous rule only re-pointed the
      // selection when the selected entry had been PRUNED, so a brand-new
      // generation left it sitting on whatever was picked last — the picker
      // went stale exactly when there was something new to look at, which is
      // the one moment it matters.
      //
      // Tracking the newest id (rather than just assigning it every load)
      // keeps manual browsing intact: index.json is rewritten and reloaded for
      // reasons other than a new generation — a save, a prune, a clear — and
      // those must not yank the user off an older entry they deliberately
      // opened.
      var isNewGeneration = newestId !== "" && newestId !== root.newestReviewId
      var stillExists = root.reviewsIndex.some(function(r) { return r.id === root.selectedReviewId })

      if (isNewGeneration || !stillExists) root.selectedReviewId = newestId
      root.newestReviewId = newestId
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
  readonly property string saveThemeBin: binDir + "/astro-arc-save-theme"
  readonly property string pruneHistoryBin: binDir + "/astro-arc-prune-history"

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

  // ---- Export: package the selected generation as a shareable Omarchy theme
  // REPOSITORY, which is a different job from Save. Save installs a theme on
  // THIS machine; Omarchy distributes themes by git clone
  // (`omarchy theme install <url>`), so handing one to another user means
  // producing a git repo. astro-arc-export-theme builds that, preview and
  // README included, and prints where it put it.
  readonly property string exportThemeBin: binDir + "/astro-arc-export-theme"
  property string _exportStdout: ""
  property string _exportStderr: ""

  function exportSelectedTheme() {
    if (root.selectedReviewId === "" || exportThemeProc.running) return
    var review = root.reviewForId(root.selectedReviewId)
    // Named from the same concept-tag suggestion Save pre-fills its prompt
    // with, but without prompting: an export lands in a folder you can rename,
    // so asking first would add a step to the one-click path for nothing.
    var name = review ? Model.suggestThemeName(review.conceptTags) : ""
    root.themeActionStatus = ""
    root._exportStdout = ""
    root._exportStderr = ""
    exportThemeProc.command = name === ""
      ? [root.exportThemeBin, root.selectedReviewId]
      : [root.exportThemeBin, root.selectedReviewId, name]
    exportThemeProc.running = true
  }

  Process {
    id: exportThemeProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root._exportStdout = String(text || "").trim() }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root._exportStderr = String(text || "").trim() }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.themeActionStatus = "Exported to " + root._exportStdout
          + " — push it to a git host and anyone can install it with \u201comarchy theme install <url>\u201d."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = root._exportStderr || "Export failed."
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
    // When true this row represents the ONE key the simple view shows, and
    // saving writes it to every slot. `slot` still names which slot's status is
    // displayed, since in that mode all three hold the same key anyway.
    property bool storeAll: false
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
          Keys.onReturnPressed: section.storeAll ? root.saveApiKeyEverywhere() : root.saveApiKey(section.slot)
        }

        Button {
          id: saveKeyBtn
          text: section.keyState.saving ? "Saving…" : "Save"
          bordered: true
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          enabled: !section.keyState.saving && root.draftFor(section.slot).length > 0
          onClicked: section.storeAll ? root.saveApiKeyEverywhere() : root.saveApiKey(section.slot)
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

            // First, because it decides what a generation does to the desktop
            // at all — more consequential than any model choice below.
            Toggle {
              width: parent.width
              label: "Background only"
              description: "Do not update theme colors"
              checked: root.configState.backgroundOnly
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              onClicked: root.commitBackgroundOnly(!root.configState.backgroundOnly)
            }

            // ---- Provider: the global setting everything else is scoped
            // to. The key you paste, which models are offered, what they cost
            // — all of it follows from this. Only OpenAI today; the control
            // exists so a second provider is a data change, not a redesign.
            // (This replaced a "Backend" picker that lived under IMAGE
            // GENERATION and offered local Stable Diffusion. Local is no longer
            // offered here, but a config that still says "local" is honored —
            // see astro-arc-generate.)
            Item {
              width: parent.width
              height: Math.max(providerLabel.implicitHeight, providerDropdown.implicitHeight, advancedBtn.implicitHeight)

              Text {
                id: providerLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Provider"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: providerDropdown
                anchors.left: providerLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: advancedBtn.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                showLabel: false
                value: root.configState.provider
                options: Model.PROVIDER_CHOICES.map(function(c) {
                  return { value: c.key, label: c.label }
                })
                foreground: root.bar.foreground
                onChanged: function(value) { root.commitProvider(value) }
              }

              Button {
                id: advancedBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Advanced"
                foreground: root.bar.foreground
                onClicked: root.advancedOpen = true
              }
            }

            // One key, written to all three slots. The per-stage split lives in
            // the popout; showing three key rows here made the common case —
            // one account, one key — look like a three-step setup.
            //
            // Titled, because a stored key renders as a masked reference
            // ("sk-...CxYA") and an unlabelled row of characters gives no clue
            // what it is or why it is there.
            Text {
              width: parent.width
              text: "API key"
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            ApiKeySection { slot: "image"; storeAll: true }

            PanelSeparator { foreground: root.bar.foreground }

            // ---- Quality: one control for every model choice. ---------
            PanelSectionHeader { text: "QUALITY"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(qualityPresetLabel.implicitHeight, presetDropdown.implicitHeight)

              Text {
                id: qualityPresetLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Preset"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Dropdown {
                id: presetDropdown
                anchors.left: qualityPresetLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                showLabel: false
                value: Model.resolvePresetKey(root.configState, root.modelCatalog.presets)
                options: {
                  var opts = (root.modelCatalog.presets || []).map(function(p) {
                    return { value: p.key, label: Model.presetLabel(p) }
                  })
                  // "Custom" is a readout, not a choice — you reach it by
                  // changing something in the popout. Listed so the dropdown can
                  // display the state it is actually in.
                  opts.push({ value: "custom", label: "Custom (set in Popout)" })
                  return opts
                }
                foreground: root.bar.foreground
                onChanged: function(value) { root.commitPreset(value) }
              }
            }

            // What the preset actually resolved to. Read-only on purpose: this
            // is the answer to "what is it using?", and changing it is what the
            // popout is for.
            Column {
              width: parent.width
              spacing: Style.space(2)

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Chat models: " + (root.configState.stage1Model === root.configState.stage2Model
                  ? root.configState.stage1Model
                  : root.configState.stage1Model + " (interpretation) · " + root.configState.stage2Model + " (image prompt)")
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Image model: " + root.configState.openaiModel + " · " + root.configState.openaiQuality
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                // Projected from a FRESH period, not from the cost log. The log
                // averages real runs, and same-day regenerations in it are
                // mostly cache hits, so it reported a daily cost far below the
                // truth — the run that prompted this charged $0.07 because only
                // Stage 2 and the render actually ran, while a new day of the
                // same settings costs $0.18.
                text: {
                  var e = Model.projectedMonthlyCost(root.modelCatalog.nextRun, root.configState.frequency)
                  if (!e) return "Est. monthly: cost unknown for the current models"
                  var per = Model.formatUsd(e.perRun) + " per full run ("
                    + (e.source === "actual" ? "actual" : "est") + ")"
                  // With nothing scheduled there is no monthly figure to give —
                  // only what a run costs when you ask for one.
                  if (!e.scheduled) return "No recurring cost — generates only when you click. " + per
                  return "Est. " + Model.formatUsd(e.perMonth) + "/month at " + root.configState.frequency
                    + " · " + per
                }
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            PanelSeparator { foreground: root.bar.foreground }

            // ---- Background size: which resolution the render is fitted to.
            // Kept here rather than in the popout because it is about your
            // monitor, not about models.
            PanelSectionHeader { text: "BACKGROUND"; foreground: root.bar.foreground }
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
              text: {
                var e = estimate
                var basis = " (based on " + e.knownCount + " saved generation" + (e.knownCount === 1 ? "" : "s") + ")"
                if (e.estimatedBytes !== null)
                  return "Est. space for " + root.configState.historyRetentionDays + " days: ~"
                    + Model.formatBytes(e.estimatedBytes) + basis
                // No cadence: how much this ends up using depends on how often
                // you click, so give the per-generation figure — which IS known
                // — rather than the "not enough history" line, which would be
                // untrue whenever there is history but nothing scheduled.
                if (e.noCadence)
                  return "Est. space: ~" + Model.formatBytes(e.avgBytes) + " per generation"
                    + " — no total, since nothing is scheduled" + basis
                return "Est. space: not enough history yet to estimate"
              }
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

            PanelSeparator { foreground: root.bar.foreground }
            PanelSectionHeader { text: "UPDATES"; foreground: root.bar.foreground }

            Item {
              width: parent.width
              height: Math.max(updateLabel.implicitHeight, updateBtn.implicitHeight)

              Text {
                id: updateLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: root.labelColW
                text: "Version"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              // One button with three jobs, because they are three steps of one
              // errand: find out, apply, load. Never two buttons where the
              // second is meaningless until the first has run.
              Button {
                id: updateBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                enabled: !root.updateBusy
                text: root.updateBusy
                  ? "Checking…"
                  : root.updateStatus === "behind"
                    ? "Update now"
                    : root.updateStatus === "updated"
                      ? "Restart shell"
                      : "Check for updates"
                onClicked: {
                  if (root.updateStatus === "updated") restartShellProc.running = true
                  else root.checkForUpdates(root.updateStatus === "behind")
                }

                // Same latch the Regenerate and Export buttons use: the label
                // changes width between states, and a control that resizes
                // under the cursor feels broken.
                property real reservedWidth: 0
                onImplicitWidthChanged: if (implicitWidth > reservedWidth) reservedWidth = implicitWidth
                width: Math.max(implicitWidth, reservedWidth)
              }
            }

            Text {
              visible: root.updateMessage !== ""
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.updateMessage
              color: (root.updateStatus === "up-to-date" || root.updateStatus === "updated")
                ? Qt.darker(root.bar.foreground, 1.3)
                : (root.bar.urgent || "#f38ba8")
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            // Naming the files matters more than the refusal does: this is the
            // one message where the user is being told their own tuning is what
            // stopped the update, and it is useless without saying which.
            Text {
              visible: root.updateFiles.length > 0
              width: parent.width
              wrapMode: Text.WordWrap
              text: "Changed: " + root.updateFiles.join(", ")
              color: Qt.darker(root.bar.foreground, 1.5)
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
              options: Model.FREQUENCY_CHOICES.map(function(c) {
                return { value: c.key, label: c.label }
              })
              foreground: root.bar.foreground
              onChanged: function(value) { root.commitFrequency(value) }
            }
          }

          // ---- What a schedule actually promises. Neither trigger outlives
          // the login session: the systemd timer is a --user unit with no
          // lingering, and the panel's own poll obviously needs the shell
          // running. Locking the screen stops neither (a lock is not a
          // logout), and Persistent=true on the timer means a boundary
          // crossed while the machine was off or asleep fires shortly after
          // it comes back rather than being skipped. Worth stating plainly:
          // "every hour" reads as a promise the machine cannot keep while it
          // is switched off. ----------------------------------------------
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.configState.frequency === "manual"
              ? "Nothing generates on its own — Regenerate is the only trigger."
              : "Runs only while the computer is on and you are logged in; a locked "
                + "screen still counts. Anything due while it was off, asleep or "
                + "logged out runs shortly after you are back."
            color: Qt.darker(root.bar.foreground, 1.5)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
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
                ? "Last run: " + Model.formatGeneratedAt(root.lastRun.generatedAt, function(d) {
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
              // Shows what THIS click will charge, which depends on what is
              // already cached for the current period — not the same as a
              // fresh day's cost.
              text: root.generating ? "Generating…" : Model.nextRunLabel(root.modelCatalog.nextRun)
              bordered: true
              foreground: root.bar.foreground
              fontSize: Style.font.caption
              enabled: !root.generating
              onClicked: root.regenerateNow()

              // The button used to resize the moment it was clicked: the label
              // shortens ("Regenerate · $0.069" -> "Generating…") AND a spinner
              // icon appeared, so the width moved twice in one frame. Latching
              // the widest width it has ever wanted fixes the shrink, and since
              // the idle label is the longer of the two and no icon is added
              // any more, that maximum is reached on the first paint and never
              // changes again — so there is no initial jump either.
              property real reservedWidth: 0
              onImplicitWidthChanged: if (implicitWidth > reservedWidth) reservedWidth = implicitWidth
              width: Math.max(implicitWidth, reservedWidth)
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
              height: Math.max(saveThemeBtn.implicitHeight, exportThemeBtn.implicitHeight)
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

              // Save installs it here; Export packages it for someone else.
              Button {
                id: exportThemeBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: exportThemeProc.running ? "Exporting…" : "Export"
                bordered: true
                fontSize: Style.font.caption
                foreground: root.bar.foreground
                enabled: root.selectedReviewId !== "" && !exportThemeProc.running
                onClicked: root.exportSelectedTheme()

                // Same latch as the Regenerate button: the label shortens while
                // running, and a control that resizes under the cursor is what
                // made that button feel wrong.
                property real reservedWidth: 0
                onImplicitWidthChanged: if (implicitWidth > reservedWidth) reservedWidth = implicitWidth
                width: Math.max(implicitWidth, reservedWidth)
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

  // ---- Advanced settings popout -------------------------------------
  // A real overlay window rather than an expanded panel, following the
  // image-picker plugin's pattern (PanelWindow + WlrLayershell overlay): the
  // bar panel is narrow, and this view has to hold every model the key can
  // reach plus three key rows without either being cramped.
  //
  // Deliberately a sibling of the panel, not a separate QML file: ApiKeySection
  // is an inline component of this file and inline components are not visible
  // from another one, so extracting this would mean duplicating the key rows —
  // the one part of this widget where duplication is genuinely dangerous.
  PanelWindow {
    id: advancedWindow

    visible: root.advancedOpen
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "astro-arc-advanced-settings"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.advancedOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    // Click-outside-to-close, with the card itself swallowing clicks so a
    // mis-aimed click inside doesn't dismiss a form being filled in.
    MouseArea {
      anchors.fill: parent
      onClicked: root.advancedOpen = false
    }

    Rectangle {
      anchors.fill: parent
      color: "#cc000000"
      z: -1
    }

    Rectangle {
      id: advancedCard
      anchors.centerIn: parent
      width: Math.min(parent.width - Style.space(16) * 2, 720)
      height: Math.min(parent.height - Style.space(16) * 2, 860)
      radius: 14
      color: root.bar ? root.bar.background : "#11111b"
      border.color: Qt.darker(root.bar ? root.bar.foreground : "#cdd6f4", 2.4)
      border.width: 1

      // Escape-to-close lives here, not on the PanelWindow. PanelWindow has no
      // `focus` property at all — assigning one aborts creation of the window
      // AND of its parent, which is what stopped the whole widget from opening.
      // Keys is an attached property of Item, so it needs an Item to attach to;
      // the card is one, and the window's Exclusive keyboardFocus is what routes
      // Wayland key events here in the first place.
      focus: root.advancedOpen
      Keys.onEscapePressed: root.advancedOpen = false

      MouseArea { anchors.fill: parent }

      Column {
        id: advancedHeader
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: Style.space(14) }
        spacing: Style.space(4)

        Item {
          width: parent.width
          height: Math.max(advancedTitle.implicitHeight, advancedCloseBtn.implicitHeight)

          Text {
            id: advancedTitle
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "Advanced settings"
            color: root.bar ? root.bar.foreground : "#cdd6f4"
            font.family: root.bar ? root.bar.fontFamily : "sans"
            font.pixelSize: Style.font.body
            font.bold: true
          }

          Button {
            id: advancedCloseBtn
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "Close"
            foreground: root.bar ? root.bar.foreground : "#cdd6f4"
            onClicked: root.advancedOpen = false
          }
        }

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          text: "Anything set here overrides the quality preset, which will then read \"Custom\". Costs marked (est) are published rates applied to token counts measured from real runs; (actual) means this exact combination has been measured on this machine."
          color: Qt.darker(root.bar ? root.bar.foreground : "#cdd6f4", 1.3)
          font.family: root.bar ? root.bar.fontFamily : "sans"
          font.pixelSize: Style.font.caption
        }
      }

      Flickable {
        anchors { top: advancedHeader.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; margins: Style.space(14) }
        contentHeight: advancedContent.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: advancedContent
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader { text: "MODEL LIST"; foreground: root.bar.foreground }

          Item {
            width: parent.width
            height: Math.max(advModelStatus.implicitHeight, advRefreshBtn.implicitHeight, advShowAllBtn.implicitHeight)

            Text {
              id: advModelStatus
              anchors.left: parent.left
              anchors.right: advShowAllBtn.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              wrapMode: Text.WordWrap
              text: {
                if (root.modelCatalogLoading) return "Refreshing…"
                if (root.modelCatalogError !== "") return root.modelCatalogError
                if (!root.modelCatalog.everFetched) return "Model list not fetched yet"
                var n = (root.modelCatalog.image || []).length + (root.modelCatalog.chat || []).length
                return n + " reachable" + (root.modelCatalog.stale ? " · over a day old" : "")
              }
              color: root.modelCatalogError !== "" ? (root.bar.urgent || "#f38ba8") : Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            Button {
              id: advShowAllBtn
              anchors.right: advRefreshBtn.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              text: root.showAllModels ? "Shortlist" : "All models"
              foreground: root.bar.foreground
              onClicked: root.showAllModels = !root.showAllModels
            }

            Button {
              id: advRefreshBtn
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Refresh"
              enabled: !root.modelCatalogLoading
              foreground: root.bar.foreground
              onClicked: root.loadModelCatalog(true)
            }
          }

          PanelSeparator { foreground: root.bar.foreground }
          PanelSectionHeader { text: "STAGE 1 · INTERPRETATION"; foreground: root.bar.foreground }

          AdvancedChatPicker { stage: "stage1" }
          ApiKeySection { slot: "stage1" }

          PanelSeparator { foreground: root.bar.foreground }
          PanelSectionHeader { text: "STAGE 2 · IMAGE PROMPT"; foreground: root.bar.foreground }

          AdvancedChatPicker { stage: "stage2" }
          ApiKeySection { slot: "stage2" }

          PanelSeparator { foreground: root.bar.foreground }
          PanelSectionHeader { text: "IMAGE GENERATION"; foreground: root.bar.foreground }

          Item {
            width: parent.width
            height: Math.max(advImageLabel.implicitHeight, advImageDropdown.implicitHeight)

            Text {
              id: advImageLabel
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: root.labelColW
              text: "Model"
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Dropdown {
              id: advImageDropdown
              anchors.left: advImageLabel.right
              anchors.leftMargin: Style.space(8)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              showLabel: false
              value: Model.openaiModelDropdownValue(root.configState.openaiModel, root.configState.openaiQuality)
              options: {
                var rows = Model.imageRowsWithinCeiling(
                  Model.visibleRows(root.modelCatalog.image || [], root.showAllModels),
                  root.configState.maxCostPerImage)
                var opts = rows.map(function(r) {
                  return { value: Model.openaiModelDropdownValue(r.model, r.quality), label: Model.imageModelLabel(r) }
                })
                var current = Model.openaiModelDropdownValue(root.configState.openaiModel, root.configState.openaiQuality)
                if (current && !opts.some(function(o) { return o.value === current }))
                  opts.unshift({ value: current, label: root.configState.openaiModel + " — " + root.configState.openaiQuality + "  · over ceiling" })
                return opts
              }
              foreground: root.bar.foreground
              onChanged: function(value) { root.commitOpenaiModel(value) }
            }
          }

          Item {
            width: parent.width
            height: Math.max(advCeilingLabel.implicitHeight, advCeilingField.implicitHeight)

            Text {
              id: advCeilingLabel
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: root.labelColW
              text: "Max $/image"
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            TextField {
              id: advCeilingField
              anchors.left: advCeilingLabel.right
              anchors.leftMargin: Style.space(8)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              placeholderText: "0.00 (no ceiling)"
              foreground: root.bar.foreground
              font.family: root.bar.fontFamily
              onEditingFinished: root.commitMaxCostPerImage(text)
              Component.onCompleted: text = String(root.configState.maxCostPerImage)
              Connections {
                target: root
                function onConfigStateChanged() {
                  if (!advCeilingField.activeFocus) advCeilingField.text = String(root.configState.maxCostPerImage)
                }
              }
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: root.maxCostPerImageError !== ""
            text: root.maxCostPerImageError
            color: root.bar.urgent || "#f38ba8"
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          ApiKeySection { slot: "image" }

          PanelSeparator { foreground: root.bar.foreground }
          PanelSectionHeader { text: "DESTRUCTIVE"; foreground: root.bar.foreground }

          // Moved out of the main panel: it sat next to "Save Selected Theme",
          // one row apart from the thing it destroys, and a mis-click there
          // deletes every generation. Two clicks are still required (the button
          // arms, then confirms) — this just puts it somewhere you have to mean
          // to go.
          Column {
            width: parent.width
            spacing: Style.space(4)

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: "Deletes every entry in Themes Generated immediately, ignoring the retention window. Saved themes are not affected."
              color: Qt.darker(root.bar.foreground, 1.3)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }

            Button {
              id: advClearHistoryBtn
              text: root.clearHistoryArmed ? "Confirm Clear?" : "Clear History"
              bordered: true
              fontSize: Style.font.caption
              foreground: root.clearHistoryArmed ? (root.bar.urgent || "#f38ba8") : root.bar.foreground
              onClicked: root.clearHistoryClicked()
            }
          }

        }
      }
    }
  }

  // One chat-model picker, used for both stages in the popout. The two stages
  // are separate controls here precisely because splitting them is the point of
  // this window; the panel shows them merged as "Chat models".
  component AdvancedChatPicker: Item {
    id: picker
    required property string stage
    readonly property string currentModel: stage === "stage1" ? root.configState.stage1Model : root.configState.stage2Model

    width: parent.width
    height: Math.max(pickerLabel.implicitHeight, pickerDropdown.implicitHeight)

    Text {
      id: pickerLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: root.labelColW
      text: "Model"
      color: Qt.darker(root.bar.foreground, 1.3)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: pickerDropdown
      anchors.left: pickerLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: picker.currentModel
      options: {
        var opts = Model.visibleRows(root.modelCatalog.chat || [], root.showAllModels).map(function(r) {
          return { value: r.model, label: Model.chatModelLabel(r) }
        })
        var cur = picker.currentModel
        if (cur && !opts.some(function(o) { return o.value === cur }))
          opts.unshift({ value: cur, label: cur + "  · not in catalog" })
        return opts
      }
      foreground: root.bar.foreground
      onChanged: function(value) {
        if (picker.stage === "stage1") root.commitStage1Model(value)
        else root.commitStage2Model(value)
      }
    }
  }

}
