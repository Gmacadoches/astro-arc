import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: Create a new style, or Import a theme a chat model wrote —
// the two flows that used to open under the style picker. Reached from
// the end of the style list; the title row is the hero's.
Column {
  id: view
  required property var panel

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color urgent: panel.bar.urgent || "#f38ba8"

  width: parent.width
  spacing: Style.space(12)

  // ---- Create: one line, then the default agent or a prompt for any
  // chat model. ---------------------------------------------------------
  Column {
    width: parent.width
    spacing: Style.space(10)
    visible: panel.styleCreateMode === "create"

    // Until a launch finds none, the agent is assumed and named by its
    // display name ("your agent" before detection answers).
    readonly property bool hasAgent: !panel.styleNoAgent

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Describe the style in one line"
      color: view.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    ClipboardSafeField {
      id: styleIdeaField
      width: parent.width
      maximumLength: 200
      placeholderText: "e.g. 1920s art deco travel posters"
      foreground: panel.bar.foreground
      font.family: panel.bar.fontFamily
      text: panel.styleIdeaDraft
      onTextChanged: panel.styleIdeaDraft = text
      Keys.onReturnPressed: panel.defaultAgent !== "" ? panel.createStyleWithAgent() : panel.copyCreatePrompt()
      Keys.onEnterPressed: panel.defaultAgent !== "" ? panel.createStyleWithAgent() : panel.copyCreatePrompt()
    }

    // The primary action, filled with the accent like Set wallpaper.
    PanelButton {
      width: parent.width
      visible: parent.hasAgent
      text: panel.styleAgentRunning ? "Opening…" : "Create with " + Model.agentLabel(panel.defaultAgent)
      fontSize: Style.font.caption
      foreground: panel.bar.background
      color: hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent
      opacity: enabled ? 1 : 0.45
      enabled: !panel.styleAgentRunning
      onClicked: panel.createStyleWithAgent()
    }

    Item {
      width: parent.width
      height: Math.max(copyPromptBtn.implicitHeight, importThemeBtn.implicitHeight)

      PanelButton {
        id: copyPromptBtn
        anchors.left: parent.left
        width: (parent.width - Style.space(8)) / 2
        text: "Copy prompt"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleCopyRunning
        onClicked: panel.copyCreatePrompt()
      }

      PanelButton {
        id: importThemeBtn
        anchors.right: parent.right
        width: copyPromptBtn.width
        text: "Import theme"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: { panel.styleCreateMode = "import"; panel.styleImportResult = null }
      }
    }

    InfoNote {
      width: parent.width
      foreground: panel.bar.foreground
      fontFamily: panel.bar.fontFamily
      text: parent.hasAgent
        ? Model.agentLabelStart(panel.defaultAgent) + " writes the theme with you in a terminal, and asks before "
          + "checking it or rendering its preview with your API key. Copy prompt is for a web chat model."
        : "No default agent is set in Omarchy. Copy prompt puts everything a chat model needs on the "
          + "clipboard; paste it into any chat, then bring the file it writes back with Import theme."
    }
  }

  // ---- Import theme: the TOML a chat model wrote, from the clipboard
  // or a file, saved to ~/.config/astro-arc/styles and validated. --------
  Column {
    width: parent.width
    spacing: Style.space(10)
    visible: panel.styleCreateMode === "import"

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Copy the theme your chat model wrote (the whole TOML file, code fences are fine), "
        + "or choose a .toml file. It is saved to your styles folder and checked."
      color: view.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Item {
      width: parent.width
      height: Math.max(importClipboardBtn.implicitHeight, importFileBtn.implicitHeight)

      PanelButton {
        id: importClipboardBtn
        anchors.left: parent.left
        width: (parent.width - Style.space(8)) / 2
        text: panel.styleImportRunning ? "Importing…" : "From clipboard"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleImportRunning
        onClicked: panel.importStyle(true)
      }

      PanelButton {
        id: importFileBtn
        anchors.right: parent.right
        width: importClipboardBtn.width
        text: "Choose file…"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleImportRunning
        onClicked: panel.importStyle(false)
      }
    }

    PanelButton {
      text: "‹ Back to create"
      fontSize: Style.font.caption
      foreground: view.dim
      onClicked: { panel.styleCreateMode = "create"; panel.styleImportResult = null }
    }

    // Failed: the errors as one block to paste back into the chat.
    Column {
      width: parent.width
      spacing: Style.space(6)
      visible: panel.styleImportResult !== null && panel.styleImportResult.errors.length > 0

      Text {
        width: parent.width
        wrapMode: Text.WordWrap
        text: panel.styleImportResult
          ? "“" + panel.styleImportResult.label + "” was saved but failed validation. Paste this back into your chat:"
          : ""
        color: view.urgent
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      Rectangle {
        width: parent.width
        height: importErrorsText.implicitHeight + Style.space(12)
        color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.06)
        border.width: 1
        border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.2)

        Text {
          id: importErrorsText
          anchors.fill: parent
          anchors.margins: Style.space(6)
          wrapMode: Text.WordWrap
          text: panel.styleImportResult ? panel.styleImportResult.fixBlock : ""
          color: panel.bar.foreground
          font.family: "monospace"
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        spacing: Style.space(8)

        PanelButton {
          text: "Copy errors"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          onClicked: panel.copyImportErrors()
        }

        PanelButton {
          visible: panel.defaultAgent !== ""
          text: panel.styleAgentRunning ? "Opening…" : "Fix with AI"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          enabled: !panel.styleAgentRunning
          onClicked: panel.fixStyleWithAgent(panel.styleImportResult)
        }
      }
    }

    // Valid but for its preview: render one (cost first) or pick an image.
    Column {
      width: parent.width
      spacing: Style.space(6)
      visible: panel.styleImportResult !== null && panel.styleImportResult.errors.length === 0
        && panel.styleImportResult.needsPreview

      Text {
        width: parent.width
        wrapMode: Text.WordWrap
        text: panel.styleImportResult
          ? "“" + panel.styleImportResult.label + "” is valid. It needs a preview image: render one "
            + "with your image model and settings"
            + (panel.styleRenderEstimate && panel.styleRenderEstimate.line ? " (" + panel.styleRenderEstimate.model
               + ", " + panel.styleRenderEstimate.quality + ")" : "")
            + ", or choose an image you have."
          : ""
        color: view.dim
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      Flow {
        width: parent.width
        spacing: Style.space(8)

        PanelButton {
          text: panel.styleRenderButtonText()
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          enabled: panel.styleRenderEstimate !== null && !panel.styleRenderRunning
          onClicked: panel.renderStylePreview()
        }

        PanelButton {
          text: panel.stylePreviewRunning ? "Choosing…" : "Choose an image…"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          enabled: !panel.stylePreviewRunning && !panel.styleRenderRunning
          onClicked: panel.changeStylePreview(panel.styleImportResult.key)
        }
      }
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
