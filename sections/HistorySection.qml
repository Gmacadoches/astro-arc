import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view: HISTORY — every real generation, picked by date and style with
// its thumbnail beside it; picking one shows it in the Today section above,
// where its actions are. Browse all is the whole archive as one page. Each
// entry carries its on-disk size, which is what you check before Clear
// History (in the Advanced popout); automatic age-based pruning runs every
// generation, see the THEME section's History retention field.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "HISTORY"; foreground: panel.bar.foreground }

  // Thumbnail (the same thumb.jpg the Browse all gallery uses; hidden for
  // entries built before thumbnails existed), picker, Browse all.
  Item {
    id: pickerRow
    width: parent.width
    height: Math.max(reviewThumb.visible ? reviewThumb.height : 0, reviewsDropdown.implicitHeight, archiveBtn.implicitHeight)

    readonly property string thumbPath: panel.selectedReview ? panel.selectedReview.thumbPath : ""

    Rectangle {
      id: reviewThumb
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: reviewsDropdown.implicitHeight
      height: width
      visible: pickerRow.thumbPath !== "" && reviewThumbImage.status !== Image.Error
      radius: Style.cornerRadius
      color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
      border.width: 1
      border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

      Image {
        id: reviewThumbImage
        anchors.fill: parent
        anchors.margins: parent.border.width
        source: pickerRow.thumbPath ? "file://" + pickerRow.thumbPath : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        sourceSize.width: 160
      }
    }

    Dropdown {
      id: reviewsDropdown
      anchors.left: reviewThumb.visible ? reviewThumb.right : parent.left
      anchors.leftMargin: reviewThumb.visible ? Style.space(8) : 0
      anchors.right: archiveBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: panel.selectedReviewId
      options: panel.reviewsIndex.map(function(r) {
        return { value: r.id, label: r.label }
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.selectedReviewId = value }
    }

    Button {
      id: archiveBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      height: reviewsDropdown.implicitHeight
      text: "Browse all"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openArchive()
    }
  }

  // Size is null on an entry built before that field existed, and the
  // caption goes with it.
  Text {
    width: parent.width
    visible: text !== ""
    text: panel.selectedReview && panel.selectedReview.sizeBytes !== null
      ? Model.formatBytes(panel.selectedReview.sizeBytes) + " on disk"
      : ""
    color: Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
    elide: Text.ElideRight
  }
}
