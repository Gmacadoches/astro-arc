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

  onIndexChanged: strip.follow()
  onCountChanged: strip.follow()

  function focusSelectedThumb() { strip.focusSelected() }

  // Where ↑↓ land from the panel's navigation (Panel.navMove).
  readonly property Item imageItem: imageFrame
  function selectedThumb() { return thumbRepeater.itemAt(index - strip.start) }
  // Apply theme, or the next button along while it is disabled (Applied ✓,
  // or Regenerate running).
  function firstActionItem() { return setBtn.enabled ? setBtn : regenerateBtn.enabled ? regenerateBtn : moreBtn }

  // One row of the ⋯ menu.
  component MenuItem: PanelButton {
    width: parent.width
    leftAlign: true
    fontSize: Style.font.caption
    foreground: Color.popups.text
  }

  // A group heading in the ⋯ menu: the thing its items act on, so each item
  // only needs the verb. Styled like the narrative-position badge. Not
  // focusable, so ↑↓ step over it.
  component MenuHeading: Text {
    width: parent.width
    textFormat: Text.PlainText
    leftPadding: Style.spacing.controlPaddingX + Style.normalBorderWidth
    topPadding: Style.space(4)
    bottomPadding: Style.space(2)
    color: Color.accent
    font.family: Style.font.family
    font.pixelSize: Math.round(Style.font.caption * 0.85)
    font.bold: true
    font.capitalization: Font.AllUppercase
    font.letterSpacing: 1.5
  }

  // A thumbnail in the strip: the generation's thumb.jpg, ringed when it is
  // the one showing.
  component Thumb: Rectangle {
    id: thumb
    required property var entry
    readonly property bool isThumb: true
    readonly property bool current: entry.id === section.panel.selectedReviewId
    width: Style.space(36)
    height: width
    radius: Style.space(4)
    color: Qt.rgba(section.panel.bar.foreground.r, section.panel.bar.foreground.g, section.panel.bar.foreground.b, 0.08)
    border.width: current || activeFocus ? 2 : 1
    border.color: activeFocus ? section.panel.bar.foreground
      : (current ? Color.accent : Qt.rgba(section.panel.bar.foreground.r, section.panel.bar.foreground.g, section.panel.bar.foreground.b, 0.2))
    activeFocusOnTab: true
    // Focus is selection: a thumbnail you move to is the one showing, and
    // ←→ go on paging past the ends of the strip, which slides to follow.
    onActiveFocusChanged: if (activeFocus && entry.id !== section.panel.selectedReviewId) section.panel.selectedReviewId = entry.id
    Keys.onLeftPressed: function(event) {
      if (section.index > 0) { section.panel.showNewer(); section.focusSelectedThumb() } else event.accepted = false
    }
    Keys.onRightPressed: function(event) {
      if (section.index < section.count - 1) { section.panel.showOlder(); section.focusSelectedThumb() } else event.accepted = false
    }
    Keys.onReturnPressed: section.panel.setSelectedTheme()
    Keys.onEnterPressed: section.panel.setSelectedTheme()
    Keys.onSpacePressed: section.panel.openSelectedImage()

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

    // Focused, it is the browser: ←→ page, Enter applies its theme, Space
    // opens the image. At either end ←→ go on to the panel's navigation.
    activeFocusOnTab: section.count > 0
    // The strip runs newest-first from the left, so ← is newer, → older.
    Keys.onLeftPressed: function(event) {
      if (section.index > 0) panel.showNewer(); else event.accepted = false
    }
    Keys.onRightPressed: function(event) {
      if (section.index < section.count - 1) panel.showOlder(); else event.accepted = false
    }
    Keys.onReturnPressed: panel.setSelectedTheme()
    Keys.onEnterPressed: panel.setSelectedTheme()
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

    // ‹ newer, › older, as the strip runs. Mouse targets on the image; from the keyboard the
    // focused image takes ←→ itself.
    Repeater {
      model: [{ older: true }, { older: false }]

      Rectangle {
        required property var modelData
        readonly property bool live: modelData.older ? section.index < section.count - 1 : section.index > 0
        visible: section.count > 1
        anchors.verticalCenter: parent.verticalCenter
        x: modelData.older ? parent.width - width - Style.space(8) : Style.space(8)
        width: Style.space(34)
        height: Style.space(42)
        radius: Style.cornerRadius
        color: section.scrim
        opacity: live ? 1 : 0.35

        Text {
          textFormat: Text.PlainText
          anchors.centerIn: parent
          text: parent.modelData.older ? "󰅂" : "󰅁"
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
  // only when the one showing would leave it, so moving within it stays
  // put; All opens the whole history. Its height
  // is kept even when there is nothing to show, so the panel doesn't move. --
  Item {
    id: strip
    width: parent.width
    height: Math.max(Style.space(36), allBtn.implicitHeight)
    // Pulled up under the image, as one block with it.
    readonly property real gap: Style.space(6)
    readonly property int fits: Math.max(1, Math.floor((width - allBtn.width - gap + gap) / (Style.space(36) + gap)))
    readonly property int shown: Math.min(fits, section.count)
    property int start: 0

    function follow() {
      var s = start
      if (section.index < s) s = section.index
      else if (section.index >= s + shown) s = section.index - shown + 1
      start = Math.max(0, Math.min(s, section.count - shown))
    }

    function focusSelected() {
      Qt.callLater(function() {
        var thumb = thumbRepeater.itemAt(section.index - strip.start)
        if (thumb) thumb.forceActiveFocus()
      })
    }

    onShownChanged: follow()
    Component.onCompleted: follow()

    Row {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      spacing: strip.gap

      Repeater {
        id: thumbRepeater
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

  // ---- The reading saved with this generation, or — while Install in
  // Omarchy asks for a name — that prompt in its place. The reading opens the
  // generation's full record in the browser. A fixed slot: four lines of quote
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
        font.underline: quoteMouse.containsMouse

        MouseArea {
          id: quoteMouse
          anchors.fill: parent
          enabled: section.review !== null && section.review.path !== ""
          hoverEnabled: true
          cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
          onClicked: panel.openSelectedReview()
        }

        PanelToolTip {
          visible: quoteMouse.containsMouse
          text: "Open the full record in your browser"
        }
      }
    }

    // Name prompt for Install in Omarchy. Pre-filled with Model.suggestThemeName's
    // guess from that generation's own concept tags.
    Column {
      anchors.fill: parent
      spacing: Style.space(6)
      visible: panel.themeNamePromptOpen

      Text {
        textFormat: Text.PlainText
        text: "Install as an Omarchy theme named"
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
          text: "Install"
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
      // Applied: this generation's theme is already the desktop's, so there is
      // nothing to do and the button says so instead of offering it.
      text: panel.setThemeRunning ? "Applying…" : panel.selectedIsApplied ? "Applied ✓" : "Apply theme"
      fontSize: Style.font.caption
      foreground: panel.bar.background
      color: hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent
      opacity: enabled ? 1 : 0.45
      enabled: panel.selectedReviewId !== "" && !panel.setThemeRunning && !panel.selectedIsApplied
      tooltipText: "Puts this generation's wallpaper and colours on your desktop. The next generation replaces it."
      onClicked: { panel.setSelectedTheme(); panel.navRelease() }
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
      onClicked: { panel.regenerateNow(); panel.navRelease() }
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
        // Grows to fit its longest item, between 220 and 300; a style name
        // too long for that is cut short (previewItem).
        width: Math.min(Style.space(300), Math.max(Style.space(220), menuColumn.widest + leftPadding + rightPadding))
        padding: Style.space(4)
        focus: true
        readonly property var borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)

        background: BorderSurface {
          color: Color.popups.background
          borderSpec: morePopup.borderSpec
          radius: Style.cornerRadius
        }

        // An item that acts closes the menu with done(), which hands the keys
        // back to browsing (←→ page again); Escape or a click outside goes
        // back to ⋯.
        property bool acted: false
        function done() { acted = true; close() }

        onOpened: Qt.callLater(function() { menuColumn.step(0) })
        onClosed: {
          if (acted) panel.navRelease(); else moreBtn.forceActiveFocus()
          acted = false
        }

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

          readonly property real widest: Math.max(openImageItem.implicitWidth, previewItem.implicitWidth,
            installItem.implicitWidth, exportItem.implicitWidth, openRecordItem.implicitWidth, saveRecordItem.implicitWidth)

          MenuHeading {
            text: "Image"
            visible: openImageItem.visible || previewItem.visible
          }
          MenuItem {
            id: openImageItem
            // Older generations kept no image of their own; Record has their page.
            visible: section.review !== null && section.review.imagePath !== ""
            text: "Open full size"
            tooltipText: "Opens the PNG in your image viewer."
            onClicked: { morePopup.done(); panel.openSelectedImage() }
          }
          // User styles only: a shipped style's preview ships with the code.
          MenuItem {
            id: previewItem
            readonly property string styleLabel: panel.selectedPreviewTheme ? panel.selectedPreviewTheme.label : ""
            visible: panel.selectedPreviewTheme !== null
            text: panel.stylePreviewRunning ? "Setting…" : "Use as " + previewLabel.elidedText + " preview"
            tooltipText: styleLabel !== "" ? "Replaces “" + styleLabel + "”'s thumbnail in the style picker." : ""
            enabled: !panel.stylePreviewRunning
            onClicked: { morePopup.done(); panel.useSelectedAsStylePreview() }

            TextMetrics {
              id: previewFrame
              font.family: previewItem.fontFamily
              font.pixelSize: previewItem.fontSize
              text: "Use as  preview"
            }
            TextMetrics {
              id: previewLabel
              font: previewFrame.font
              text: previewItem.styleLabel
              elide: Text.ElideRight
              elideWidth: Style.space(300) - morePopup.leftPadding - morePopup.rightPadding
                - previewItem.horizontalPadding * 2 - previewItem._reservedBorderLeft - previewItem._reservedBorderRight
                - previewFrame.advanceWidth
            }
          }

          MenuHeading { text: "Theme" }
          MenuItem {
            id: installItem
            text: "Install in Omarchy…"
            tooltipText: "Adds it to Omarchy's theme picker for good. Doesn't change your desktop."
            onClicked: { morePopup.done(); panel.openSaveThemePrompt() }
          }
          MenuItem {
            id: exportItem
            text: panel.exportThemeRunning ? "Exporting…" : "Export as git repo…"
            tooltipText: "A git repo anyone can install with omarchy theme install. Leaves out the reading."
            enabled: !panel.exportThemeRunning
            onClicked: { morePopup.done(); panel.exportSelectedTheme() }
          }

          MenuHeading { text: "Record" }
          MenuItem {
            id: openRecordItem
            text: "Open in browser"
            tooltipText: "The reading, chart signs, prompt and costs for this generation."
            onClicked: { morePopup.done(); panel.openSelectedReview() }
          }
          MenuItem {
            id: saveRecordItem
            text: panel.shareRecordRunning ? "Saving…" : "Save as .html file…"
            tooltipText: "One file with the image inside, to send. No birth data."
            enabled: !panel.shareRecordRunning
            onClicked: { morePopup.done(); panel.shareSelectedRecord() }
          }
        }
      }
    }
  }
}
