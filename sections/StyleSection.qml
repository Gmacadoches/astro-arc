import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view, bottom: the style row — the selected theme's preview, the
// picker (which ends in "+ Create a new style…" and opens upward), and Tweak
// for your own themes — then one fixed status slot. Everything that used to
// grow the panel under the picker (an action's result, a broken or missing
// style) is a line in that slot now, and the long forms are drill-in views.
Column {
  id: section
  required property var panel
  // Panel.qml resets this to the shown style after a non-selectable pick.
  property alias styleDropdown: styleDropdown

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color urgent: panel.bar.urgent || "#f38ba8"

  width: parent.width
  spacing: Style.space(10)

  Item {
    width: parent.width
    height: Math.max(stylePreviewFrame.height, styleDropdown.implicitHeight)

    // A user theme's swatch is also how you change its preview.
    Rectangle {
      id: stylePreviewFrame
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: styleDropdown.implicitHeight
      height: width
      radius: Style.cornerRadius
      color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
      border.width: activeFocus ? 2 : 1
      border.color: activeFocus ? panel.bar.foreground
        : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

      readonly property bool changeable: panel.shownStyle !== null && panel.shownStyle.source === "user"
        && !panel.stylePreviewRunning
      activeFocusOnTab: changeable
      Keys.onReturnPressed: panel.changeStylePreview(panel.shownStyleKey)
      Keys.onEnterPressed: panel.changeStylePreview(panel.shownStyleKey)
      Keys.onSpacePressed: panel.changeStylePreview(panel.shownStyleKey)

      Image {
        anchors.fill: parent
        anchors.margins: 2
        visible: status === Image.Ready
        source: panel.shownStyle && panel.shownStyle.preview
          ? "file://" + panel.shownStyle.preview + "?v=" + panel.stylePreviewVersion : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        sourceSize.width: 160
      }

      MouseArea {
        id: previewMouse
        anchors.fill: parent
        enabled: stylePreviewFrame.changeable
        hoverEnabled: true
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: panel.changeStylePreview(panel.shownStyleKey)
      }

      PanelToolTip {
        visible: stylePreviewFrame.changeable && (previewMouse.containsMouse || stylePreviewFrame.activeFocus)
        text: "Change preview"
      }
    }

    // Options come from the theme files (style_catalog.py); see
    // loadStyleCatalog. Create sits at the end of the list itself.
    PanelPicker {
      id: styleDropdown
      anchors.left: stylePreviewFrame.right
      anchors.leftMargin: Style.space(8)
      anchors.right: tweakBtn.visible ? tweakBtn.left : parent.right
      anchors.rightMargin: tweakBtn.visible ? Style.space(8) : 0
      anchors.verticalCenter: parent.verticalCenter
      bounds: panel.contentArea
      preferUp: true
      value: panel.shownStyleKey
      options: panel.styleCatalog.map(function(s) {
        return {
          value: s.key,
          label: s.label + (s.valid ? "" : " (Failed Validation)"),
          tag: s.source === "user" ? "custom" : ""
        }
      })
      footerText: "+ Create a new style…"
      foreground: panel.bar.foreground
      fontFamily: panel.bar.fontFamily
      onChanged: function(value) { panel.pickArtStyle(value) }
      onFooterActivated: panel.pickArtStyle(Model.STYLE_CREATE)
    }

    // Your own themes only: a shipped theme's files are the plugin's.
    PanelButton {
      id: tweakBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      height: styleDropdown.implicitHeight
      visible: panel.tweakTheme !== null
      iconText: "󰏫"
      iconSize: Style.font.caption
      text: "Tweak"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openView("tweak")
    }
  }

  // ---- Status slot: two lines, always there. The newest thing worth saying,
  // in this order: what the last action did, what is wrong with the style,
  // the last generation's error. ----------------------------------------------
  Item {
    id: statusSlot
    width: parent.width
    height: statusMetrics.lineSpacing * 2

    FontMetrics { id: statusMetrics; font: statusText.font }

    readonly property var message: {
      if (panel.styleActionStatus !== "")
        return { text: panel.styleActionStatus, error: panel.styleActionStatusIsError, fix: false }
      if (panel.themeActionStatus !== "")
        return { text: panel.themeActionStatus, error: panel.themeActionStatusIsError, fix: false }
      if (panel.failedStyle !== null)
        return { text: "“" + panel.failedStyle.label + "” failed validation"
          + (panel.failedStyle.key === panel.shownStyleKey ? ", so runs will fail until it is fixed." : "."),
          error: true, fix: true }
      if (panel.configuredStyleMissing)
        return { text: "Warning: " + Model.missingStyleWarning(panel.configState.artStyle) + ".", error: true, fix: false }
      if (panel.styleCatalogNotice !== "")
        return { text: panel.styleCatalogNotice, error: true, fix: false }
      if (panel.generateError !== "")
        return { text: "Last run error: " + panel.generateError, error: true, fix: false }
      return null
    }

    Text {
      id: statusText
      anchors.left: parent.left
      anchors.right: detailsBtn.visible ? detailsBtn.left : parent.right
      anchors.rightMargin: detailsBtn.visible ? Style.space(8) : 0
      anchors.top: parent.top
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      text: statusSlot.message ? statusSlot.message.text : ""
      color: statusSlot.message && statusSlot.message.error ? section.urgent : section.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption

      MouseArea {
        id: statusMouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
      }

      // The whole message, when two lines cut it short.
      PanelToolTip {
        visible: statusMouse.containsMouse && statusText.truncated
        text: statusText.text
      }
    }

    PanelButton {
      id: detailsBtn
      anchors.right: parent.right
      anchors.top: parent.top
      visible: statusSlot.message !== null && statusSlot.message.fix
      text: "Details 󰅂"
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.openView("fix")
    }
  }
}
