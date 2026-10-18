import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: a style that failed validation — what is wrong with it, and
// the ways to get it fixed. Reached by picking it, or from Details in the
// main view's status line.
Column {
  id: view
  required property var panel

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color urgent: panel.bar.urgent || Color.urgent

  width: parent.width
  spacing: Style.space(12)

  Text {
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.failedStyle
      ? "“" + panel.failedStyle.label + "” failed validation and can't be used for a generation"
        + (panel.failedStyle.key === panel.shownStyleKey ? " (it is the configured style, so runs will fail until it is fixed)" : "")
        + ":\n" + panel.failedStyle.errors.map(function(e) { return "• " + e }).join("\n")
      : ""
    color: view.urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Flow {
    width: parent.width
    spacing: Style.space(8)

    PanelButton {
      visible: !Model.styleOnlyNeedsPreview(panel.failedStyle) && panel.agentCanLaunch
      text: panel.styleAgentRunning ? "Opening…" : "Fix with AI"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleAgentRunning
      onClicked: panel.fixStyleWithAgent(panel.failedStyle)
    }

    PanelButton {
      visible: !Model.styleOnlyNeedsPreview(panel.failedStyle)
      text: "Copy fix prompt"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleCopyRunning
      onClicked: panel.copyStyleFixPrompt(panel.failedStyle)
    }

    // Missing only its preview: render one, cost shown first.
    PanelButton {
      visible: panel.failedStyle !== null && panel.failedStyle.source === "user"
        && Model.styleOnlyNeedsPreview(panel.failedStyle)
      text: panel.styleRenderKey === (panel.failedStyle ? panel.failedStyle.key : "") && panel.styleRenderEstimate
        ? panel.styleRenderButtonText() : "Render a preview…"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleRenderRunning && !panel.styleEstimateRunning
      onClicked: {
        if (panel.styleRenderKey === panel.failedStyle.key && panel.styleRenderEstimate) panel.renderStylePreview()
        else panel.estimateStyleRender(panel.failedStyle)
      }
    }

    // A missing preview is itself a validation error, so a failed user
    // theme needs this here too.
    PanelButton {
      visible: panel.failedStyle !== null && panel.failedStyle.source === "user"
      text: panel.stylePreviewRunning ? "Choosing…" : "Change preview"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.stylePreviewRunning
      onClicked: panel.changeStylePreview(panel.failedStyle.key)
    }
  }

  Text {
    visible: panel.styleActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleActionStatus
    color: panel.styleActionStatusIsError ? view.urgent : view.dim
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
