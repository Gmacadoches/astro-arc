import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view: Regenerate beside the last-run status, the last error, and
// the last reading's distillation.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(8)

  // ---- Regenerate, beside the last-generated status -------------
  Item {
    width: parent.width
    height: Math.max(statusText.implicitHeight, regenerateBtn.implicitHeight)

    Text {
      id: statusText
      anchors.left: parent.left
      anchors.right: regenerateBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      elide: Text.ElideRight
      text: panel.lastRun
        ? "Last run: " + Model.formatGeneratedAt(panel.lastRun.generatedAt, function(d) {
            return Qt.formatDateTime(d, "MMM d, h:mm AP")
          })
        : "Not generated yet"
      color: Qt.darker(panel.bar.foreground, 1.5)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Button {
      id: regenerateBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      // Shows what THIS click will charge, which depends on what is
      // already cached for the current period — not the same as a
      // fresh day's cost.
      text: panel.generating ? "Generating…" : Model.nextRunLabel(panel.modelCatalog.nextRun)
      bordered: true
      foreground: panel.bar.foreground
      fontSize: Style.font.caption
      enabled: !panel.generating
      onClicked: panel.regenerateNow()

      // The button used to resize the moment it was clicked: the label
      // shortens ("Regenerate · $0.069" -> "Generating…") AND a spinner
      // icon appeared, so the width moved twice in one frame. Latching
      // the widest width it has ever wanted fixes the shrink, and since
      // the idle label is the longer of the two and no icon is added
      // any more, that maximum is reached on the first paint and never
      // changes again — so there is no initial jump either.
      property real reservedWidth: 0
      onImplicitWidthChanged: if (implicitWidth > reservedWidth) reservedWidth = implicitWidth
      width: Math.max(implicitWidth, reservedWidth)
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

  // ---- Distillation: the one-line psychological core of the last
  // reading, with its narrative-position badge above it — same two
  // fields the review-history HTML shows as a badge over an
  // italicized quote (build_review.py's .badge/.distillation). -----
  Column {
    width: parent.width
    spacing: Style.space(2)
    visible: !!panel.lastRun && panel.lastRun.distillation !== ""

    Text {
      visible: panel.lastRun ? panel.lastRun.narrativePosition !== "" : false
      text: panel.lastRun ? panel.lastRun.narrativePosition : ""
      color: Color.accent
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.capitalization: Font.AllUppercase
    }

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: panel.lastRun ? "“" + panel.lastRun.distillation + "”" : ""
      font.family: panel.bar.fontFamily
      font.italic: true
      font.pixelSize: Style.font.bodySmall
      color: panel.bar.foreground
    }
  }
}
