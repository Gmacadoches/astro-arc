import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "sections"

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
  // Where Astro-Arc keeps things: the QML copy of backend/bin/paths.py and
  // astro-arc-paths.sh, which explain the split. Change a location in all
  // three. The archive is every generation's page, wallpaper and theme, plus
  // the gallery: the user's data, so it lives in ~/.local/share rather than
  // in state, and outlives the plugin.
  readonly property string archiveDir: (Quickshell.env("XDG_DATA_HOME") || (home + "/.local/share")) + "/astro-arc"
  readonly property string galleryFile: archiveDir + "/index.html"
  readonly property string stateDir: home + "/.local/state/omarchy/astro-arc"
  readonly property string configPath: home + "/.local/state/omarchy/settings/astro-arc.json"

  readonly property string configBin: binDir + "/astro-arc-config"
  readonly property string generateBin: binDir + "/astro-arc-generate"

  // ---- Lifecycle -----------------------------------------------------
  function open() {
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    syncDraftsFromConfig()
    refreshVersion()
    configFile.reload()
    lastRunFile.reload()
    reviewsIndexFile.reload()
    costsFile.reload()
    loadModelCatalogIfStale()
    loadStyleCatalog()
    defaultAgentProc.running = true
  }

  function openFromHotkey() {
    open()
  }

  function close() {
    setCenterHoverRevealSuppressed(false)
    if (root.editingLocation) root.cancelEditingLocation()
    // Reopening starts on the main view (or Settings, which stays put as
    // it always has); a half-done drill-in doesn't wait behind the icon.
    root.closeDrillIns("settings")
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
  // The settings, as astro-arc-config prints them: every key present and every
  // value one its key may hold (backend/pipeline/settings.toml). The panel
  // keeps no defaults of its own; until the first read lands, configState is
  // empty and configLoaded false.
  property var configState: ({})
  property bool configLoaded: false
  property var lastRun: null

  // Watched only as the signal that the settings changed; they are read
  // through astro-arc-config, so the panel sees what every other reader sees.
  property FileView configFile: FileView {
    path: root.configPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.readConfig()
    onLoadFailed: root.readConfig()
  }

  property bool configReadPending: false
  function readConfig() {
    if (configReadProc.running) { configReadPending = true; return }
    configReadProc.running = true
  }

  Process {
    id: configReadProc
    command: [root.configBin]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          root.configState = JSON.parse(String(text || "{}"))
          root.configLoaded = true
          root.checkAutoGenerate()
        } catch (e) {}
      }
    }
    onExited: {
      if (root.configReadPending) {
        root.configReadPending = false
        root.readConfig()
      }
    }
  }

  // Every settings write, one at a time, in order: two quick toggles can never
  // race each other to the file. `onDone(exitCode)` runs after the write; a
  // successful one is read back through readConfig.
  property var configWriteQueue: []
  property var configWriteDone: null
  function writeConfig(args, onDone) {
    configWriteQueue = configWriteQueue.concat([{ args: args, onDone: onDone || null }])
    if (!configWriteProc.running) nextConfigWrite()
  }
  function nextConfigWrite() {
    if (configWriteQueue.length === 0) return
    var job = configWriteQueue[0]
    configWriteQueue = configWriteQueue.slice(1)
    configWriteDone = job.onDone
    configWriteProc.command = [root.configBin].concat(job.args)
    configWriteProc.running = true
  }

  Process {
    id: configWriteProc
    onExited: function(exitCode) {
      var done = root.configWriteDone
      root.configWriteDone = null
      if (exitCode === 0) root.configFile.reload()
      if (done) done(exitCode)
      root.nextConfigWrite()
    }
  }

  property FileView lastRunFile: FileView {
    path: root.stateDir + "/last-run.json"
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
    // Fields are plain (unbound) ClipboardSafeFields — see the note on
    // birthDateField/birthTimeField in sections/BirthLocationSection.qml — so the displayed text needs an
    // imperative push here too, deferred until after the fields exist on
    // first load.
    Qt.callLater(function() {
      birthLocationSection.birthDateField.text = root.draftBirthDate
      birthLocationSection.birthTimeField.text = root.draftBirthTime
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

    root.writeConfig(["--set-birth", draftBirthDate, timeArg])
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
      birthLocationSection.locationField.text = root.configState.locationName
      birthLocationSection.locationField.selectAll()
      birthLocationSection.locationField.forceActiveFocus()
    })
  }

  function cancelEditingLocation() {
    editingLocation = false
    savingLocation = false
    locationSuggestions = []
    geocodeDebounce.stop()
  }

  function commitLocation() {
    var location = Model.locationCommit(birthLocationSection.locationField.text, locationSuggestions, suggestionIndex)
    if (location.name === "") {
      clearLocation()
      return
    }
    savingLocation = true
    persistLocation(location.name, location.latitude, location.longitude, location.timezone)
  }

  function clearLocation() {
    savingLocation = true
    persistLocation("", null, null)
  }

  function pickSuggestion(suggestion) {
    if (!suggestion) return
    savingLocation = true
    persistLocation(suggestion.name, suggestion.latitude, suggestion.longitude, suggestion.timezone)
  }

  function persistLocation(name, latitude, longitude, timezone) {
    var args
    if (name && Model.isValidCoordinate(latitude, longitude))
      args = timezone
        ? ["--set-location", name, latitude + "," + longitude, timezone]
        : ["--set-location", name, latitude + "," + longitude]
    else if (name)
      args = ["--set-location", name]
    else
      args = ["--clear-location"]
    root.writeConfig(args, function() {
      root.savingLocation = false
      root.cancelEditingLocation()
    })
  }

  function requestGeocode() {
    var query = birthLocationSection.locationField.text.trim()
    if (query.length < 2) {
      locationSuggestions = []
      return
    }
    geocodePendingQuery = query
    if (!geocodeProc.running) startGeocode()
  }

  function startGeocode() {
    geocodeActiveQuery = geocodePendingQuery
    // A deadline and a size cap: the reply is a few KB of JSON, and the
    // output is buffered in the shell itself, so anything larger is refused
    // rather than read (as astro-arc-generate's timezone lookup does).
    geocodeProc.command = ["curl", "-fsS", "--max-time", "5", "--max-filesize", "1048576",
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

  function restartGeocodeDebounce() {
    geocodeDebounce.restart()
  }

  // ---- Frequency ------------------------------------------------------
  function commitFrequency(value) {
    root.writeConfig(["--set-frequency", value])
  }

  function commitIntervalHours(value) {
    root.writeConfig(["--set-interval-hours", value])
  }

  Connections {
    target: root
    function onConfigStateChanged() {
      if (scheduleSection.frequencyDropdown.value !== root.configState.frequency)
        scheduleSection.frequencyDropdown.value = root.configState.frequency
      if (scheduleSection.intervalHoursDropdown.value !== String(root.configState.intervalHours))
        scheduleSection.intervalHoursDropdown.value = String(root.configState.intervalHours)
    }
  }

  // ---- Generation -------------------------------------------------------
  property bool generating: false
  property string generateError: ""

  // ifDue selects astro-arc-generate's `--if-due` flag: the manual
  // Regenerate button always forces a run (ifDue: false), while the
  // widget's own auto-check (below) asks the script to first re-check
  // last-run.json itself and no-op if the period's already covered —
  // closing the race where a manual Regenerate finished a generation for
  // this period in the moment between this widget's own JS-side check and
  // this process actually starting.
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
  // This Timer IS the scheduler. The bar widget loads this panel eagerly (see
  // BarWidget.qml's Loader), so it polls from the moment the shell starts,
  // whether or not the panel is ever opened. Each tick asks
  // `astro-arc-generate --check-due`, and only on a yes starts
  // `astro-arc-generate --if-due`, which re-checks and holds a lock so a manual
  // click can never double-render. The panel keeps no copy of the rule:
  // period_key.py says what a period is, and astro-arc-generate whether the
  // current one is covered or was already attempted.
  //
  // There used to be a systemd timer too, installed by an install script as a
  // "backstop". On Omarchy the shell IS the desktop, so it only ever added
  // coverage for a logged-in session with no shell — and it outlived the
  // plugin, firing into a deleted script after removal. Both are gone.
  //
  // "New period" is a period-key comparison, never elapsed-time math, so
  // "daily" means "the calendar date changed", not "24 hours since the last
  // run": the first tick after midnight (at most autoGenerateCheckInterval
  // later) starts that day's generation.
  property bool lastRunLoaded: false
  readonly property int autoGenerateCheckInterval: 5 * 60 * 1000

  function checkAutoGenerate() {
    if (!root.configLoaded || !root.lastRunLoaded || root.generating || dueCheckProc.running) return
    // "manual" schedules nothing, so there is no need to spawn a process to be
    // told so.
    if (root.configState.frequency === "manual") return
    dueCheckProc.running = true
  }

  Process {
    id: dueCheckProc
    command: [root.generateBin, "--check-due"]
    onExited: function(exitCode) { if (exitCode === 0) root.startGenerate(true) }
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

  // The schedule in a word or two, for the hero: "Daily", "Every 3 hours",
  // "Manual".
  readonly property string scheduleSummary: {
    var f = root.configState.frequency
    if (f === "manual") return "Manual"
    if (f === "hourly") {
      var s = Model.scheduleLabel(f, root.configState.intervalHours)
      return s.charAt(0).toUpperCase() + s.slice(1)
    }
    var choice = Model.FREQUENCY_CHOICES.filter(function(c) { return c.key === f })[0]
    return choice ? choice.label : f
  }

  // heroStatus: compact one-line summary shown under the title, matching
  // the built-in bluetooth/network panels' icon+title+status hero row:
  // state · schedule · last run. The schedule lives here now that the main
  // view has no footer; the last run's time is the time alone on a run from
  // today, with the date otherwise.
  readonly property string heroStatus: root.generating
    ? "Generating… · " + root.scheduleSummary
    : (root.lastRun
      ? "Up to date · " + root.scheduleSummary + " · " + Model.formatGeneratedAt(root.lastRun.generatedAt, function(d) {
          var today = new Date()
          return Qt.formatDateTime(d, d.toDateString() === today.toDateString() ? "h:mm AP" : "MMM d, h:mm AP")
        })
      : "Not generated yet · " + root.scheduleSummary)

  // ---- Views. One KeyboardPanel, one fixed height: the main view, or one
  // drill-in at a time — Settings, Tweak, Create, History (Browse all) and
  // Fix (a style that failed validation) — each with a back button. Each has
  // its own flag, so the logic that opens and closes them stays where it
  // was; `view` only says which one is showing. -----------------------------
  property bool browseOpen: false
  property bool fixOpen: false
  readonly property string view: showSettings ? "settings"
    : tweakOpen ? "tweak"
    : styleCreateMode !== "" ? "create"
    : browseOpen ? "browse"
    : fixOpen ? "fix"
    : "main"

  // Close every drill-in but `keep`, so opening one never leaves another
  // underneath it.
  function closeDrillIns(keep) {
    if (keep !== "settings" && showSettings) toggleSettings()
    if (keep !== "tweak") tweakOpen = false
    if (keep !== "create" && styleCreateMode !== "") { styleCreateMode = ""; styleImportResult = null }
    if (keep !== "browse") browseOpen = false
    if (keep !== "fix") fixOpen = false
  }

  function openView(name) {
    navReturnItem = navCurrentItem()
    closeDrillIns(name)
    styleActionStatus = ""
    if (name === "settings" && !showSettings) toggleSettings()
    else if (name === "tweak") { tweakOpen = true; tweakBackupFile.reload() }
    else if (name === "create") { styleNoAgent = false; styleCreateMode = "create" }
    else if (name === "browse") browseOpen = true
    else if (name === "fix") fixOpen = true
    Qt.callLater(navReset)
  }

  // Back to the main view, focus returned to whatever opened the drill-in.
  function goBack() {
    closeDrillIns("")
    var back = navReturnItem
    navReturnItem = null
    Qt.callLater(function() {
      if (back && back.visible) back.forceActiveFocus()
      else navReset()
    })
  }

  // ---- Main view: browsing generations with ‹ › and the thumbnail strip.
  readonly property int selectedIndex: {
    for (var i = 0; i < reviewsIndex.length; i++)
      if (reviewsIndex[i].id === selectedReviewId) return i
    return -1
  }

  function showOlder() {
    if (selectedIndex >= 0 && selectedIndex < reviewsIndex.length - 1)
      selectedReviewId = reviewsIndex[selectedIndex + 1].id
  }

  function showNewer() {
    if (selectedIndex > 0) selectedReviewId = reviewsIndex[selectedIndex - 1].id
  }

  // The reading saved with the selected generation; for the newest, an
  // entry from before generations.json carried readings falls back to
  // last-run.json, which is the same run.
  readonly property string selectedReading: selectedReview && selectedReview.distillation !== ""
    ? selectedReview.distillation
    : ((selectedIsNewest || reviewsIndex.length === 0) && lastRun ? lastRun.distillation : "")
  readonly property string selectedPosition: selectedReview && selectedReview.distillation !== ""
    ? selectedReview.narrativePosition
    : ((selectedIsNewest || reviewsIndex.length === 0) && lastRun ? lastRun.narrativePosition : "")

  // "Oct 17 · 8:40 PM" and "Dragon Ball Z" for a generation's caption.
  function reviewWhen(review) {
    if (!review || review.createdAt === "") return review ? review.label : ""
    var d = new Date(review.createdAt)
    return isNaN(d.getTime()) ? review.label : Qt.formatDateTime(d, "MMM d · h:mm AP")
  }

  function reviewStyleLabel(review) {
    if (!review) return ""
    if (review.artStyleLabel !== "") return review.artStyleLabel
    var style = Model.findStyle(styleCatalog, Model.reviewStyleKey(review, styleCatalog))
    return style ? style.label : ""
  }

  // ---- Keyboard navigation. Every control in the showing view takes focus
  // (Tab, or the arrows by position); Enter/Space activate it, Escape goes
  // back a view or closes. A focused control keeps the keys it uses itself
  // (text fields their typing and caret keys, the image its ‹ ›), and only
  // what it ignores reaches PanelKeyCatcher. -------------------------------
  property var navReturnItem: null
  // The showing view's root, and its Flickable (so a control moved to is
  // scrolled into sight; the history grid scrolls itself).
  readonly property Item navRoot: view === "settings" ? settingsColumn
    : view === "tweak" ? tweakView
    : view === "create" ? createView
    : view === "fix" ? fixView
    : view === "browse" ? browseView
    : mainColumn
  readonly property Item navFlick: view === "settings" ? settingsFlick
    : view === "tweak" ? tweakFlick
    : view === "create" ? createFlick
    : view === "fix" ? fixFlick
    : view === "browse" ? null
    : mainFlick
  // What popups and pickers must stay inside: the area under the hero.
  readonly property Item contentArea: viewArea
  // The main view's height, latched while it shows (see mainColumn), and
  // the gap between the hero and the views.
  property real mainViewHeight: 0
  readonly property int viewGap: Style.space(14)

  // A fixed style leaves its Fix view with nothing to show.
  onFailedStyleChanged: if (fixOpen && failedStyle === null) goBack()

  // A control loses focus when its view hides; whatever changed the view,
  // the keys must still reach the catcher afterwards. The new view fades in;
  // the orbits behind it stay where they are.
  onViewChanged: {
    viewFade.restart()
    Qt.callLater(function() {
      if (root.opened && !root.navCurrentItem()) keyCatcher.forceActiveFocus()
    })
  }

  function navCandidates() {
    var out = []
    function walk(item) {
      if (!item || !item.visible) return
      if (item.activeFocusOnTab && item.enabled !== false && item.width > 0 && item.height > 0) out.push(item)
      var kids = item.children
      for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(heroSection)
    walk(navRoot)
    return out
  }

  function navCurrentItem() {
    var c = navCandidates()
    for (var i = 0; i < c.length; i++) if (c[i].activeFocus) return c[i]
    return null
  }

  function navFocus(item) {
    if (!item) return
    item.forceActiveFocus()
    // Scroll a control in a scrolling view into sight.
    if (navFlick && navFlick.contentItem && item !== navFlick) {
      var p = item.mapToItem(navFlick.contentItem, 0, 0)
      if (p.y < navFlick.contentY) navFlick.contentY = Math.max(0, p.y - Style.space(8))
      else if (p.y + item.height > navFlick.contentY + navFlick.height)
        navFlick.contentY = Math.min(Math.max(0, navFlick.contentHeight - navFlick.height),
                                     p.y + item.height - navFlick.height + Style.space(8))
    }
  }

  // A view just appeared: nothing in it is focused, keys go to the catcher.
  function navReset() {
    if (navFlick) navFlick.contentY = 0
    keyCatcher.forceActiveFocus()
  }

  function navTab(direction) {
    var c = navCandidates()
    if (c.length === 0) return
    var i = c.indexOf(navCurrentItem())
    if (i < 0) navFocus(direction > 0 ? c[0] : c[c.length - 1])
    else navFocus(c[(i + direction + c.length) % c.length])
  }

  // Spatial: the nearest control in the arrow's direction, preferring ones in
  // line with the current one (same row for ←→, same column for ↑↓).
  function navMove(dx, dy) {
    var c = navCandidates()
    var cur = navCurrentItem()
    if (!cur) {
      // Nothing focused yet: ←→ browse the generations on the main view,
      // ↑↓ start at the top or the bottom.
      if (dx !== 0 && view === "main") { if (dx < 0) showOlder(); else showNewer(); return }
      if (c.length > 0) navFocus(dy < 0 ? c[c.length - 1] : c[0])
      return
    }
    var base = keyCatcher
    var a = cur.mapToItem(base, 0, 0)
    var best = null, bestScore = Infinity
    for (var i = 0; i < c.length; i++) {
      if (c[i] === cur) continue
      var b = c[i].mapToItem(base, 0, 0)
      var along, across
      if (dx !== 0) {
        // Horizontal neighbours must overlap the current row.
        if (b.y + c[i].height <= a.y + 2 || b.y >= a.y + cur.height - 2) continue
        along = dx > 0 ? b.x - (a.x + cur.width) : a.x - (b.x + c[i].width)
        across = Math.abs((b.y + c[i].height / 2) - (a.y + cur.height / 2))
      } else {
        along = dy > 0 ? b.y - (a.y + cur.height) : a.y - (b.y + c[i].height)
        var overlap = Math.min(a.x + cur.width, b.x + c[i].width) - Math.max(a.x, b.x)
        across = overlap > 0 ? 0 : -overlap
      }
      if (along < -2) continue
      var score = Math.max(0, along) + across * 2
      if (score < bestScore) { bestScore = score; best = c[i] }
    }
    if (best) navFocus(best)
  }

  // Escape: a drill-in goes back; the main view closes the panel.
  function navEscape() {
    if (view !== "main") goBack()
    else close()
  }

  // ---- Settings sub-view (image backend/model, Stage 1/2 models, API
  // keys) -------------------------------------------------------------------
  // A second state of this same panel (toggled by the hero's gear icon)
  // rather than a separate Panel.qml — everything about anchoring/focus/
  // popout-coordination is already solved for this panel, and duplicating
  // that just to host a handful of extra rows isn't worth it.
  property bool showSettings: false
  readonly property string apiKeyBin: binDir + "/astro-arc-apikey"
  readonly property string openaiImageGenBin: binDir + "/openai_image_gen.py"
  // The system's own Python: the backend uses the standard library only.
  readonly property string python: "python3"

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

  // Keys are scoped to the provider, matching astro-arc-apikey: switching
  // provider never overwrites the other provider's stored key.
  function accountForSlot(slot) {
    var prefix = root.configState.provider === "openrouter" ? "openrouter-api-key" : "openai-api-key"
    return slot === "stage1" ? prefix + "-stage1" : slot === "stage2" ? prefix + "-stage2" : prefix
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
      : ["secret-tool", "store", "--label=Astro-Arc " + Model.providerLabel(root.configState.provider) + " API Key (" + slot + ")", "service", "astro-arc", "account", root.accountForSlot(slot)]
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
    apiKeyValidateProc.command = [root.python, root.openaiImageGenBin, "--validate", slot]
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
    root.writeConfig(["--set-image-backend", value])
  }

  function commitOpenaiModel(compositeValue) {
    var parts = compositeValue.split("|")
    root.writeConfig(["--set-openai-model", parts[0], parts[1] || ""])
  }

  // ---- Stage 1 / Stage 2 chat models — separate from the image-render
  // model above, and from each other, since interpreting a chart and
  // writing an image prompt are different tasks that may call for
  // different models (and different API keys — see apiKeyState). ---------
  function commitStage1Model(value) {
    if (!value) return
    root.writeConfig(["--set-stage1-model", value])
  }

  function commitStage2Model(value) {
    if (!value) return
    root.writeConfig(["--set-stage2-model", value])
  }

  // ---- Background size — "auto" (default) re-detects the focused
  // monitor at generation time; a fixed "WxH" pins it. Either way,
  // astro-arc-generate resolves this into the exact final background
  // size regardless of what either image backend natively renders at
  // (see image_fit.py's fit_cover). --------------------------------------
  property string backgroundSizeError: ""
  // ---- Provider: the one global setting. -----------------------------
  function commitProvider(value) {
    root.writeConfig(["--set-provider", value], function(exitCode) {
      if (exitCode !== 0) return
      // Everything the panel shows is scoped to the provider: its key, its
      // model list and its prices. astro-arc-config has already moved the
      // models to the new provider's recommended preset.
      root.refreshApiKeyStatus("stage1")
      root.refreshApiKeyStatus("stage2")
      root.refreshApiKeyStatus("image")
      root.loadModelCatalog(false)
    })
  }

  // ---- The popout. The panel's settings view holds only what a reading
  // needs (birth, place, schedule); everything else lives here: provider and
  // key, the quality preset, background size, theme, usage, every model the
  // key can reach, the two chat stages split apart, a separate key per slot,
  // and the cost ceiling.
  property bool advancedOpen: false

  // The key rows live in the popout, so their status is fetched when it
  // opens, and a half-finished Replace is dropped when it closes.
  onAdvancedOpenChanged: {
    var slots = ["stage1", "stage2", "image"]
    for (var i = 0; i < slots.length; i++) {
      if (advancedOpen) refreshApiKeyStatus(slots[i])
      else setApiKeyField(slots[i], "replacing", false)
    }
  }

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
    root.writeConfig(["--set-models",
      preset.chat, preset.chat, preset.image, preset.quality, preset.reasoning || "default"])
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
    root.writeConfig(["--set-max-cost-per-image", trimmed])
  }

  // ---- Updates: deliberately not done from in here. The plugin does not
  // update itself; `omarchy plugin update` does, which is Omarchy's own code
  // path and not something this plugin can influence. A button that fetched
  // tags and checked one out would let whatever the remote offers next become
  // executable plugin code, so the panel only reports the version it is on and
  // links to the releases page. ---------------------------------------------

  function openArchive() {
    Quickshell.execDetached(["xdg-open", root.galleryFile])
  }

  // ---- Version: read from git by astro-arc-version, so a release is a tag
  // and nothing in here is edited per release. Links to that release's GitHub
  // page. Refreshed each time the panel opens, since `omarchy plugin update`
  // can move the checkout while the shell keeps running. ---------------------
  readonly property string versionBin: binDir + "/astro-arc-version"
  property string versionLabel: ""
  property string versionUrl: ""
  property int versionAhead: 0
  property string versionCommit: ""

  function refreshVersion() {
    if (!versionProc.running) versionProc.running = true
  }

  Process {
    id: versionProc
    command: [root.versionBin]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = null
        try { parsed = JSON.parse(String(text || "")) } catch (e) { parsed = null }
        root.versionLabel = parsed ? String(parsed.label || "") : ""
        root.versionUrl = parsed ? String(parsed.url || "") : ""
        root.versionAhead = parsed ? (parsed.ahead || 0) : 0
        root.versionCommit = parsed ? String(parsed.commit || "") : ""
      }
    }
  }

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
    var cmd = [root.python, root.modelCatalogBin, "--size", root.configState.backgroundSize || "1024x1024"]
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

  // The settings model_catalog.py's `nextRun` cost is computed FROM (see its
  // main(), which reads openaiQuality, openaiModel, the two chat models and
  // the background size straight out of the config). Those are inputs to the
  // number, so the number has to be recomputed when any of them changes.
  //
  // Without this, picking a different quality in Settings wrote the config,
  // updated the "Image model: ... · high" readout right next to it, and left
  // "per full run" showing the cost of the tier you just moved away from —
  // which is the one figure the choice is being made on.
  //
  // A reload here is a disk read of the cached catalog, not a network call:
  // loadModelCatalog(false) re-runs model_catalog.py, which recomputes the
  // cost against the settings as they now stand without hitting /v1/models.
  readonly property string costInputsKey: [
    root.configState.openaiModel, root.configState.openaiQuality,
    root.configState.stage1Model, root.configState.stage2Model,
    root.configState.backgroundSize
  ].join("|")

  onCostInputsKeyChanged: {
    // Skip the first evaluation, which fires while configState is still the
    // defaults and would run a second catalog load on every panel open.
    if (root.modelCatalog.everFetched === true) root.loadModelCatalog(false)
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
    root.writeConfig(["--set-background-size", value])
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
          advancedWindow.backgroundSizeField.text = size
          root.commitBackgroundSize(size)
        } else {
          root.backgroundSizeError = "Couldn't detect the current resolution"
        }
      }
    }
  }

  // ---- Art style: which theme the run is built for. The dropdown is built
  // from the theme files themselves (backend/bin/style_catalog.py): shipped
  // ones in backend/pipeline/styles/, the user's own in
  // ~/.config/astro-arc/styles/. A theme that fails validation stays listed
  // but is never written to the config; picking it shows what is wrong. ------
  readonly property string styleCatalogBin: binDir + "/style_catalog.py"
  property var styleCatalog: []
  property bool styleCatalogLoaded: false
  // The loader's own warnings (stderr), e.g. a user file ignored because it
  // has a shipped theme's key.
  property string styleCatalogNotice: ""
  // A failed theme picked from the list to see its errors. The config keeps
  // the theme it had.
  property string inspectedStyleKey: ""
  property string styleActionStatus: ""
  property bool styleActionStatusIsError: false
  // Bumped after a preview is rewritten, so the Image reloads the same path.
  property int stylePreviewVersion: 0

  // A configured key with no theme (a deleted user theme, say). A run falls
  // back to the default and says so on stderr (resolve_style_key); the panel
  // selects the default and says the same thing where it can be seen.
  readonly property bool configuredStyleMissing: root.styleCatalogLoaded
    && !Model.findStyle(root.styleCatalog, root.configState.artStyle)
  readonly property string shownStyleKey: root.configuredStyleMissing
    ? Model.DEFAULT_ART_STYLE : root.configState.artStyle
  readonly property var shownStyle: Model.findStyle(root.styleCatalog, root.shownStyleKey)
  // The failed theme whose errors are on show: the one picked from the list,
  // or the configured one if its file has broken since it was chosen.
  readonly property var failedStyle: {
    var picked = Model.findStyle(root.styleCatalog, root.inspectedStyleKey)
    if (picked && !picked.valid) return picked
    return root.shownStyle && !root.shownStyle.valid ? root.shownStyle : null
  }

  onShownStyleKeyChanged: {
    styleSection.styleDropdown.value = root.shownStyleKey
    // An open Tweak card (and its note) was about the previous theme.
    root.tweakOpen = false
  }

  function loadStyleCatalog() {
    if (styleCatalogProc.running) return
    root.styleCatalogNotice = ""
    styleCatalogProc.running = true
  }

  function pickArtStyle(value) {
    root.styleActionStatus = ""
    if (value === Model.STYLE_CREATE || value === Model.STYLE_IMPORT) {
      root.openView("create")
      if (value === Model.STYLE_IMPORT) root.styleCreateMode = "import"
      root.styleImportResult = null
      styleSection.styleDropdown.value = root.shownStyleKey
      return
    }
    root.styleCreateMode = ""
    var row = Model.findStyle(root.styleCatalog, value)
    if (row && !row.valid) {
      root.inspectedStyleKey = value
      styleSection.styleDropdown.value = root.shownStyleKey
      // Its errors and fixes are a drill-in now.
      root.openView("fix")
      return
    }
    root.inspectedStyleKey = ""
    root.commitArtStyle(value)
  }

  function commitArtStyle(value) {
    root.writeConfig(["--set-art-style", value])
  }

  // ---- Create your own style (decision 9). The plugin never asks a model
  // to write a theme: "Create" briefs the user's Omarchy default agent with
  // the astro-arc-style skill, "Copy prompt" builds one self-contained prompt
  // for a web chat, and "Import theme" brings the chat's TOML back in. All of
  // it is backend/bin/astro-arc-style. ------------------------------------
  readonly property string styleBin: binDir + "/astro-arc-style"
  // "", "create" or "import": which of the two forms is open.
  property string styleCreateMode: ""
  property string styleIdeaDraft: ""
  // omarchy-default-agent's answer; "" when none is set.
  property string defaultAgent: ""
  // An agent launch found no default agent: the form shows the copy path.
  property bool styleNoAgent: false
  // Whether Astro-Arc will start the default agent at all: only an agent with
  // a mode that asks the user before each action (Model.AGENTS_THAT_ASK), and
  // it is always started in that mode. Otherwise the forms offer Copy prompt
  // and say why.
  readonly property bool agentCanLaunch: defaultAgent !== "" && !styleNoAgent && Model.agentAsks(defaultAgent)
  readonly property string agentWontLaunch: (defaultAgent === "" || styleNoAgent)
    ? "No default agent is set in Omarchy."
    : Model.agentLabelStart(defaultAgent) + " can't be started in a mode that asks you before each step, "
      + "so Astro-Arc doesn't start it (Claude Code, Codex, Gemini and Grok can be)."
  // astro-arc-style import's JSON: {key, path, label, errors, needsPreview, fixBlock, ...}.
  property var styleImportResult: null
  // render --estimate's JSON for the theme being given a preview.
  property var styleRenderEstimate: null
  property string styleRenderKey: ""

  function setStyleStatus(text, isError) {
    root.styleActionStatus = text
    root.styleActionStatusIsError = isError === true
  }

  function runStyle(proc, args) {
    if (proc.running) return
    root.styleActionStatus = ""
    proc.command = [root.python, root.styleBin].concat(args)
    proc.running = true
  }

  function createStyleWithAgent() {
    var idea = root.styleIdeaDraft.trim()
    if (idea === "") { setStyleStatus("Describe the style in one line first.", true); return }
    root.runStyle(styleAgentProc, ["agent", "create", idea])
  }

  function copyCreatePrompt() {
    var idea = root.styleIdeaDraft.trim()
    if (idea === "") { setStyleStatus("Describe the style in one line first.", true); return }
    styleCopyProc.doneText = "Copied. Paste it into any chat model, then bring its reply back with Import theme."
    root.runStyle(styleCopyProc, ["create-prompt", idea, "--copy"])
  }

  // Replaces Prompt 4's stubs: "Fix with AI" briefs the default agent with
  // the file and its errors, "Copy fix prompt" does the same for a web chat.
  function fixStyleWithAgent(row) {
    if (!row) return
    root.runStyle(styleAgentProc, ["agent", "fix", row.path])
  }

  function copyStyleFixPrompt(row) {
    if (!row) return
    styleCopyProc.doneText = "Copied. Paste it into your chat model, then Import theme with the corrected file."
    root.runStyle(styleCopyProc, ["fix-prompt", row.path, "--copy"])
  }

  // ---- Tweak: change a working user theme by saying what's off about it.
  // The agent (or a web chat, via Copy prompt) edits the theme in place;
  // astro-arc-style backs its files up first, and "Undo last tweak" puts
  // them back. ---------------------------------------------------------------
  property bool tweakOpen: false
  property string tweakNoteDraft: ""
  property bool tweakIncludeExample: true
  // The theme the tweak card acts on: the shown style, when it is the user's.
  readonly property var tweakTheme: shownStyle && shownStyle.source === "user" ? shownStyle : null
  // "Today's image": the newest generation's background, when it kept one.
  readonly property string tweakExampleImage: reviewsIndex.length > 0 ? reviewsIndex[0].imagePath : ""
  // The backup's manifest, when there is one: {key, files, savedAt}.
  property var tweakBackup: null

  property FileView tweakBackupFile: FileView {
    path: root.tweakTheme
      ? root.stateDir + "/tweak-backups/" + root.tweakTheme.key + "/manifest.json" : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.tweakBackup = root.parseStyleJson(text())
    onLoadFailed: root.tweakBackup = null
    onPathChanged: root.tweakBackup = null
  }

  // A drill-in view now (openView reloads the backup state on the way in).
  function toggleTweak() {
    if (root.tweakOpen) root.goBack()
    else root.openView("tweak")
  }

  function tweakArgs() {
    var args = [root.tweakTheme.path, "--note", root.tweakNoteDraft.trim()]
    if (root.tweakIncludeExample && root.tweakExampleImage !== "") args = args.concat(["--example", root.tweakExampleImage])
    return args
  }

  function tweakStyleWithAgent() {
    if (!root.tweakTheme) return
    if (root.tweakNoteDraft.trim() === "") { setStyleStatus("Say what's off about it first.", true); return }
    root.runStyle(styleAgentProc, ["agent", "tweak"].concat(root.tweakArgs()))
  }

  function copyTweakPrompt() {
    if (!root.tweakTheme) return
    if (root.tweakNoteDraft.trim() === "") { setStyleStatus("Say what's off about it first.", true); return }
    styleCopyProc.doneText = "Copied. Paste it into any chat model"
      + (root.tweakIncludeExample && root.tweakExampleImage !== "" ? ", attach " + root.tweakExampleImage : "")
      + ", then bring its reply back with Import theme; the current version is saved first, so Undo last "
      + "tweak can put it back."
    root.runStyle(styleCopyProc, ["tweak-prompt"].concat(root.tweakArgs()).concat(["--copy"]))
  }

  function undoLastTweak() {
    if (!root.tweakTheme || !root.tweakBackup) return
    root.runStyle(styleUndoProc, ["tweak-undo", root.tweakTheme.path])
  }

  Process {
    id: styleUndoProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parseStyleJson(text)
        if (r && r.ok) {
          root.setStyleStatus("Restored “" + (root.tweakTheme ? root.tweakTheme.label : r.key)
            + "” to how it was before the last tweak.", false)
          root.stylePreviewVersion++
          root.loadStyleCatalog()
        } else {
          root.setStyleStatus((r && r.error) || "Couldn't undo the tweak.", true)
        }
        root.tweakBackupFile.reload()
      }
    }
  }

  function copyImportErrors() {
    if (!root.styleImportResult) return
    Quickshell.execDetached(["wl-copy", root.styleImportResult.fixBlock])
    setStyleStatus("Copied. Paste it back into your chat.", false)
  }

  function importStyle(fromClipboard) {
    root.styleImportResult = null
    root.runStyle(styleImportProc, ["import", fromClipboard ? "--clipboard" : "--choose-file"])
  }

  // A preview by render: the cost first (--estimate), then a click to spend it.
  function estimateStyleRender(row) {
    if (!row) return
    root.styleRenderKey = row.key
    root.styleRenderEstimate = null
    root.runStyle(styleEstimateProc, ["render", row.path, "--estimate", "--json"])
  }

  function renderStylePreview() {
    var row = Model.findStyle(root.styleCatalog, root.styleRenderKey)
    var path = row ? row.path : (root.styleImportResult ? root.styleImportResult.path : "")
    if (path === "") return
    // The cost was on the button that was just clicked: that is the yes.
    root.runStyle(styleRenderProc, ["render", path, "--json"])
  }

  function styleRenderButtonText() {
    if (styleRenderProc.running) return "Rendering…"
    var e = root.styleRenderEstimate
    if (!e) return "Render a preview"
    return "Render a preview (" + (typeof e.cost === "number" ? "about " + Model.formatUsd(e.cost) : "cost unknown") + ")"
  }

  function parseStyleJson(text) {
    try { return JSON.parse(String(text || "")) } catch (e) { return null }
  }

  // The skill is not linked anywhere on load: the agent's brief names the
  // skill file directly, and writing into other programs' folders is the
  // user's call (astro-arc-style install-skill, documented in the README).

  Process {
    id: defaultAgentProc
    command: ["omarchy-default-agent"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.defaultAgent = String(text || "").trim()
    }
  }

  Process {
    id: styleAgentProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parseStyleJson(text)
        if (r && r.ok) {
          root.setStyleStatus(Model.agentLabelStart(r.agent) + " is opening in a terminal. It asks you before each "
            + "change and before anything that costs money, telling you the cost first.", false)
          root.tweakBackupFile.reload()
        } else if (r && r.unsupportedAgent) {
          root.setStyleStatus(r.error, true)
        } else if (r && r.noAgent) {
          root.defaultAgent = ""
          root.styleNoAgent = true
          root.setStyleStatus("No default agent is set in Omarchy, so use Copy prompt with any chat model instead "
            + "(or set one with: omarchy default agent).", true)
        } else {
          root.setStyleStatus((r && r.error) || "Couldn't open the agent.", true)
        }
      }
    }
  }

  Process {
    id: styleCopyProc
    property string doneText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parseStyleJson(text)
        if (r && r.ok) {
          root.setStyleStatus(styleCopyProc.doneText, false)
          root.tweakBackupFile.reload()
        }
        else root.setStyleStatus((r && r.error) || "Couldn't build the prompt.", true)
      }
    }
  }

  Process {
    id: styleImportProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parseStyleJson(text)
        if (r && r.ok && r.cancelled) return
        if (!r || !r.ok) { root.setStyleStatus((r && r.error) || "Couldn't import the theme.", true); return }
        root.styleImportResult = r
        root.styleRenderKey = r.key
        root.styleRenderEstimate = null
        root.loadStyleCatalog()
        if (r.errors.length === 0 && r.needsPreview) root.runStyle(styleEstimateProc, ["render", r.path, "--estimate", "--json"])
        if (r.errors.length === 0 && !r.needsPreview)
          root.setStyleStatus("“" + r.label + "” is ready: pick it from the Style list.", false)
      }
    }
  }

  Process {
    id: styleEstimateProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.styleRenderEstimate = root.parseStyleJson(text)
    }
  }

  Process {
    id: styleRenderProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parseStyleJson(text)
        if (r && r.ok) {
          root.setStyleStatus("Preview rendered (spent " + Model.formatUsd(r.spent) + "). The theme is ready: pick it from the Style list.", false)
          root.styleRenderEstimate = null
          root.styleImportResult = null
          root.stylePreviewVersion++
          root.loadStyleCatalog()
        } else {
          root.setStyleStatus((r && r.error) || "The render failed.", true)
        }
      }
    }
  }

  // Both write <key>.jpg into a user theme's folder; shipped previews are
  // read-only and style_catalog.py refuses them.
  function setStylePreview(key, imagePath) {
    if (stylePreviewProc.running) return
    root.styleActionStatus = ""
    stylePreviewProc.command = [root.python, root.styleCatalogBin, "--set-preview", key, imagePath]
    stylePreviewProc.running = true
  }

  function changeStylePreview(key) {
    if (stylePreviewProc.running) return
    root.styleActionStatus = ""
    stylePreviewProc.command = [root.python, root.styleCatalogBin, "--change-preview", key]
    stylePreviewProc.running = true
  }

  Process {
    id: styleCatalogProc
    command: [root.python, root.styleCatalogBin]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var rows = JSON.parse(String(text || ""))
          if (Array.isArray(rows)) {
            root.styleCatalog = Model.sortStyleCatalog(rows)
            root.styleCatalogLoaded = true
            return
          }
        } catch (e) {}
        root.styleCatalogNotice = "Couldn't read the theme list"
      }
    }
    // Only the loader's warnings, never a parse failure reported above.
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").trim().split("\n").filter(function(l) { return l !== "" })
        if (lines.length > 0) root.styleCatalogNotice = lines
          .map(function(l) { return l.replace(/^astro-arc: warning: /, "") }).join("\n")
      }
    }
  }

  Process {
    id: stylePreviewProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var result = null
        try { result = JSON.parse(String(text || "")) } catch (e) {}
        if (result && result.ok && result.cancelled) return
        if (result && result.ok) {
          root.styleImportResult = null
          root.styleRenderEstimate = null
          root.styleActionStatus = "Preview updated."
          root.styleActionStatusIsError = false
          root.stylePreviewVersion++
          root.loadStyleCatalog()
        } else {
          root.styleActionStatus = (result && result.error) ? result.error : "Could not set the preview."
          root.styleActionStatusIsError = true
        }
      }
    }
  }

  // ---- Theme generator: "built-in" (palette_extract.py, default) or
  // "aether" (shells out to Omarchy's own theme generator for a richer
  // theme — see palette_extract.py's icons.theme comment for why the
  // built-in one alone left the file manager's icon color never changing).
  // astro-arc-generate falls back to built-in automatically if aether is
  // missing or fails, so picking it here never risks a broken generation.
  function commitThemeGenerator(value) {
    root.writeConfig(["--set-theme-generator", value])
  }

  // ---- Background only (default on): each generation changes the wallpaper
  // and nothing else. Off, it also applies the derived palette as the
  // astro-arc theme — which repaints every app a theme touches, and replaces
  // whatever theme the user was running. -----------------------------------
  function commitBackgroundOnly(value) {
    root.writeConfig(["--set-background-only", value ? "true" : "false"])
  }

  // ---- Animated background: whether the orbits behind the panel move.
  function commitAnimatedBackground(value) {
    root.writeConfig(["--set-animated-background", value ? "true" : "false"])
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
    root.writeConfig(["--set-history-retention-days", String(parseInt(value, 10))])
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
    path: root.stateDir + "/pipeline/costs.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.costsSummary = Model.sumCosts(text())
    onLoadFailed: root.costsSummary = { total: 0, unknownCount: 0, generationCount: 0 }
  }

  // ---- Reset usage: starts "Spent" over at zero (astro-arc-reset-usage).
  // The old costs.json is kept beside it as costs-reset-<time>.json, so this
  // is recoverable, but it still arms on the first click and reverts after 4s
  // like Clear History: a total that quietly went to zero is easy to miss. ---
  readonly property string resetUsageBin: binDir + "/astro-arc-reset-usage"
  property bool resetUsageArmed: false
  property string resetUsageError: ""

  function resetUsageClicked() {
    if (resetUsageProc.running) return
    if (root.resetUsageArmed) {
      root.resetUsageArmed = false
      resetUsageArmTimer.stop()
      root.resetUsageError = ""
      resetUsageProc.running = true
    } else {
      root.resetUsageArmed = true
      resetUsageArmTimer.restart()
    }
  }

  Timer {
    id: resetUsageArmTimer
    interval: 4000
    onTriggered: root.resetUsageArmed = false
  }

  Process {
    id: resetUsageProc
    command: [root.resetUsageBin]
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.resetUsageError = String(text || "").trim() }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.resetUsageError = ""
        root.costsFile.reload()
      } else if (root.resetUsageError === "") {
        root.resetUsageError = "Reset failed."
      }
    }
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
    path: root.archiveDir + "/generations.json"
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

  // The generation the main view shows: its image, its actions, and — when
  // it is the newest — the reading last-run.json carries for it.
  readonly property var selectedReview: reviewForId(selectedReviewId)
  readonly property bool selectedIsNewest: selectedReviewId !== ""
    && reviewsIndex.length > 0 && reviewsIndex[0].id === selectedReviewId
  // The selected generation's user theme, when it has one and an image to
  // offer it as a preview — the condition for "Use as style preview".
  readonly property var selectedPreviewTheme: {
    var review = selectedReview
    if (!review || review.imagePath === "") return null
    var theme = Model.findStyle(styleCatalog, Model.reviewStyleKey(review, styleCatalog))
    return theme && theme.source === "user" ? theme : null
  }

  // The full-size background when the entry kept its theme snapshot, the
  // entry's page otherwise (older entries have no image of their own).
  function openSelectedImage() {
    var review = selectedReview
    if (!review) return
    if (review.imagePath !== "") Quickshell.execDetached(["xdg-open", review.imagePath])
    else openReview(review.path)
  }

  function useSelectedAsStylePreview() {
    if (selectedPreviewTheme) setStylePreview(selectedPreviewTheme.key, selectedReview.imagePath)
  }

  // The "Create a new style" expander. Open means a create or import flow is
  // showing; closing it drops a half-finished import result.
  function toggleStyleCreate() {
    if (styleCreateMode !== "") goBack()
    else openView("create")
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
  readonly property string setThemeBin: binDir + "/astro-arc-set-theme"
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

  // ---- Set Theme: put the selected generation's theme back on the desktop.
  // Neighbour to Save and Export, and the odd one out: those two make a copy
  // and leave the desktop alone, this one changes what you are looking at and
  // leaves nothing behind — the live astro-arc theme is rebuilt by the next
  // generation either way. It is the "I liked Tuesday's better" button, and
  // the reason it is safe to press is that Tuesday's is still in the archive
  // afterwards. ---------------------------------------------------------------
  property string _setThemeStdout: ""
  property string _setThemeStderr: ""

  function setSelectedTheme() {
    if (!root.selectedReviewId || setThemeProc.running) return
    root.themeActionStatus = ""
    root._setThemeStdout = ""
    root._setThemeStderr = ""
    setThemeProc.command = [root.setThemeBin, root.selectedReviewId]
    setThemeProc.running = true
  }

  Process {
    id: setThemeProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root._setThemeStdout = String(text || "").trim() }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root._setThemeStderr = String(text || "").trim() }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.themeActionStatus = "Desktop is now on “" + root._setThemeStdout
          + "”. The next generation replaces it."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = root._setThemeStderr || "Could not set that theme."
        root.themeActionStatusIsError = true
      }
    }
  }

  // ---- Export: package the selected generation as a shareable Omarchy theme
  // REPOSITORY, which is a different job from Save. Save installs a theme on
  // THIS machine; Omarchy distributes themes as git repositories
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

  // ---- Share Record: the selected generation as ONE self-contained .html —
  // image, reading, prompt and costs in a single file that opens anywhere.
  // Neighbour to Export and easy to confuse with it: Export packages the
  // THEME for a stranger to install and leaves the reading out,
  // astro-arc-share-record sends the RECORD to someone you chose, to read.
  // No name prompt here, unlike Save: the script opens a save dialog, so the
  // file gets named and filed in the same gesture that says where it goes.
  readonly property string shareRecordBin: binDir + "/astro-arc-share-record"
  property string _shareStdout: ""
  property string _shareStderr: ""

  function shareSelectedRecord() {
    if (root.selectedReviewId === "" || shareRecordProc.running) return
    root.themeActionStatus = ""
    root._shareStdout = ""
    root._shareStderr = ""
    shareRecordProc.command = [root.shareRecordBin, root.selectedReviewId]
    shareRecordProc.running = true
  }

  Process {
    id: shareRecordProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root._shareStdout = String(text || "").trim() }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root._shareStderr = String(text || "").trim() }
    onExited: function(exitCode) {
      // 2 is the save dialog being cancelled. Nothing was written and nothing
      // went wrong, so it must not come back red like a failure.
      if (exitCode === 2) {
        root.themeActionStatus = "Share cancelled."
        root.themeActionStatusIsError = false
      } else if (exitCode === 0) {
        root.themeActionStatus = "Saved to " + root._shareStdout
          + " — the image is inside the file, so it opens anywhere you send it."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = root._shareStderr || "Could not build the record."
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
        root.themeActionStatus = "History cleared."
        root.themeActionStatusIsError = false
      } else {
        root.themeActionStatus = "Clear failed."
        root.themeActionStatusIsError = true
      }
    }
  }

  // ---- Read by the section files, which can't see this file's ids. --------
  readonly property bool styleAgentRunning: styleAgentProc.running
  readonly property bool styleCopyRunning: styleCopyProc.running
  readonly property bool styleUndoRunning: styleUndoProc.running
  readonly property bool styleImportRunning: styleImportProc.running
  readonly property bool styleEstimateRunning: styleEstimateProc.running
  readonly property bool styleRenderRunning: styleRenderProc.running
  readonly property bool stylePreviewRunning: stylePreviewProc.running
  readonly property bool setThemeRunning: setThemeProc.running
  readonly property bool exportThemeRunning: exportThemeProc.running
  readonly property bool shareRecordRunning: shareRecordProc.running

  // Shared label-column width so every label-left/control-right row lines
  // up, the same way a settings form's labels would.
  // Wide enough for "Schedule", the longest of the row labels — a narrower
  // column let that label's text run past its own box and butt straight
  // into the control beside it.
  readonly property int labelColW: Style.space(64)

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
    // One height for every view: the main view's, capped by
    // fittedContentHeight at the screen height minus the bar (and the gap
    // and margin around the card). The main view is built from fixed-height
    // blocks, so browsing doesn't change it either; anything taller than
    // this scrolls inside its view.
    contentHeight: panel.fittedContentHeight(heroSection.implicitHeight + root.viewGap + root.mainViewHeight)

    // ---- The solar system behind every view (sections/OrbitBackground.qml).
    // Exactly one, beneath the views, pinned to the card inside its border:
    // switching views never touches it.
    OrbitBackground {
      x: -panel.padding
      y: -panel.padding
      width: panel.contentWidth - Border.left(panel.borderSpec) - Border.right(panel.borderSpec)
      height: panel.contentHeight - Border.top(panel.borderSpec) - Border.bottom(panel.borderSpec)
      radius: Math.max(0, Style.cornerRadius - Border.left(panel.borderSpec))
      unit: Style.spaceReal(1)
      sunX: panel.padding + heroSection.starCenter.x
      sunY: panel.padding + heroSection.starCenter.y
      active: root.opened
      animated: root.configState.animatedBackground
      accent: Color.accent
      foreground: root.bar.foreground
    }

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      NumberAnimation {
        id: viewFade
        targets: [heroSection, viewArea]
        property: "opacity"
        from: 0
        to: 1
        duration: 160
        easing.type: Easing.OutCubic
      }
      blocked: root.editingLocation
      onCloseRequested: root.navEscape()
      // Tab stays inside the panel now, cycling its controls (it used to
      // switch to the neighbouring bar panel).
      onTabRequested: function(direction) { root.navTab(direction) }
      onMoveRequested: function(dx, dy) { root.navMove(dx, dy) }

      // ---- Hero: ✦ · Astro-Arc · status, or back · the view's title ------
      HeroSection {
        id: heroSection
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        panel: root
      }

      // ---- The views, each filling the same area below the hero ----------
      Item {
        id: viewArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: heroSection.bottom
        anchors.topMargin: root.viewGap
        anchors.bottom: parent.bottom

        // ================== MAIN VIEW ==================
        Flickable {
          id: mainFlick
          anchors.fill: parent
          contentWidth: width
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          visible: root.view === "main"
          contentHeight: mainColumn.implicitHeight

          Column {
            id: mainColumn
            width: parent.width
            spacing: Style.space(14)

            // A hidden Column measures 0, so the height every view uses is
            // taken while this one is showing.
            function latch() { if (visible && implicitHeight > 0) root.mainViewHeight = implicitHeight }
            onImplicitHeightChanged: latch()
            onVisibleChanged: latch()
            Component.onCompleted: latch()

            TodaySection { panel: root }
            PanelSeparator { foreground: root.bar.foreground }
            StyleSection { id: styleSection; panel: root }
          }
        }

        // ================== SETTINGS ==================
        // Only what a reading needs. Everything else is in the Advanced
        // popout (sections/AdvancedSettingsWindow.qml).
        Flickable {
          id: settingsFlick
          anchors.fill: parent
          contentWidth: width
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          visible: root.view === "settings"
          contentHeight: settingsColumn.implicitHeight

          Column {
            id: settingsColumn
            width: parent.width
            spacing: Style.space(16)

            BirthLocationSection { id: birthLocationSection; panel: root }
            PanelSeparator { foreground: root.bar.foreground }
            ScheduleSection { id: scheduleSection; panel: root }
            PanelSeparator { foreground: root.bar.foreground }
            AdvancedLinkSection { panel: root }
          }
        }

        // ================== DRILL-INS ==================
        Flickable {
          id: tweakFlick
          anchors.fill: parent
          contentWidth: width
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          visible: root.view === "tweak"
          contentHeight: tweakView.implicitHeight
          TweakView { id: tweakView; panel: root }
        }

        Flickable {
          id: createFlick
          anchors.fill: parent
          contentWidth: width
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          visible: root.view === "create"
          contentHeight: createView.implicitHeight
          CreateView { id: createView; panel: root }
        }

        Flickable {
          id: fixFlick
          anchors.fill: parent
          contentWidth: width
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          visible: root.view === "fix"
          contentHeight: fixView.implicitHeight
          FixView { id: fixView; panel: root }
        }

        // Scrolls its own grid, so it takes the area as it is.
        BrowseView {
          id: browseView
          visible: root.view === "browse"
          panel: root
          availableHeight: viewArea.height
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

  // ---- Advanced settings popout (sections/AdvancedSettingsWindow.qml) ----
  AdvancedSettingsWindow { id: advancedWindow; panel: root }

}
