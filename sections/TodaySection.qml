import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view, top: the selected generation — the newest unless browsed back —
// as a picture you can page through (‹ › on it, or the thumbnail strip, or
// ←→ with it focused), the reading saved with it, and what you can do with
// it. Every block has a fixed height, so browsing never resizes the panel.
Column {
  id: section
  required property var panel

  readonly property var review: panel.selectedReview
  readonly property int count: panel.reviewsIndex.length
  readonly property int index: panel.selectedIndex
  readonly property string imageSource: review
    ? (review.imagePath !== "" ? review.imagePath : review.thumbPath) : ""
  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color scrim: Qt.rgba(panel.bar.background.r, panel.bar.background.g, panel.bar.background.b, 0.78)

  width: parent.width
  spacing: Style.space(14)

  // One row of the ⋯ menu.
  component MenuItem: PanelButton {
    width: parent.width
    leftAlign: true
    fontSize: Style.font.caption
    foreground: Color.popups.text
  }

  // A thumbnail in the strip: the generation's thumb.jpg, ringed when it is
  // the one showing.
  component Thumb: Rectangle {
    id: thumb
    required property var entry
    readonly property bool current: entry.id === section.panel.selectedReviewId
    width: Style.space(36)
    height: width
    radius: Style.space(4)
    color: Qt.rgba(section.panel.bar.foreground.r, section.panel.bar.foreground.g, section.panel.bar.foreground.b, 0.08)
    border.width: current || activeFocus ? 2 : 1
    border.color: activeFocus ? section.panel.bar.foreground
      : (current ? Color.accent : Qt.rgba(section.panel.bar.foreground.r, section.panel.bar.foreground.g, section.panel.bar.foreground.b, 0.2))
    activeFocusOnTab: true
    Keys.onReturnPressed: section.panel.selectedReviewId = entry.id
    Keys.onEnterPressed: section.panel.selectedReviewId = entry.id
    Keys.onSpacePressed: section.panel.selectedReviewId = entry.id

    Image {
      anchors.fill: parent
      anchors.margins: 2
      source: thumb.entry.thumbPath !== "" ? "file://" + thumb.entry.thumbPath : ""
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      sourceSize.width: 96
    }

    MouseArea {
      id: thumbMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: section.panel.selectedReviewId = thumb.entry.id
    }

    PanelToolTip {
      visible: thumbMouse.containsMouse
      text: section.panel.reviewWhen(thumb.entry) + " · " + section.panel.reviewStyleLabel(thumb.entry)
    }
  }

  // ---- The image, with ‹ › and its caption ---------------------------------
  Rectangle {
    id: imageFrame
    width: parent.width
    height: Math.round(width * 2 / 3)
    radius: Style.cornerRadius
    color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.06)
    border.width: activeFocus ? 2 : 1
    border.color: activeFocus ? panel.bar.foreground
      : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

    // Focused, it is the browser: ←→ page, Enter opens the image. At either
    // end the key goes on to the panel's navigation.
    activeFocusOnTab: section.count > 0
    Keys.onLeftPressed: function(event) {
      if (section.index < section.count - 1) panel.showOlder(); else event.accepted = false
    }
    Keys.onRightPressed: function(event) {
      if (section.index > 0) panel.showNewer(); else event.accepted = false
    }
    Keys.onReturnPressed: panel.openSelectedImage()
    Keys.onEnterPressed: panel.openSelectedImage()
    Keys.onSpacePressed: panel.openSelectedImage()

    Image {
      id: todayImage
      anchors.fill: parent
      anchors.margins: 2
      visible: status === Image.Ready
      source: section.imageSource !== "" ? "file://" + section.imageSource : ""
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      // The full-size background can be 4K; the panel needs a fraction.
      sourceSize.width: 760
    }

    Text {
      textFormat: Text.PlainText
      anchors.centerIn: parent
      visible: !todayImage.visible
      text: panel.generating ? "Generating…"
        : section.review ? (todayImage.status === Image.Loading ? "Loading…" : "No image for this entry")
        : "Not generated yet"
      color: panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    MouseArea {
      anchors.fill: parent
      enabled: section.review !== null
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: panel.openSelectedImage()
    }

    // ‹ older, › newer. Mouse targets on the image; from the keyboard the
    // focused image takes ←→ itself.
    Repeater {
      model: [{ older: true }, { older: false }]

      Rectangle {
        required property var modelData
        readonly property bool live: modelData.older ? section.index < section.count - 1 : section.index > 0
        visible: section.count > 1
        anchors.verticalCenter: parent.verticalCenter
        x: modelData.older ? Style.space(8) : parent.width - width - Style.space(8)
        width: Style.space(34)
        height: Style.space(42)
        radius: Style.cornerRadius
        color: section.scrim
        opacity: live ? 1 : 0.35

        Text {
          textFormat: Text.PlainText
          anchors.centerIn: parent
          text: parent.modelData.older ? "󰅁" : "󰅂"
          color: panel.bar.foreground
          font.family: panel.bar.fontFamily
          font.pixelSize: Style.font.body
        }

        MouseArea {
          anchors.fill: parent
          enabled: parent.live
          cursorShape: Qt.PointingHandCursor
          onClicked: parent.modelData.older ? panel.showOlder() : panel.showNewer()
        }
      }
    }

    // Caption: when and in what style on the left, where in the history on
    // the right.
    Item {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.margins: Style.space(8)
      height: whenChip.height
      visible: section.review !== null

      Rectangle {
        id: whenChip
        anchors.left: parent.left
        width: Math.min(whenText.implicitWidth + Style.space(16), parent.width - countChip.width - Style.space(8))
        height: whenText.implicitHeight + Style.space(8)
        radius: Style.space(4)
        color: section.scrim

        Text {
          id: whenText
          textFormat: Text.PlainText
          anchors.fill: parent
          anchors.leftMargin: Style.space(8)
          anchors.rightMargin: Style.space(8)
          verticalAlignment: Text.AlignVCenter
          elide: Text.ElideRight
          text: panel.reviewWhen(section.review)
            + (panel.reviewStyleLabel(section.review) !== "" ? " · " + panel.reviewStyleLabel(section.review) : "")
          color: panel.bar.foreground
          font.family: panel.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Rectangle {
        id: countChip
        anchors.right: parent.right
        width: countText.implicitWidth + Style.space(16)
        height: whenChip.height
        radius: Style.space(4)
        color: section.scrim

        Text {
          id: countText
          textFormat: Text.PlainText
          anchors.centerIn: parent
          text: (section.index + 1) + " of " + section.count
          color: panel.bar.foreground
          font.family: panel.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }

  // ---- Thumbnail strip: as many recent generations as fit, sliding along
  // to keep the one showing in view; All opens the whole history. Its height
  // is kept even when there is nothing to show, so the panel doesn't move. --
  Item {
    id: strip
    width: parent.width
    height: Math.max(Style.space(36), allBtn.implicitHeight)
    // Pulled up under the image, as one block with it.
    readonly property real gap: Style.space(6)
    readonly property int fits: Math.max(1, Math.floor((width - allBtn.width - gap + gap) / (Style.space(36) + gap)))
    readonly property int shown: Math.min(fits, section.count)
    readonly property int start: section.index < shown ? 0 : section.index - shown + 1

    Row {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      spacing: strip.gap

      Repeater {
        model: strip.shown
        Thumb {
          required property int index
          entry: panel.reviewsIndex[strip.start + index]
        }
      }
    }

    PanelButton {
      id: allBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: section.count > 0
      text: "All 󰁔"
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openView("browse")
    }
  }

  // ---- The reading saved with this generation, or — while Save a copy asks
  // for a name — that prompt in its place. A fixed slot: four lines of quote
  // under the narrative-position badge. ---------------------------------------
  Item {
    width: parent.width
    height: badge.implicitHeight + Style.space(4) + quoteMetrics.lineSpacing * 1.25 * 4

    FontMetrics { id: quoteMetrics; font: quote.font }

    Column {
      anchors.fill: parent
      spacing: Style.space(4)
      visible: !panel.themeNamePromptOpen

      Text {
        id: badge
        // PlainText here and on every Text in the plugin: these strings come
        // from the model, and AutoText would render markup in them, including
        // an <img> Qt fetches with no size cap inside the shell.
        textFormat: Text.PlainText
        width: parent.width
        text: panel.selectedPosition !== "" ? panel.selectedPosition : " "
        color: Color.accent
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1.5
      }

      Text {
        id: quote
        textFormat: Text.PlainText
        width: parent.width
        wrapMode: Text.WordWrap
        maximumLineCount: 4
        elide: Text.ElideRight
        text: panel.selectedReading !== "" ? "“" + panel.selectedReading + "”"
          : (section.review ? "No reading was saved with this image." : "")
        // A serif for the one line that is prose rather than UI.
        font.family: panel.selectedReading !== "" ? "Noto Serif" : panel.bar.fontFamily
        font.italic: panel.selectedReading !== ""
        font.pixelSize: panel.selectedReading !== "" ? Style.font.body : Style.font.caption
        lineHeight: 1.25
        color: panel.selectedReading !== "" ? panel.bar.foreground : section.dim
      }
    }

    // Name prompt for Save a copy. Pre-filled with Model.suggestThemeName's
    // guess from that generation's own concept tags.
    Column {
      anchors.fill: parent
      spacing: Style.space(6)
      visible: panel.themeNamePromptOpen

      Text {
        textFormat: Text.PlainText
        text: "Save a copy as a theme named"
        color: section.dim
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      ClipboardSafeField {
        id: themeNameField
        width: parent.width
        placeholderText: "e.g. Geological Rootedness"
        foreground: panel.bar.foreground
        font.family: panel.bar.fontFamily
        text: panel.themeNameDraft
        onTextChanged: panel.themeNameDraft = text
        Keys.onReturnPressed: panel.confirmSaveTheme()
        Keys.onEnterPressed: panel.confirmSaveTheme()
        Keys.onEscapePressed: panel.cancelSaveThemePrompt()
        onVisibleChanged: if (visible) forceActiveFocus()
      }

      Item {
        width: parent.width
        height: Math.max(confirmSaveThemeBtn.implicitHeight, cancelSaveThemeBtn.implicitHeight)

        PanelButton {
          id: cancelSaveThemeBtn
          anchors.left: parent.left
          text: "Cancel"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          onClicked: panel.cancelSaveThemePrompt()
        }

        PanelButton {
          id: confirmSaveThemeBtn
          anchors.right: parent.right
          text: "Save"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          onClicked: panel.confirmSaveTheme()
        }
      }
    }
  }

  // ---- Actions ------------------------------------------------------------
  Item {
    id: actionRow
    width: parent.width
    readonly property real rowHeight: Math.max(setBtn.implicitHeight, regenerateBtn.implicitHeight, moreBtn.implicitHeight)
    readonly property real gap: Style.space(8)
    readonly property real flexWidth: width - rowHeight - gap * 2
    height: rowHeight

    // The primary action: the kit's Button with the accent as its fill. The
    // label takes the bar's background colour so it reads on the accent.
    PanelButton {
      id: setBtn
      anchors.left: parent.left
      width: actionRow.flexWidth * 0.44
      height: actionRow.rowHeight
      text: panel.setThemeRunning ? "Setting…" : "Set wallpaper"
      fontSize: Style.font.caption
      foreground: panel.bar.background
      color: hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent
      opacity: enabled ? 1 : 0.45
      enabled: panel.selectedReviewId !== "" && !panel.setThemeRunning
      tooltipText: "Put this generation's wallpaper and theme on the desktop. The next generation replaces it."
      onClicked: panel.setSelectedTheme()
    }

    // Shows what THIS click will charge, which depends on what is already
    // cached for the current period — not the same as a fresh day's cost.
    PanelButton {
      id: regenerateBtn
      anchors.left: setBtn.right
      anchors.leftMargin: actionRow.gap
      width: actionRow.flexWidth - setBtn.width
      height: actionRow.rowHeight
      iconText: "󰑐"
      iconSize: Style.font.caption
      text: panel.generating ? "Generating…" : Model.nextRunLabel(panel.modelCatalog.nextRun)
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.generating
      iconSpinning: panel.generating
      onClicked: panel.regenerateNow()
    }

    PanelButton {
      id: moreBtn
      anchors.right: parent.right
      width: actionRow.rowHeight
      height: actionRow.rowHeight
      iconText: "󰇘"
      bordered: true
      selected: morePopup.opened
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== ""
      opacity: enabled ? 1 : 0.45
      tooltipText: "More actions"
      onClicked: {
        if (morePopup.opened) { morePopup.close(); return }
        // Open toward whichever side has room inside the panel.
        var top = moreBtn.mapToItem(panel.contentArea, 0, 0).y
        morePopup.upward = panel.contentArea.height - top - moreBtn.height < morePopup.implicitHeight + Style.space(8)
          && top > panel.contentArea.height - top - moreBtn.height
        morePopup.open()
      }

      Popup {
        id: morePopup
        property bool upward: false
        x: moreBtn.width - width
        y: upward ? -height - Style.spacing.xxs : moreBtn.height + Style.spacing.xxs
        width: Style.space(220)
        padding: Style.space(4)
        focus: true
        readonly property var borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)

        background: BorderSurface {
          color: Color.popups.background
          borderSpec: morePopup.borderSpec
          radius: Style.cornerRadius
        }

        onOpened: Qt.callLater(function() { menuColumn.step(0) })
        onClosed: moreBtn.forceActiveFocus()

        contentItem: Column {
          id: menuColumn
          spacing: Style.space(2)

          // ↑↓ walk the visible items, wrapping; 0 focuses the first.
          function step(d) {
            var items = []
            for (var i = 0; i < children.length; i++)
              if (children[i].visible && children[i].activeFocusOnTab && children[i].enabled) items.push(children[i])
            if (items.length === 0) return
            var at = -1
            for (var j = 0; j < items.length; j++) if (items[j].activeFocus) at = j
            items[at < 0 ? 0 : (at + d + items.length) % items.length].forceActiveFocus()
          }

          Keys.onUpPressed: step(-1)
          Keys.onDownPressed: step(1)
          Keys.onTabPressed: step(1)
          Keys.onBacktabPressed: step(-1)
          Keys.onEscapePressed: morePopup.close()

          MenuItem {
            text: "Open image"
            onClicked: { morePopup.close(); panel.openSelectedImage() }
          }
          MenuItem {
            text: "Save a copy…"
            tooltipText: "Install it as a permanent Omarchy theme on this machine."
            onClicked: { morePopup.close(); panel.openSaveThemePrompt() }
          }
          MenuItem {
            text: panel.exportThemeRunning ? "Exporting…" : "Export…"
            tooltipText: "Package it as a theme repository anyone can install."
            enabled: !panel.exportThemeRunning
            onClicked: { morePopup.close(); panel.exportSelectedTheme() }
          }
          MenuItem {
            text: panel.shareRecordRunning ? "Sharing…" : "Share…"
            tooltipText: "Save the image and its reading as one file to send."
            enabled: !panel.shareRecordRunning
            onClicked: { morePopup.close(); panel.shareSelectedRecord() }
          }

          // User themes only: a shipped theme's preview ships with the code.
          PanelSeparator {
            width: parent.width
            visible: panel.selectedPreviewTheme !== null
            foreground: Color.popups.text
          }
          MenuItem {
            visible: panel.selectedPreviewTheme !== null
            text: panel.stylePreviewRunning ? "Setting…" : "Use as style preview"
            tooltipText: panel.selectedPreviewTheme
              ? "Make this image the preview for “" + panel.selectedPreviewTheme.label + "”." : ""
            enabled: !panel.stylePreviewRunning
            onClicked: { morePopup.close(); panel.useSelectedAsStylePreview() }
          }
        }
      }
    }
  }
}
