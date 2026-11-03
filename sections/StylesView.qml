import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: every style, starred first (decision record
// 2026-11-styles-owned). Each one can be starred, hidden from the Style
// picker, reverted to its starter version once changed, or deleted. Delete
// and revert change files, so each asks for its word to be typed first.
// Picking a row's name makes it the style. The title row is the hero's.
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

  PanelButton {
    text: (view.showHidden ? "Hide hidden" : "Show hidden") + " (" + view.hiddenCount + ")"
    bordered: true
    fontSize: Style.font.caption
    foreground: panel.bar.foreground
    enabled: view.hiddenCount > 0 || view.showHidden
    opacity: enabled ? 1 : 0.45
    onClicked: view.showHidden = !view.showHidden
  }

  Repeater {
    model: view.rows

    Column {
      id: entry
      required property var modelData
      readonly property var style: modelData
      readonly property bool starred: panel.favoriteStyles.indexOf(style.key) >= 0
      readonly property bool hidden: panel.hiddenStyles.indexOf(style.key) >= 0
      readonly property bool selected: style.key === panel.shownStyleKey
      readonly property bool isNew: panel.stylesNewAtOpen.indexOf(style.key) >= 0
      readonly property bool confirmingDelete: panel.styleDeleteKey === style.key
      readonly property bool confirmingRevert: panel.styleRevertKey === style.key

      width: view.width
      spacing: Style.space(6)

      Item {
        width: parent.width
        height: Math.max(thumb.height, buttons.height)
        opacity: entry.hidden && !entry.selected ? 0.6 : 1

        Rectangle {
          id: thumb
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(56)
          height: Style.space(36)
          radius: Style.cornerRadius
          color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
          border.width: entry.selected ? 2 : 1
          border.color: entry.selected ? Color.accent
            : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)
          clip: true

          Image {
            anchors.fill: parent
            anchors.margins: 1
            visible: status === Image.Ready
            source: entry.style.preview ? "file://" + entry.style.preview + "?v=" + panel.stylePreviewVersion : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: false
            sourceSize.width: 120
          }
        }

        // The name: picking it makes it the style.
        Item {
          id: name
          anchors.left: thumb.right
          anchors.leftMargin: Style.space(10)
          anchors.right: buttons.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          height: nameColumn.implicitHeight
          activeFocusOnTab: !entry.selected
          Keys.onReturnPressed: panel.pickArtStyle(entry.style.key)
          Keys.onEnterPressed: panel.pickArtStyle(entry.style.key)
          Keys.onSpacePressed: panel.pickArtStyle(entry.style.key)

          Column {
            id: nameColumn
            width: parent.width

            Text {
              textFormat: Text.PlainText
              width: parent.width
              elide: Text.ElideRight
              text: entry.style.label
              color: panel.bar.foreground
              font.family: panel.bar.fontFamily
              font.pixelSize: Style.font.body
              font.underline: name.activeFocus || nameMouse.containsMouse
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              elide: Text.ElideRight
              visible: text !== ""
              text: [entry.selected ? "selected" : "", entry.isNew ? "new" : "",
                     entry.hidden && !entry.selected ? "hidden" : "",
                     entry.style.changed ? "changed" : "",
                     entry.style.valid ? "" : "failed validation"]
                .filter(function(s) { return s !== "" }).join(" · ")
              color: entry.style.valid ? view.dim : view.urgent
              font.family: panel.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          MouseArea {
            id: nameMouse
            anchors.fill: parent
            enabled: !entry.selected
            hoverEnabled: true
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: panel.pickArtStyle(entry.style.key)
          }
        }

        Row {
          id: buttons
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(4)

          PanelIconButton {
            iconText: entry.starred ? "󰓎" : "󰓒"
            focusable: true
            fontSize: Style.font.body
            foreground: entry.starred ? Color.accent : panel.bar.foreground
            tooltipText: entry.starred ? "Unstar" : "Star: list it first"
            onClicked: panel.toggleFavoriteStyle(entry.style.key)
          }

          PanelIconButton {
            iconText: entry.hidden ? "󰈉" : "󰈈"
            focusable: true
            enabled: !entry.selected
            opacity: enabled ? 1 : 0.35
            fontSize: Style.font.body
            foreground: panel.bar.foreground
            tooltipText: entry.selected ? "The selected style can't be hidden"
              : (entry.hidden ? "Show it in the Style picker" : "Hide it from the Style picker")
            onClicked: panel.toggleHiddenStyle(entry.style.key)
          }

          PanelIconButton {
            visible: entry.style.starter && entry.style.changed
            iconText: "󰑓"
            focusable: true
            enabled: !panel.styleManageRunning
            fontSize: Style.font.body
            foreground: panel.bar.foreground
            tooltipText: "Revert to the starter version"
            onClicked: { panel.styleDeleteKey = ""; panel.styleRevertKey = entry.confirmingRevert ? "" : entry.style.key }
          }

          PanelIconButton {
            iconText: "󰆴"
            focusable: true
            enabled: !entry.selected && !panel.styleManageRunning
            opacity: enabled ? 1 : 0.35
            fontSize: Style.font.body
            foreground: entry.confirmingDelete ? view.urgent : panel.bar.foreground
            tooltipText: entry.selected ? "The selected style can't be deleted" : "Delete…"
            onClicked: { panel.styleRevertKey = ""; panel.styleDeleteKey = entry.confirmingDelete ? "" : entry.style.key }
          }
        }
      }

      // Typed confirmation: delete and revert change files.
      Column {
        id: confirm
        visible: entry.confirmingDelete || entry.confirmingRevert
        width: parent.width
        spacing: Style.space(6)
        readonly property string word: entry.confirmingDelete ? "delete" : "revert"

        Text {
          textFormat: Text.PlainText
          width: parent.width
          wrapMode: Text.WordWrap
          text: entry.confirmingDelete
            ? "Deletes “" + entry.style.label + "” and its tweak backup for good"
              + (entry.style.starter ? "; updates won't bring it back (Restore deleted starter styles, below, does)."
                                     : ". It is your own style, so nothing can bring it back.")
              + " Type delete to confirm."
            : "Replaces your “" + entry.style.label + "” with the version that ships, and it gets updates again. "
              + "Yours is saved first, so Undo last tweak can put it back. Type revert to confirm."
          color: entry.confirmingDelete ? view.urgent : panel.bar.foreground
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
            text: entry.confirmingDelete ? "Delete" : "Revert"
            bordered: true
            fontSize: Style.font.caption
            foreground: entry.confirmingDelete ? view.urgent : panel.bar.foreground
            enabled: confirmField.text.trim().toLowerCase() === confirm.word && !panel.styleManageRunning
            opacity: enabled ? 1 : 0.45
            function go() {
              if (!enabled) return
              if (entry.confirmingDelete) panel.deleteStyle(entry.style.key)
              else panel.revertStyle(entry.style.key)
            }
            onClicked: go()
          }
        }
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

  PanelButton {
    text: panel.styleManageRunning ? "Working…" : "Restore deleted starter styles"
    bordered: true
    fontSize: Style.font.caption
    foreground: panel.bar.foreground
    enabled: !panel.styleManageRunning
    onClicked: panel.restoreDeletedStyles()
  }

  InfoNote {
    width: parent.width
    foreground: panel.bar.foreground
    fontFamily: panel.bar.fontFamily
    text: "Every style is a file of your own in ~/.config/astro-arc/styles/. Ones that came with Astro-Arc "
      + "get its updates until you change them; Revert brings the update back."
  }
}
