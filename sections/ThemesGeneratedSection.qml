import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

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
  required property var panel

  width: parent.width
  spacing: Style.space(6)

  PanelSeparator { foreground: panel.bar.foreground }
  PanelSectionHeader { text: "THEMES GENERATED"; foreground: panel.bar.foreground }

  // The picker gets the whole row. It used to share it with a
  // "History" label and the Open button, which left an entry like
  // "Sep 19, 2:10 PM · Cyberpunk · 2.39 MB" elided in the middle —
  // and the part that gets cut is the style, which is the part you
  // are choosing by. The section header already says what this is,
  // so the label column was pure cost; Open moved to the row below,
  // where it sits with the other two things you can do to the
  // entry you picked.
  //
  // The thumbnail beside it is the selected generation's background
  // (the same thumb.jpg the Browse All gallery uses), so a label like
  // "Oct 11, 9:40 PM · Ukiyo-e" comes with a picture of which one
  // that was. Hidden for entries built before thumbnails existed,
  // and the picker takes the whole row again.
  //
  // The thumbnail costs the picker about a fifth of the row, which
  // was enough to elide the end of "· Ukiyo-e · 8.6 MB". So the size
  // left the label and sits under the picker as a caption: it's what
  // you check before Clear History, not what you pick by.
  Item {
    id: reviewPickerRow
    width: parent.width
    height: Math.max(reviewPickerColumn.implicitHeight, reviewThumb.visible ? reviewThumb.height : 0)

    readonly property var selectedReview: panel.reviewForId(panel.selectedReviewId)
    readonly property string selectedThumbPath: selectedReview ? selectedReview.thumbPath : ""

    Rectangle {
      id: reviewThumb
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Math.min(96, parent.width * 0.2)
      height: Math.round(width * 9 / 16)
      visible: reviewPickerRow.selectedThumbPath !== "" && reviewThumbImage.status !== Image.Error
      color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
      border.width: 1
      border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

      Image {
        id: reviewThumbImage
        anchors.fill: parent
        anchors.margins: parent.border.width
        source: reviewPickerRow.selectedThumbPath ? "file://" + reviewPickerRow.selectedThumbPath : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        sourceSize.width: 320
      }
    }

    Column {
      id: reviewPickerColumn
      anchors.left: reviewThumb.visible ? reviewThumb.right : parent.left
      anchors.leftMargin: reviewThumb.visible ? Style.space(8) : 0
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(4)

      Dropdown {
        id: reviewsDropdown
        width: parent.width
        showLabel: false
        value: panel.selectedReviewId
        // cardCount was meaningful when a review could bundle
        // several style variants for one reading (the Phase 3
        // exploration batches); every review since is one real
        // generation, so it's redundant noise now.
        options: panel.reviewsIndex.map(function(r) {
          return { value: r.id, label: r.label }
        })
        foreground: panel.bar.foreground
        onChanged: function(value) { panel.selectedReviewId = value }
      }

      // Size is null on an entry built before that field existed,
      // and the caption goes with it.
      Text {
        width: parent.width
        visible: text !== ""
        text: reviewPickerRow.selectedReview && reviewPickerRow.selectedReview.sizeBytes !== null
          ? Model.formatBytes(reviewPickerRow.selectedReview.sizeBytes) + " on disk"
          : ""
        color: Qt.darker(panel.bar.foreground, 1.3)
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }
    }
  }

  // ---- One action bar for the selected generation. -------------
  //
  // Was two rows with different rules: Open/Browse/Set in a left-
  // packed Row, then Save/Share/Export anchored left/centre/right in
  // an Item. Two layout idioms, two orders, and the pair that
  // actually changes something (Set) sat next to the pair that only
  // looks (Open/Browse).
  //
  // Now one Grid in a single stated order, equal column widths, and
  // it wraps to two rows only when one will not fit. `columns` is
  // the whole layout rule: all of them across if the row can hold
  // them at a sane width, otherwise half each row. Widths are set
  // from the column count rather than from each label, so the row
  // stays put when a label shortens to "Setting…" — which is what
  // the per-button reservedWidth latches used to do one at a time.
  Grid {
    id: reviewActions
    width: parent.width
    visible: !panel.themeNamePromptOpen
    spacing: Style.space(8)

    readonly property int count: 6
    // The narrowest a button may get before wrapping is better than
    // cramming. Below this the labels start eliding, which is worse
    // than a second row.
    readonly property int minButtonWidth: 84
    readonly property bool fitsOneRow:
      (width - spacing * (count - 1)) / count >= minButtonWidth
    columns: fitsOneRow ? count : Math.ceil(count / 2)
    readonly property real cellWidth:
      (width - spacing * (columns - 1)) / columns

    // The whole history as one browsable page — the URL worth
    // bookmarking. Plain files; opens in the default browser. First
    // because it is the way in to everything, not just this entry.
    Button {
      id: archiveBtn
      text: "Browse All"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openArchive()
    }

    Button {
      id: openReviewBtn
      text: "Open"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== ""
      onClicked: panel.openSelectedReview()
    }

    // The only one here that changes what you are looking at, and
    // the next generation undoes it — the permanent copies are Save
    // and Export.
    Button {
      id: setThemeBtn
      text: panel.setThemeRunning ? "Setting…" : "Set"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== "" && !panel.setThemeRunning
      onClicked: panel.setSelectedTheme()
    }

    // Save installs the theme here; Export packages it for someone
    // else.
    Button {
      id: saveThemeBtn
      text: "Save"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== ""
      onClicked: panel.openSaveThemePrompt()
    }

    Button {
      id: exportThemeBtn
      text: panel.exportThemeRunning ? "Exporting…" : "Export"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== "" && !panel.exportThemeRunning
      onClicked: panel.exportSelectedTheme()
    }

    // The odd one out between its neighbours: they copy the THEME,
    // this copies the RECORD — one .html with the image in it, made
    // to be read rather than installed. Last because it is the only
    // one that is not about the desktop, and NOT dropped: it was
    // absent from the requested order, but removing a working
    // feature is a different decision from reordering the bar.
    Button {
      id: shareRecordBtn
      text: panel.shareRecordRunning ? "Sharing…" : "Share"
      width: reviewActions.cellWidth
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.selectedReviewId !== "" && !panel.shareRecordRunning
      onClicked: panel.shareSelectedRecord()
    }
  }

  // ---- Name prompt: shown in place of the row above once Save
  // Selected Theme is clicked. Pre-filled with
  // Model.suggestThemeName's guess from that generation's own
  // concept tags — editable before it's actually saved. ----------
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: panel.themeNamePromptOpen

    Text {
      text: "Theme name"
      color: Qt.darker(panel.bar.foreground, 1.3)
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

  // The selected generation as its theme's preview. User themes
  // only: a shipped theme's preview ships with the code.
  Item {
    id: reviewPreviewRow
    width: parent.width
    readonly property var review: panel.reviewForId(panel.selectedReviewId)
    readonly property var theme: Model.findStyle(panel.styleCatalog, Model.reviewStyleKey(review, panel.styleCatalog))
    visible: !panel.themeNamePromptOpen && review !== null && review.imagePath !== ""
      && theme !== null && theme.source === "user"
    height: visible ? setPreviewBtn.implicitHeight : 0

    Button {
      id: setPreviewBtn
      anchors.left: parent.left
      text: panel.stylePreviewRunning ? "Setting…"
        : "Set as preview for “" + (reviewPreviewRow.theme ? reviewPreviewRow.theme.label : "") + "”"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.stylePreviewRunning
      onClicked: panel.setStylePreview(reviewPreviewRow.theme.key, reviewPreviewRow.review.imagePath)
    }
  }

  // Set as preview reports here too, beside the entry it acted on.
  Text {
    visible: reviewPreviewRow.visible && panel.styleActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleActionStatus
    color: panel.styleActionStatusIsError ? (panel.bar.urgent || "#f38ba8") : Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: panel.themeActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.themeActionStatus
    color: panel.themeActionStatusIsError ? (panel.bar.urgent || "#f38ba8") : Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
