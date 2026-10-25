import QtQuick
import Qt.labs.folderlistmodel
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import Quickshell.Services.UPower
import qs.Commons

// The moving background. When the wallpaper on screen has a depth map (see
// backend/bin/astro-arc-motion), this draws it again on a layer of its own,
// just above Omarchy's background layer, with shaders/parallax.frag shifting
// near things against far ones. Omarchy's still stays underneath the whole
// time, so if anything here fails or is switched off the desktop is the same
// picture, standing still.
//
// What it costs is not the shader (under 0.2 ms a frame at 4K on a desktop
// GPU) but the compositor: every frame damages the whole screen, so Hyprland
// recomposites it and re-blurs anything translucent on top. Hence a low frame
// rate (10 fps; at 8 px/s that is under a pixel a frame, and the filtered
// sampling hides the steps), and pausing whenever nobody can see it or it
// should not move:
//   - motion is off in the panel, or the wallpaper has no depth map: no layer
//   - a DisplayLink monitor: no layer. It has no GPU of its own; every changed
//     frame is compressed on the CPU and sent over USB, which on a 1024x600
//     screen measured 0.1% of a core frozen against 5 to 6% moving.
//     motionOutputs in the settings file overrides this per monitor.
//   - Omarchy's no-animations mode (Style.reduceMotion): no layer
//   - covered, per monitor: any tiled or fullscreen window on the active
//     workspace, a special workspace shown over it, or floating windows over
//     more than half the screen. Moving under an opaque window is wasted work,
//     and under a translucent one it costs a re-blur every frame and makes the
//     text on top harder to read.
//   - a game running anywhere, on every monitor: a fullscreen window on any
//     monitor's active workspace, or any window Steam launched (class
//     steam_app_*, or SteamGameId in its environment), windowed or not. The compositor draws every monitor on one GPU,
//     usually the one the game runs on, so any monitor still moving costs
//     the game a little.
//   - on battery or the power-saver profile: frozen
//   - no input for two minutes (idle inhibitors respected): frozen
//   - locked or the screen off: Hyprland stops sending frame callbacks to
//     anything under a session lock or on a blanked output, so Qt stops
//     drawing this layer by itself; the lock screen draws its own still.
//     A monitor Hyprland reports as DPMS off also counts as covered.
// Frozen keeps the current frame on screen and damages nothing. Coming back,
// the drift eases up to speed over a second instead of starting at full pace.
Item {
  id: root

  // Where things are: astro-arc-paths.sh's locations, which QML cannot source.
  readonly property string home: Quickshell.env("HOME")
  readonly property string configPath: home + "/.local/state/omarchy/settings/astro-arc.json"
  readonly property string omarchyCurrentDir: home + "/.local/state/omarchy/current"
  readonly property string pluginRoot: {
    var u = String(Qt.resolvedUrl("."))
    if (u.indexOf("file://") === 0) u = u.substring(7)
    return u.replace(/\/+$/, "")
  }
  readonly property string motionBin: pluginRoot + "/backend/bin/astro-arc-motion"

  // The speed the user picked from the prototype's ladder: the nearest things
  // peak at about 8 px/s on a 1080-line screen. focusDepth is 0, so the far
  // background stays put and the middle moves at about half that.
  readonly property real parallaxPx: 22
  readonly property real parallaxPeriod: 15
  readonly property int rampMs: 3000
  readonly property int resumeMs: 1000
  // motionIdleSeconds in the settings file changes it; 0 turns the idle
  // freeze off.
  property int idleSeconds: 120

  // 10 unless the settings file carries motionFps (4 to 30), which exists for
  // measuring; the panel does not offer it.
  property int fps: 10

  property bool enabled: false
  property string imagePath: ""
  property string depthPath: ""

  // The bar builds this widget once per monitor, so each copy draws the
  // monitor its own bar is on, and three monitors get three layers, not nine.
  readonly property var hostScreen: root.QsWindow && root.QsWindow.window ? root.QsWindow.window.screen : null

  // motionOutputs in the settings file maps connector names to true or false,
  // e.g. {"DVI-I-1": true}, overriding the default: on everywhere except on
  // DisplayLink (evdi) outputs. Until the driver is known the monitor waits,
  // so a DisplayLink screen never gets a layer for the length of the check.
  property var outputOverrides: ({})
  property string outputDriver: ""
  property bool outputProbed: false
  readonly property bool outputAllowed: {
    if (!hostScreen || !outputProbed) return false
    var choice = outputOverrides[hostScreen.name]
    if (choice === true || choice === false) return choice
    return outputDriver !== "evdi"
  }

  readonly property bool active: enabled && depthPath !== "" && !Style.reduceMotion && hostScreen !== null && outputAllowed
  readonly property bool globallyFrozen: gameRunning
    || UPower.onBattery
    || PowerProfiles.profile === PowerProfile.PowerSaver
    || idle.isIdle

  // Whether this monitor's wallpaper is hidden, and whether a game is running
  // anywhere, from the last layout check.
  property bool covered: false
  property bool gameRunning: false

  IdleMonitor {
    id: idle
    enabled: root.active && root.idleSeconds > 0
    timeout: Math.max(1, root.idleSeconds)
    respectInhibitors: true
  }

  // Settings live in the same file the panel edits; only an explicit true
  // turns motion on, so it is off until someone asks for it.
  FileView {
    path: root.configPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var settings = JSON.parse(text() || "{}")
        root.enabled = settings.motion === true
        var fps = Number(settings.motionFps)
        root.fps = fps >= 4 && fps <= 30 ? Math.round(fps) : 10
        var idleSeconds = Number(settings.motionIdleSeconds)
        root.idleSeconds = settings.motionIdleSeconds !== undefined && idleSeconds >= 0 ? Math.round(idleSeconds) : 120
        var outputs = settings.motionOutputs
        root.outputOverrides = outputs && typeof outputs === "object" && !Array.isArray(outputs) ? outputs : {}
      } catch (e) {
        root.enabled = false
      }
    }
    onLoadFailed: root.enabled = false
  }

  function refresh() {
    if (!root.enabled) {
      root.imagePath = ""
      root.depthPath = ""
      return
    }
    if (lookup.running) {
      lookup.again = true
      return
    }
    lookup.running = true
  }

  onEnabledChanged: refresh()

  // Omarchy repoints current/background (a symlink) on every wallpaper or
  // theme change. A directory model is the one watcher here that sees the
  // link being replaced, so it stands in for an inotify watch on the folder.
  FolderListModel {
    folder: "file://" + root.omarchyCurrentDir
    showHidden: true
    showDirs: true
    onStatusChanged: if (status === FolderListModel.Ready) root.refresh()
  }

  Process {
    id: lookup
    property bool again: false
    command: [root.motionBin, "lookup"]
    stdout: StdioCollector {
      onStreamFinished: {
        var parts = String(text || "").replace(/\n+$/, "").split("\t")
        var image = parts[0] || ""
        var depth = parts.length > 1 ? parts[1] : ""
        // A new path reloads its Image, which drops the layer to transparent
        // until both textures are in, so old and new never mix on screen.
        root.imagePath = image
        root.depthPath = depth
        // A wallpaper with no depth map yet (set by hand, or an older
        // generation put back with Set wallpaper): make one here, then look
        // again. A failed map does not look again, so it cannot loop.
        if (root.enabled && image !== "" && depth === "" && !autoDepth.running) {
          autoDepth.command = [root.motionBin, "depth", image]
          autoDepth.running = true
        }
      }
    }
    onExited: {
      if (again) {
        again = false
        running = true
      }
    }
  }

  // Makes the depth map the lookup above found missing.
  Process {
    id: autoDepth
    onExited: function(exitCode) { if (exitCode === 0) root.refresh() }
  }

  // The layout check. Hyprland announces every change that can uncover or
  // cover the wallpaper; each one, coalesced over 150 ms, asks hyprctl for the
  // monitors and windows once. Nothing polls. Dragging a floating window sends
  // no event, so its new place counts from the next one.
  readonly property var layoutEvents: ["openwindow", "closewindow", "movewindow", "movewindowv2",
    "workspace", "workspacev2", "changefloatingmode", "fullscreen", "activespecial", "activespecialv2",
    "moveworkspace", "moveworkspacev2", "focusedmon", "focusedmonv2", "monitoradded", "monitoraddedv2",
    "monitorremoved", "monitorremovedv2", "pin", "minimized"]

  Connections {
    target: Hyprland
    enabled: root.active
    function onRawEvent(event) {
      if (root.layoutEvents.indexOf(event.name) !== -1) layoutDebounce.restart()
    }
  }

  onActiveChanged: if (active) layoutDebounce.restart()
  onHostScreenChanged: {
    layoutDebounce.restart()
    probeOutput()
  }
  Component.onCompleted: probeOutput()

  // Which kernel driver runs this monitor's connector: the connector's sysfs
  // node sits inside its card's, and the card links to its driver.
  function probeOutput() {
    if (!root.hostScreen) return
    root.outputProbed = false
    outputProbe.command = ["bash", "-c",
      "for c in /sys/class/drm/card*-\"$1\"; do [[ -e $c ]] || continue; r=$(readlink -f \"$c\"); " +
      "basename \"$(readlink -f \"${r%/*}/device/driver\")\"; break; done", "probe", root.hostScreen.name]
    outputProbe.running = true
  }

  Process {
    id: outputProbe
    stdout: StdioCollector {
      onStreamFinished: {
        root.outputDriver = String(text || "").trim()
        root.outputProbed = true
      }
    }
  }

  Timer {
    id: layoutDebounce
    interval: 150
    onTriggered: {
      if (!root.active) return
      if (layoutQuery.running) layoutQuery.again = true
      else layoutQuery.running = true
    }
  }

  Process {
    id: layoutQuery
    property bool again: false
    // Third part: the pids of windows Steam launched. Native Linux games keep
    // their own window class, so the class alone misses them, but everything
    // Steam starts carries SteamGameId in its environment.
    command: ["bash", "-c", "hyprctl -j monitors; printf '\\n\\036\\n'; c=$(hyprctl -j clients); printf '%s' \"$c\"; " +
      "printf '\\n\\036\\n'; for p in $(grep -o '\"pid\": [0-9]*' <<<\"$c\" | grep -o '[0-9]*$'); do " +
      "grep -qaz '^SteamGameId=' /proc/$p/environ 2>/dev/null && echo $p; done"]
    stdout: StdioCollector {
      onStreamFinished: {
        var parts = String(text || "").split("\n\u001e\n")
        try {
          var monitors = JSON.parse(parts[0])
          var clients = JSON.parse(parts[1])
          root.covered = root.isCovered(monitors, clients)
          root.gameRunning = root.isGameRunning(monitors, clients, String(parts[2] || "").split("\n"))
        } catch (e) {
          root.covered = false
          root.gameRunning = false
        }
      }
    }
    onExited: {
      if (again) {
        again = false
        running = true
      }
    }
  }

  function isCovered(monitors, clients) {
    if (!root.hostScreen) return false
    var m = null
    for (var i = 0; i < monitors.length; i++) {
      if (monitors[i].name === root.hostScreen.name) m = monitors[i]
    }
    if (!m) return false
    if (m.dpmsStatus === false) return true
    if (m.specialWorkspace && m.specialWorkspace.id !== 0) return true

    var w = m.width / m.scale
    var h = m.height / m.scale
    if (m.transform % 2 === 1) {
      var t = w; w = h; h = t
    }

    var floats = []
    for (var j = 0; j < clients.length; j++) {
      var c = clients[j]
      if (c.mapped === false || c.hidden === true) continue
      var here = c.workspace && c.workspace.id === m.activeWorkspace.id
      if (!here && !(c.pinned && c.monitor === m.id)) continue
      if (c.fullscreen > 0 || !c.floating) return true
      floats.push(c)
    }
    if (floats.length === 0) return false

    // Floating windows overlap, so sample a grid instead of adding areas.
    var cols = 48, rows = 27, hidden = 0
    for (var r = 0; r < rows; r++) {
      for (var k = 0; k < cols; k++) {
        var px = m.x + (k + 0.5) * w / cols
        var py = m.y + (r + 0.5) * h / rows
        for (var f = 0; f < floats.length; f++) {
          var fc = floats[f]
          if (px >= fc.at[0] && px < fc.at[0] + fc.size[0] && py >= fc.at[1] && py < fc.at[1] + fc.size[1]) {
            hidden++
            break
          }
        }
      }
    }
    return hidden > cols * rows / 2
  }

  // Hyprland's fullscreen field is a bit set: 1 maximized, 2 fullscreen. Only
  // real fullscreen counts here, so a maximized music player is not a game.
  function isGameRunning(monitors, clients, steamPids) {
    var shown = {}
    for (var i = 0; i < monitors.length; i++) {
      shown[monitors[i].activeWorkspace.id] = true
      if (monitors[i].specialWorkspace && monitors[i].specialWorkspace.id !== 0) shown[monitors[i].specialWorkspace.id] = true
    }
    for (var j = 0; j < clients.length; j++) {
      var c = clients[j]
      if (c.mapped === false) continue
      if (String(c.class || "").indexOf("steam_app_") === 0) return true
      if (steamPids.indexOf(String(c.pid)) !== -1) return true
      if ((c.fullscreen & 2) && c.workspace && shown[c.workspace.id]) return true
    }
    return false
  }

  LazyLoader {
    active: root.active

    PanelWindow {
      id: panel

      screen: root.hostScreen
      anchors { top: true; bottom: true; left: true; right: true }
      color: "transparent"
      exclusionMode: ExclusionMode.Ignore
      // Bottom, not Background: it has to sit above Omarchy's own background
      // layer, and within one layer the stacking follows creation order,
      // which a monitor being plugged in can shuffle. Nothing else uses
      // Bottom, and windows still stack above it.
      WlrLayershell.layer: WlrLayer.Bottom
      WlrLayershell.namespace: "astro-arc-motion"
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      // Clicks fall through to Omarchy's background (its double-click picker).
      mask: Region {}

      readonly property bool frozen: root.globallyFrozen || root.covered
      readonly property bool ready: still.status === Image.Ready && depthImage.status === Image.Ready

      readonly property int decodeWidth: width > 0 ? Math.ceil(width * screen.devicePixelRatio) : 0
      readonly property int decodeHeight: height > 0 ? Math.ceil(height * screen.devicePixelRatio) : 0

      // 0 at first, so the opening frame matches the still underneath, then
      // eased up to full so the zoom and the drift arrive together.
      property real strength: 0
      property real time: 0
      // How much of a tick reaches the clock: 0 frozen, eased back to 1.
      property real pace: 1

      NumberAnimation {
        id: resume
        target: panel
        property: "pace"
        from: 0
        to: 1
        duration: root.resumeMs
        easing.type: Easing.InOutSine
      }

      onFrozenChanged: {
        if (frozen) {
          resume.stop()
          pace = 0
        } else {
          resume.restart()
        }
      }

      NumberAnimation {
        id: ramp
        target: panel
        property: "strength"
        from: 0
        to: 1
        duration: root.rampMs
        easing.type: Easing.InOutSine
      }

      onReadyChanged: {
        if (ready) {
          strength = 0
          ramp.restart()
        } else {
          ramp.stop()
          strength = 0
        }
      }

      Timer {
        interval: Math.round(1000 / root.fps)
        repeat: true
        running: panel.ready && !panel.frozen
        onTriggered: panel.time += panel.pace * interval / 1000
      }

      Image {
        id: still
        visible: false
        // The depth map's name is the image's hash. Carrying it in the URL
        // reloads the still when a theme switch rewrites the same path
        // (current/theme/backgrounds/background.png) with a new picture.
        source: root.imagePath && root.depthPath && panel.decodeWidth > 0
          ? "file://" + root.imagePath + "?" + root.depthPath.split("/").pop() : ""
        sourceSize.width: panel.decodeWidth
        sourceSize.height: panel.decodeHeight
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        smooth: true
      }

      // Half the screen's size each way, a quarter of its pixels: the map is
      // blurred to begin with, so the shader cannot tell, and the texture it
      // reads every frame shrinks. Never above the 1280 px the maps are made
      // at (depth_map.py MAX_WIDTH), so a 4K screen keeps the map as it is.
      Image {
        id: depthImage
        visible: false
        source: root.depthPath ? "file://" + root.depthPath : ""
        sourceSize.width: Math.min(1280, Math.ceil(panel.decodeWidth / 2))
        sourceSize.height: Math.min(720, Math.ceil(panel.decodeHeight / 2))
        asynchronous: true
        cache: false
        smooth: true
      }

      ShaderEffect {
        anchors.fill: parent
        visible: panel.ready
        fragmentShader: Qt.resolvedUrl("shaders/parallax.frag.qsb")

        property var source: still
        property var depth: depthImage
        property real time: panel.time
        property real screenAspect: panel.width / Math.max(panel.height, 1)
        property real imageAspect: still.implicitWidth / Math.max(still.implicitHeight, 1)
        property real parallaxPx: root.parallaxPx * panel.strength
        property real parallaxPeriod: root.parallaxPeriod
        // Enough zoom that the largest shift never shows an edge: the x reach
        // in full, the y reach at the path's 0.6, plus 2 px for filtering.
        property real overscan: panel.strength * Math.max((0.6 * root.parallaxPx + 2) / 1080,
                                                          (root.parallaxPx + 2) / (1080 * screenAspect))
      }
    }
  }
}
