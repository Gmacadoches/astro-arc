import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: every style, starred first (decision record
// 2026-11-styles-owned), one at a time: its preview big, ‹ › or the strip
// to page through them, and under it what can be done to that one — use it,
// star it, hide it from the Style picker, change its preview, revert it to
// its starter version once changed, or delete it. Delete and revert change
// files, so each asks for its word to be typed first. It opens on the
// selected style. The title row is the hero's.
Column {
  id: view
  required property var panel

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color urgent: panel.bar.urgent || Color.urgent
  property bool showHidden: false
  readonly property int hiddenCount: panel.styleCatalog.filter(function(r) {
    return panel.hiddenStyles.indexOf(r.key) >= 0 && r.key !== panel.shownStyleKey
  }).length
  readonly property var rows: panel.sortedStyles.filter(function(r) {
    return view.showHidden || r.key === panel.shownStyleKey || panel.hiddenStyles.indexOf(r.key) < 0
  })

  // The style in focus, by key; where it was, so that when it goes (deleted,
  // or hidden with hidden ones not showing) its neighbour takes its place.
  property string focusKey: ""
  property int lastIndex: 0
  readonly property int index: {
    for (var i = 0; i < rows.length; i++) if (rows[i].key === focusKey) return i
    return rows.length > 0 ? Math.min(lastIndex, rows.length - 1) : -1
  }
  readonly property var style: index >= 0 ? rows[index] : null
  readonly property bool starred: style !== null && panel.favoriteStyles.indexOf(style.key) >= 0
  readonly property bool hidden: style !== null && panel.hiddenStyles.indexOf(style.key) >= 0
  readonly property bool selected: style !== null && style.key === panel.shownStyleKey
  readonly property bool isNew: style !== null && panel.stylesNewAtOpen.indexOf(style.key) >= 0
  readonly property bool confirmingDelete: style !== null && panel.styleDeleteKey === style.key
  readonly property bool confirmingRevert: style !== null && panel.styleRevertKey === style.key

  onIndexChanged: if (index >= 0) lastIndex = index

  // For the panel's arrows (Panel.navMove), as the main view's: with nothing
  // focused ←→ page, ↑ goes to the image and ↓ to Use this style (or the
  // first action that can be pressed); ↑↓ into the strip land on the one
  // showing.
  readonly property Item imageItem: frame
  function selectedThumb() { return strip.currentThumb() }
  function firstActionItem() {
    if (useBtn.enabled) return useBtn
    for (var i = 0; i < actions.children.length; i++) {
      var b = actions.children[i]
      if (b.visible && b.enabled) return b
    }
    return frame
  }

  // Enter, from the image or the strip: use the style showing.
  function useShowing() { if (useBtn.enabled) useBtn.clicked() }
  onVisibleChanged: if (visible) { focusKey = panel.shownStyleKey; showHidden = false }

  function show(i) {
    if (rows.length === 0) return
    i = (i + rows.length) % rows.length
    focusKey = rows[i].key
    // A half-typed confirmation was about the style left behind.
    panel.styleDeleteKey = ""
    panel.styleRevertKey = ""
  }

  width: parent.width
  spacing: Style.space(10)

  Text {
    textFormat: Text.PlainText
    visible: panel.stylesNewAtOpen.length > 0
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.stylesNewAtOpen.length + " new style" + (panel.stylesNewAtOpen.length === 1 ? "" : "s")
      + " arrived with this update, hidden until you show " + (panel.stylesNewAtOpen.length === 1 ? "it" : "them")
      + ". They're marked new under Show hidden."
    color: panel.bar.foreground
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  // ---- The style in focus, big, and the strip of all of them --------------
  Column {
    width: parent.width
    spacing: Style.space(6)

    PreviewFrame {
      id: frame
      panel: view.panel
      source: view.style && view.style.preview ? view.style.preview : ""
      version: panel.stylePreviewVersion
      caption: view.style ? view.style.label : ""
      emptyText: view.style ? "No preview yet. Click to pick one." : "No styles to show"
      index: view.index
      count: view.rows.length
      wraps: true
      opacity: view.hidden && !view.selected ? 0.6 : 1
      onPrevious: view.show(view.index - 1)
      onNext: view.show(view.index + 1)
      onEntered: view.useShowing()
      onActivated: if (view.style && !panel.stylePreviewRunning) panel.changeStylePreview(view.style.key)
    }

    PreviewStrip {
      id: strip
      panel: view.panel
      wraps: true
      sources: view.rows.map(function(r) { return r.preview || "" })
      tips: view.rows.map(function(r) { return r.label })
      version: panel.stylePreviewVersion
      current: view.index
      onPicked: function(i) { view.show(i) }
      onEntered: view.useShowing()
      onActivated: if (view.style && !panel.stylePreviewRunning) panel.changeStylePreview(view.style.key)
    }
  }

  // ---- Its name and what it is --------------------------------------------
  Column {
    width: parent.width
    visible: view.style !== null

    Text {
      textFormat: Text.PlainText
      width: parent.width
      elide: Text.ElideRight
      text: view.style ? view.style.label : ""
      color: panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }

    Text {
      textFormat: Text.PlainText
      width: parent.width
      elide: Text.ElideRight
      visible: text !== ""
      text: !view.style ? "" : [view.selected ? "selected" : "", view.starred ? "starred" : "",
             view.isNew ? "new" : "", view.hidden && !view.selected ? "hidden" : "",
             view.style.changed ? "changed" : "", view.style.valid ? "" : "failed validation"]
        .filter(function(s) { return s !== "" }).join(" · ")
      color: view.style && !view.style.valid ? view.urgent : view.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // ---- What can be done to it ---------------------------------------------
  // The primary action, filled with the accent like Apply theme. A style
  // that failed validation goes to its Fix view instead.
  PanelButton {
    id: useBtn
    width: parent.width
    visible: view.style !== null
    text: !view.style ? "" : !view.style.valid ? "Fix…" : view.selected ? "Selected ✓" : "Use this style"
    fontSize: Style.font.caption
    foreground: panel.bar.background
    color: hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent
    enabled: view.style !== null && (!view.selected || !view.style.valid)
    opacity: enabled ? 1 : 0.45
    onClicked: panel.pickArtStyle(view.style.key)
  }

  Grid {
    id: actions
    width: parent.width
    visible: view.style !== null
    columns: 3
    spacing: Style.space(8)
    readonly property real cell: (width - spacing * (columns - 1)) / columns

    PanelButton {
      width: actions.cell
      text: view.starred ? "󰓎 Unstar" : "󰓒 Star"
      bordered: true
      fontSize: Style.font.caption
      foreground: view.starred ? Color.accent : panel.bar.foreground
      tooltipText: view.starred ? "Stop listing it first" : "List it first"
      onClicked: panel.toggleFavoriteStyle(view.style.key)
    }

    PanelButton {
      width: actions.cell
      text: view.hidden ? "󰈉 Show" : "󰈈 Hide"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !view.selected
      opacity: enabled ? 1 : 0.45
      tooltipText: view.selected ? "The selected style can't be hidden"
        : (view.hidden ? "Show it in the Style picker" : "Hide it from the Style picker")
      onClicked: panel.toggleHiddenStyle(view.style.key)
    }

    PanelButton {
      width: actions.cell
      text: panel.stylePreviewRunning ? "Picking…" : "Preview…"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.stylePreviewRunning
      opacity: enabled ? 1 : 0.45
      tooltipText: "Change its preview image"
      onClicked: panel.changeStylePreview(view.style.key)
    }

    PanelButton {
      width: actions.cell
      visible: view.style !== null && view.style.starter && view.style.changed
      text: "󰑓 Revert…"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleManageRunning
      opacity: enabled ? 1 : 0.45
      tooltipText: "Revert to the starter version"
      onClicked: { panel.styleDeleteKey = ""; panel.styleRevertKey = view.confirmingRevert ? "" : view.style.key }
    }

    PanelButton {
      width: actions.cell
      text: "󰆴 Delete…"
      bordered: true
      fontSize: Style.font.caption
      foreground: view.confirmingDelete ? view.urgent : panel.bar.foreground
      enabled: !view.selected && !panel.styleManageRunning
      opacity: enabled ? 1 : 0.45
      tooltipText: view.selected ? "The selected style can't be deleted" : "Delete it"
      onClicked: { panel.styleRevertKey = ""; panel.styleDeleteKey = view.confirmingDelete ? "" : view.style.key }
    }
  }

  // Typed confirmation: delete and revert change files.
  Column {
    id: confirm
    visible: view.confirmingDelete || view.confirmingRevert
    width: parent.width
    spacing: Style.space(6)
    readonly property string word: view.confirmingDelete ? "delete" : "revert"

    Text {
      textFormat: Text.PlainText
      width: parent.width
      wrapMode: Text.WordWrap
      text: !view.style ? "" : view.confirmingDelete
        ? "Deletes “" + view.style.label + "” and its tweak backup for good"
          + (view.style.starter ? "; updates won't bring it back (Restore deleted starters, below, does)."
                                : ". It is your own style, so nothing can bring it back.")
          + " Type delete to confirm."
        : "Replaces your “" + view.style.label + "” with the version that ships, and it gets updates again. "
          + "Yours is saved first, so Undo last tweak can put it back. Type revert to confirm."
      color: view.confirmingDelete ? view.urgent : panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Item {
      width: parent.width
      height: Math.max(confirmField.implicitHeight, confirmBtn.implicitHeight)

      ClipboardSafeField {
        id: confirmField
        anchors.left: parent.left
        anchors.right: confirmBtn.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        maximumLength: 20
        placeholderText: confirm.word
        foreground: panel.bar.foreground
        font.family: panel.bar.fontFamily
        onVisibleChanged: { text = ""; if (visible) forceActiveFocus() }
        Keys.onReturnPressed: confirmBtn.go()
        Keys.onEnterPressed: confirmBtn.go()
      }

      PanelButton {
        id: confirmBtn
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: view.confirmingDelete ? "Delete" : "Revert"
        bordered: true
        fontSize: Style.font.caption
        foreground: view.confirmingDelete ? view.urgent : panel.bar.foreground
        enabled: confirmField.text.trim().toLowerCase() === confirm.word && !panel.styleManageRunning
        opacity: enabled ? 1 : 0.45
        function go() {
          if (!enabled) return
          if (view.confirmingDelete) panel.deleteStyle(view.style.key)
          else panel.revertStyle(view.style.key)
        }
        onClicked: go()
      }
    }
  }

  Text {
    textFormat: Text.PlainText
    visible: panel.styleActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleActionStatus
    color: panel.styleActionStatusIsError ? view.urgent : view.dim
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Item {
    width: parent.width
    height: Math.max(hiddenBtn.implicitHeight, restoreBtn.implicitHeight)

    PanelButton {
      id: hiddenBtn
      anchors.left: parent.left
      width: (parent.width - Style.space(8)) / 2
      text: (view.showHidden ? "Hide hidden" : "Show hidden") + " (" + view.hiddenCount + ")"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: view.hiddenCount > 0 || view.showHidden
      opacity: enabled ? 1 : 0.45
      onClicked: view.showHidden = !view.showHidden
    }

    PanelButton {
      id: restoreBtn
      anchors.right: parent.right
      width: hiddenBtn.width
      text: panel.styleManageRunning ? "Working…" : "Restore deleted starters"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleManageRunning
      opacity: enabled ? 1 : 0.45
      tooltipText: "Bring back starter styles you deleted"
      onClicked: panel.restoreDeletedStyles()
    }
  }

  InfoNote {
    width: parent.width
    foreground: panel.bar.foreground
    fontFamily: panel.bar.fontFamily
    text: "Every style is a file of your own in ~/.config/astro-arc/styles/. Ones that came with Astro-Arc "
      + "get its updates until you change them; Revert brings the update back."
  }
}
