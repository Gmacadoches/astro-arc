import QtQuick
import qs.Commons
import qs.Ui

// Advanced popout: MODEL LIST — catalog status, shortlist toggle, refresh.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "MODEL LIST"; foreground: panel.bar.foreground }

  Item {
    width: parent.width
    height: Math.max(advModelStatus.implicitHeight, advRefreshBtn.implicitHeight, advShowAllBtn.implicitHeight)

    Text {
      id: advModelStatus
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.right: advShowAllBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      wrapMode: Text.WordWrap
      text: {
        if (panel.modelCatalogLoading) return "Refreshing…"
        if (panel.modelCatalogError !== "") return panel.modelCatalogError
        if (!panel.modelCatalog.everFetched) return "Model list not fetched yet"
        var n = (panel.modelCatalog.image || []).length + (panel.modelCatalog.chat || []).length
        return n + " reachable" + (panel.modelCatalog.stale ? " · over a day old" : "")
      }
      color: panel.modelCatalogError !== "" ? (panel.bar.urgent || Color.urgent) : Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Button {
      id: advShowAllBtn
      anchors.right: advRefreshBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      text: panel.showAllModels ? "Shortlist" : "All models"
      foreground: panel.bar.foreground
      onClicked: panel.showAllModels = !panel.showAllModels
    }

    Button {
      id: advRefreshBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: "Refresh"
      enabled: !panel.modelCatalogLoading
      foreground: panel.bar.foreground
      onClicked: panel.loadModelCatalog(true)
    }
  }
}
