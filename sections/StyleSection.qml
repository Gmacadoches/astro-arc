import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view: Style — the picker, the selected theme's preview, a failed
// theme's errors and fixes, and the Create / Import flows.
Column {
  required property var panel
  // Panel.qml resets this to the shown style after a non-selectable pick.
  property alias styleDropdown: styleDropdown

  width: parent.width
  spacing: Style.space(8)

  // ---- Style: label left, dropdown right. Options come from the
  // theme files (style_catalog.py); see loadStyleCatalog. -----------
  Item {
    width: parent.width
    height: Math.max(styleLabel.implicitHeight, styleDropdown.implicitHeight)

    Text {
      id: styleLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Style"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: styleDropdown
      anchors.left: styleLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: panel.shownStyleKey
      options: Model.styleDropdownOptions(panel.styleCatalog)
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.pickArtStyle(value) }
    }
  }

  Text {
    visible: panel.configuredStyleMissing
    width: parent.width
    wrapMode: Text.WordWrap
    text: "Warning: " + Model.missingStyleWarning(panel.configState.artStyle) + "."
    color: panel.bar.urgent || "#f38ba8"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: panel.styleCatalogNotice !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleCatalogNotice
    color: panel.bar.urgent || "#f38ba8"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  // The selected theme's preview, and for a user theme the way to
  // change it. Shipped previews are read-only.
  Item {
    id: stylePreviewRow
    width: parent.width
    visible: panel.shownStyle !== null && panel.failedStyle === null
    height: Math.max(stylePreviewFrame.visible ? stylePreviewFrame.height : 0, changePreviewBtn.implicitHeight)

    Rectangle {
      id: stylePreviewFrame
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Math.min(128, parent.width * 0.3)
      height: Math.round(width * 9 / 16)
      visible: panel.shownStyle !== null && panel.shownStyle.preview !== "" && stylePreviewImage.status !== Image.Error
      color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
      border.width: 1
      border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

      Image {
        id: stylePreviewImage
        anchors.fill: parent
        anchors.margins: parent.border.width
        source: panel.shownStyle && panel.shownStyle.preview
          ? "file://" + panel.shownStyle.preview + "?v=" + panel.stylePreviewVersion : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: false
        sourceSize.width: 320
      }
    }

    Button {
      id: changePreviewBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: panel.shownStyle !== null && panel.shownStyle.source === "user"
      text: panel.stylePreviewRunning ? "Choosing…" : "Change preview"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.stylePreviewRunning
      onClicked: panel.changeStylePreview(panel.shownStyleKey)
    }
  }

  // A theme that failed validation: what is wrong, and the two ways
  // to get it fixed.
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: panel.failedStyle !== null

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: panel.failedStyle
        ? "“" + panel.failedStyle.label + "” failed validation and can't be used for a generation"
          + (panel.failedStyle.key === panel.shownStyleKey ? " (it is the configured style, so runs will fail until it is fixed)" : "")
          + ":\n" + panel.failedStyle.errors.map(function(e) { return "• " + e }).join("\n")
        : ""
      color: panel.bar.urgent || "#f38ba8"
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Row {
      spacing: Style.space(8)

      Button {
        visible: !Model.styleOnlyNeedsPreview(panel.failedStyle) && panel.defaultAgent !== ""
        text: panel.styleAgentRunning ? "Opening…" : "Fix with AI"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleAgentRunning
        onClicked: panel.fixStyleWithAgent(panel.failedStyle)
      }

      Button {
        visible: !Model.styleOnlyNeedsPreview(panel.failedStyle)
        text: "Copy fix prompt"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleCopyRunning
        onClicked: panel.copyStyleFixPrompt(panel.failedStyle)
      }

      // Missing only its preview: render one, cost shown first.
      Button {
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

      // A missing preview is itself a validation error, so a failed
      // user theme needs this here too.
      Button {
        visible: panel.failedStyle !== null && panel.failedStyle.source === "user"
        text: panel.stylePreviewRunning ? "Choosing…" : "Change preview"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.stylePreviewRunning
        onClicked: panel.changeStylePreview(panel.failedStyle.key)
      }
    }
  }

  // ---- Create your own style: one line, then the default agent or a
  // prompt for any chat model. -------------------------------------
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: panel.styleCreateMode === "create"

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Describe the style in one line"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
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

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: panel.defaultAgent !== "" && !panel.styleNoAgent
        ? "Create opens " + Model.agentLabel(panel.defaultAgent) + " in a terminal to write the theme with you. "
          + "Checking it and rendering its preview use your API key, and it asks before each. "
          + "Copy prompt is for a chat model on the web instead."
        : "No default agent is set in Omarchy. Copy prompt puts everything a chat model needs on the "
          + "clipboard; paste it into any chat, then bring the file it writes back with Import theme."
      color: Qt.darker(panel.bar.foreground, 1.5)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Row {
      spacing: Style.space(8)

      Button {
        visible: panel.defaultAgent !== "" && !panel.styleNoAgent
        text: panel.styleAgentRunning ? "Opening…" : "Create with " + Model.agentLabel(panel.defaultAgent)
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleAgentRunning
        onClicked: panel.createStyleWithAgent()
      }

      Button {
        text: "Copy prompt"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleCopyRunning
        onClicked: panel.copyCreatePrompt()
      }

      Button {
        text: "Import theme…"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: { panel.styleCreateMode = "import"; panel.styleImportResult = null }
      }

      Button {
        text: "Close"
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: panel.styleCreateMode = ""
      }
    }
  }

  // ---- Import theme: the TOML a chat model wrote, from the clipboard
  // or a file, saved to ~/.config/astro-arc/styles and validated. ----
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: panel.styleCreateMode === "import"

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Copy the theme your chat model wrote (the whole TOML file, code fences are fine), "
        + "or choose a .toml file. It is saved to your styles folder and checked."
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Row {
      spacing: Style.space(8)

      Button {
        text: panel.styleImportRunning ? "Importing…" : "Import from clipboard"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleImportRunning
        onClicked: panel.importStyle(true)
      }

      Button {
        text: "Choose file…"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !panel.styleImportRunning
        onClicked: panel.importStyle(false)
      }

      Button {
        text: "Close"
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: { panel.styleCreateMode = ""; panel.styleImportResult = null }
      }
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
        color: panel.bar.urgent || "#f38ba8"
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

        Button {
          text: "Copy errors"
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          onClicked: panel.copyImportErrors()
        }

        Button {
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
        color: Qt.darker(panel.bar.foreground, 1.3)
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }

      Row {
        spacing: Style.space(8)

        Button {
          text: panel.styleRenderButtonText()
          bordered: true
          fontSize: Style.font.caption
          foreground: panel.bar.foreground
          enabled: panel.styleRenderEstimate !== null && !panel.styleRenderRunning
          onClicked: panel.renderStylePreview()
        }

        Button {
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
    color: panel.styleActionStatusIsError ? (panel.bar.urgent || "#f38ba8") : Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
