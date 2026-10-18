import QtQuick
import qs.Commons
import qs.Ui

// Settings view: API USAGE — running spend from costs.json, and its reset.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "API USAGE"; foreground: panel.bar.foreground }

  // ---- Running total, computed locally from costs.json — real
  // token-based cost for every generation's 3 API calls (see
  // cost_estimate.py). Always available, no API call needed. -----
  Item {
    width: parent.width
    height: Math.max(spentText.implicitHeight, resetUsageBtn.implicitHeight)

    Text {
      id: spentLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Spent"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      id: spentText
      anchors.left: spentLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: resetUsageBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      elide: Text.ElideRight
      text: "$" + panel.costsSummary.total.toFixed(4)
        + " across " + panel.costsSummary.generationCount + " generation" + (panel.costsSummary.generationCount === 1 ? "" : "s")
        + (panel.costsSummary.unknownCount > 0 ? " (+" + panel.costsSummary.unknownCount + " w/ unrecognized-model cost)" : "")
      color: panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Button {
      id: resetUsageBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: panel.resetUsageArmed ? "Confirm Reset?" : "Reset"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.resetUsageArmed ? (panel.bar.urgent || "#f38ba8") : panel.bar.foreground
      onClicked: panel.resetUsageClicked()
    }
  }

  Text {
    visible: panel.resetUsageError !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.resetUsageError
    color: panel.bar.urgent || "#f38ba8"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
