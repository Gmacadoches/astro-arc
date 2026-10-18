import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view, top: the selected generation (the newest, unless History says
// otherwise) as a picture, its reading, and what you can do with it. Set
// wallpaper and Regenerate are the two everyday actions; the rest — open,
// save, export, share, use as a style preview — sit behind ⋯.
Column {
  id: section
  required property var panel

  readonly property var review: panel.selectedReview
  readonly property string imageSource: review
    ? (review.imagePath !== "" ? review.imagePath : review.thumbPath) : ""
  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)

  width: parent.width
  spacing: Style.space(12)

  // One row of the ⋯ menu.
  component MenuItem: Button {
    width: parent.width
    leftAlign: true
    fontSize: Style.font.caption
    foreground: Color.popups.text
  }

  // ---- The image. A placeholder until there is a generation to show, or
  // when an entry's image can't be read. ------------------------------------
  Rectangle {
    id: imageFrame
    width: parent.width
    height: Math.round(width * 2 / 3)
    radius: Style.cornerRadius
    color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.06)
    border.width: 1
    border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

    Image {
      id: todayImage
      anchors.fill: parent
      anchors.margins: parent.border.width
      visible: status === Image.Ready
      source: section.imageSource !== "" ? "file://" + section.imageSource : ""
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      // The full-size background can be 4K; the panel needs a fraction.
      sourceSize.width: 760
    }

    Column {
      anchors.centerIn: parent
      visible: !todayImage.visible
      spacing: Style.space(4)

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: panel.generating ? "Generating…"
          : section.review ? (todayImage.status === Image.Loading ? "Loading…" : "No image for this entry")
          : "Not generated yet"
        color: panel.bar.foreground
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        visible: text !== ""
        text: section.review ? section.review.label : ""
        color: section.dim
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    MouseArea {
      anchors.fill: parent
      enabled: section.review !== null
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: panel.openSelectedImage()
    }
  }

  // ---- The reading: narrative-position badge over the one-line
  // distillation — the same two fields the review-history HTML shows
  // (build_review.py's .badge/.distillation). last-run.json only carries the
  // newest run's, so it shows while that one is selected. --------------------
  Column {
    width: parent.width
    spacing: Style.space(4)
    visible: !!panel.lastRun && panel.lastRun.distillation !== ""
      && (panel.selectedIsNewest || panel.reviewsIndex.length === 0)

    Text {
      visible: panel.lastRun ? panel.lastRun.narrativePosition !== "" : false
      text: panel.lastRun ? panel.lastRun.narrativePosition : ""
      color: Color.accent
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.capitalization: Font.AllUppercase
      font.letterSpacing: 1.5
    }

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: panel.lastRun ? "“" + panel.lastRun.distillation + "”" : ""
      // A serif for the one line that is prose rather than UI.
      font.family: "Noto Serif"
      font.italic: true
      font.pixelSize: Style.font.body
      lineHeight: 1.3
      color: panel.bar.foreground
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
    Button {
      id: setBtn
      anchors.left: parent.left
      width: actionRow.flexWidth * 0.44
      height: actionRow.rowHeight
      text: panel.setThemeRunning ? "Setting…" : "Set wallpaper"
      fontSize: Style.font.caption
      foreground: panel.bar.background
      color: hot ? Qt.lighter(Color.accent, 1.12) : Color.accent
      opacity: enabled ? 1 : 0.45
      enabled: panel.selectedReviewId !== "" && !panel.setThemeRunning
      tooltipText: "Put this generation's wallpaper and theme on the desktop. The next generation replaces it."
      onClicked: panel.setSelectedTheme()
    }

    // Shows what THIS click will charge, which depends on what is already
    // cached for the current period — not the same as a fresh day's cost.
    Button {
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

    Button {
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
      onClicked: morePopup.opened ? morePopup.close() : morePopup.open()

      Popup {
        id: morePopup
        x: moreBtn.width - width
        y: moreBtn.height + Style.spacing.xxs
        width: Style.space(220)
        padding: Style.space(4)
        readonly property var borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)

        background: BorderSurface {
          color: Color.popups.background
          borderSpec: morePopup.borderSpec
          radius: Style.cornerRadius
        }

        contentItem: Column {
          spacing: Style.space(2)

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

  // ---- Name prompt for Save a copy. Pre-filled with Model.suggestThemeName's
  // guess from that generation's own concept tags — editable before it's
  // actually saved. ----------------------------------------------------------
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: panel.themeNamePromptOpen

    Text {
      text: "Theme name"
      color: section.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
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
      onVisibleChanged: if (visible) forceActiveFocus()
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
        foreground: panel.bar.foreground
        onClicked: panel.cancelSaveThemePrompt()
      }

      Button {
        id: confirmSaveThemeBtn
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Save"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: panel.confirmSaveTheme()
      }
    }
  }

  Text {
    visible: panel.generateError !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: "Last run error: " + panel.generateError
    color: panel.bar.urgent || "#f38ba8"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: panel.themeActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.themeActionStatus
    color: panel.themeActionStatusIsError ? (panel.bar.urgent || "#f38ba8") : section.dim
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
