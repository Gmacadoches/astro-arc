import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: the whole history as a grid, filterable by style. Picking an
// entry shows it in the main view (and goes back there). The footer keeps
// what History used to: the size on disk, the gallery page in the browser
// (the old Browse all), and Clean up, which opens the Advanced popout where
// Clear History lives. Fills the view's height: the grid scrolls inside it.
Item {
  id: view
  required property var panel
  // The grid scrolls itself, so the view takes the height it is given.
  property real availableHeight: 0

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  property string filter: ""
  // Styles in the history, most-used first, for the chips.
  readonly property var styleNames: {
    var counts = {}
    for (var i = 0; i < panel.reviewsIndex.length; i++) {
      var s = panel.reviewStyleLabel(panel.reviewsIndex[i])
      if (s !== "") counts[s] = (counts[s] || 0) + 1
    }
    return Object.keys(counts).sort(function(a, b) { return counts[b] - counts[a] || a.localeCompare(b) })
  }
  readonly property var shown: filter === "" ? panel.reviewsIndex
    : panel.reviewsIndex.filter(function(r) { return panel.reviewStyleLabel(r) === view.filter })
  readonly property real totalBytes: {
    var t = 0
    for (var i = 0; i < panel.reviewsIndex.length; i++) t += panel.reviewsIndex[i].sizeBytes || 0
    return t
  }

  width: parent.width
  implicitHeight: availableHeight
  height: availableHeight

  // The filter forgets itself when the view closes.
  onVisibleChanged: if (!visible) filter = ""

  // ---- Filter chips: All, then each style. Scrolls sideways. -------------
  Flickable {
    id: chips
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: chipRow.height
    contentWidth: chipRow.width
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.HorizontalFlick

    Row {
      id: chipRow
      spacing: Style.space(6)

      Repeater {
        model: [""].concat(view.styleNames)

        PanelButton {
          required property string modelData
          readonly property bool on: view.filter === modelData
          text: modelData === "" ? "All" : modelData
          bordered: true
          fontSize: Style.font.caption
          foreground: on ? panel.bar.background : panel.bar.foreground
          color: on ? (hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent)
            : (hot || activeFocus ? Style.hoverFillFor(panel.bar.foreground, Color.accent) : "transparent")
          onClicked: view.filter = modelData
          // Keep a chip reached by keyboard in view, clear of the ‹ › that
          // cover the row's ends while there is more to scroll.
          onActiveFocusChanged: if (activeFocus) {
            var edge = chipNav.width + Style.space(4)
            if (x < chips.contentX + edge) chips.scrollTo(x - edge)
            else if (x + width > chips.contentX + chips.width - edge) chips.scrollTo(x + width - chips.width + edge)
          }
        }
      }
    }

    readonly property real maxX: Math.max(0, contentWidth - width)
    readonly property bool moreLeft: contentX > 1
    readonly property bool moreRight: contentX < maxX - 1

    function scrollTo(x) {
      chipScroll.stop()
      chipScroll.to = Math.max(0, Math.min(maxX, x))
      chipScroll.start()
    }

    NumberAnimation on contentX { id: chipScroll; running: false; duration: 180; easing.type: Easing.OutCubic }
  }

  // ‹ and › over the row's ends, each shown only while chips are hidden on
  // that side, for whoever isn't using the keyboard (where ←→ already reach
  // every chip). A solid patch in the card's colour, fading into the row,
  // keeps half-hidden chips from showing through. Not in the focus order:
  // they only scroll, and the chips themselves are reachable.
  component ChipNav: Item {
    id: nav
    required property bool forward
    readonly property bool shown: forward ? chips.moreRight : chips.moreLeft
    width: navButton.width + Style.space(14)
    height: chips.height
    anchors.top: chips.top
    visible: opacity > 0
    opacity: shown ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 120 } }

    Rectangle {
      anchors.fill: parent
      gradient: Gradient {
        orientation: Gradient.Horizontal
        GradientStop { position: 0.0; color: nav.forward ? "transparent" : Color.popups.background }
        GradientStop { position: 0.4; color: Color.popups.background }
        GradientStop { position: 0.6; color: Color.popups.background }
        GradientStop { position: 1.0; color: nav.forward ? Color.popups.background : "transparent" }
      }
    }

    // Swallow clicks on the fade so they don't land on a half-hidden chip.
    MouseArea { anchors.fill: parent; enabled: nav.shown }

    PanelIconButton {
      id: navButton
      anchors.verticalCenter: parent.verticalCenter
      anchors.left: nav.forward ? undefined : parent.left
      anchors.right: nav.forward ? parent.right : undefined
      size: chips.height
      iconText: nav.forward ? "󰅂" : "󰅁"
      bordered: true
      fontSize: Style.font.body
      foreground: view.panel.bar.foreground
      tooltipText: nav.forward ? "More styles" : "Back"
      onClicked: chips.scrollTo(chips.contentX + (nav.forward ? 1 : -1) * chips.width * 0.75)
    }
  }

  ChipNav { id: chipNav; forward: false; anchors.left: chips.left }
  ChipNav { forward: true; anchors.right: chips.right }

  // ---- The grid ----------------------------------------------------------
  GridView {
    id: grid
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: chips.bottom
    anchors.topMargin: Style.space(12)
    anchors.bottom: footer.top
    anchors.bottomMargin: Style.space(10)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    readonly property real gap: Style.space(10)
    cellWidth: Math.floor(width / 3)
    cellHeight: Math.round((cellWidth - gap) * 2 / 3) + gap + cellText.lineSpacing * 2 + Style.space(6)
    model: view.shown

    FontMetrics { id: cellText; font.family: panel.bar.fontFamily; font.pixelSize: Style.font.caption }

    delegate: Item {
      id: cell
      required property var modelData
      width: grid.cellWidth
      height: grid.cellHeight
      readonly property bool current: modelData.id === panel.selectedReviewId

      Rectangle {
        id: tile
        x: 0
        width: grid.cellWidth - grid.gap
        height: Math.round(width * 2 / 3)
        radius: Style.space(4)
        color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
        border.width: cell.current || tile.activeFocus ? 2 : 1
        border.color: tile.activeFocus ? panel.bar.foreground
          : (cell.current ? Color.accent : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.2))
        activeFocusOnTab: true
        Keys.onReturnPressed: view.pick(cell.modelData)
        Keys.onEnterPressed: view.pick(cell.modelData)
        Keys.onSpacePressed: view.pick(cell.modelData)
        onActiveFocusChanged: if (activeFocus) grid.positionViewAtIndex(view.shown.indexOf(cell.modelData), GridView.Contain)

        Image {
          anchors.fill: parent
          anchors.margins: 2
          source: cell.modelData.thumbPath !== "" ? "file://" + cell.modelData.thumbPath : ""
          fillMode: Image.PreserveAspectCrop
          asynchronous: true
          sourceSize.width: 240
        }
      }

      Text {
        anchors.top: tile.bottom
        anchors.topMargin: Style.space(4)
        width: tile.width
        elide: Text.ElideRight
        text: panel.reviewWhen(cell.modelData)
        color: panel.bar.foreground
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      Text {
        anchors.top: tile.bottom
        anchors.topMargin: Style.space(4) + cellText.lineSpacing
        width: tile.width
        elide: Text.ElideRight
        text: panel.reviewStyleLabel(cell.modelData)
        color: view.dim
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: view.pick(cell.modelData)
      }
    }
  }

  function pick(review) {
    panel.selectedReviewId = review.id
    panel.goBack()
  }

  // ---- Footer ------------------------------------------------------------
  Item {
    id: footer
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: footerSep.height + Style.space(10) + Math.max(sizeText.implicitHeight, cleanBtn.implicitHeight)

    PanelSeparator {
      id: footerSep
      width: parent.width
      foreground: panel.bar.foreground
    }

    Text {
      id: sizeText
      anchors.left: parent.left
      anchors.right: browserBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: cleanBtn.verticalCenter
      elide: Text.ElideRight
      text: Model.formatBytes(view.totalBytes) + " on disk"
      color: view.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    PanelButton {
      id: browserBtn
      anchors.right: cleanBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.bottom: parent.bottom
      text: "Open in browser"
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openArchive()
    }

    PanelButton {
      id: cleanBtn
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      text: "Clean up…"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      tooltipText: "Retention and Clear History, in Advanced settings"
      onClicked: panel.advancedOpen = true
    }
  }
}
