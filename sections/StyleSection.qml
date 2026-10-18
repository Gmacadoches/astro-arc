import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view: STYLE — the picker with the selected theme's preview beside it,
// a failed theme's errors and fixes, and "Create a new style", which opens
// the create and import flows in place.
Column {
  required property var panel
  // Panel.qml resets this to the shown style after a non-selectable pick.
  property alias styleDropdown: styleDropdown

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)
  readonly property color faint: Qt.darker(panel.bar.foreground, 1.5)
  readonly property color urgent: panel.bar.urgent || "#f38ba8"

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "STYLE"; foreground: panel.bar.foreground }

  // ---- Picker: preview swatch, then the dropdown. Options come from the
  // theme files (style_catalog.py); see loadStyleCatalog. A user theme's
  // swatch is also how you change its preview. ------------------------------
  Item {
    width: parent.width
    height: Math.max(stylePreviewFrame.height, styleDropdown.implicitHeight)

    Rectangle {
      id: stylePreviewFrame
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: styleDropdown.implicitHeight
      height: width
      radius: Style.cornerRadius
      color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
      border.width: 1
      border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

      readonly property bool userTheme: panel.shownStyle !== null && panel.shownStyle.source === "user"

      Image {
        id: stylePreviewImage
        anchors.fill: parent
        anchors.margins: parent.border.width
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
        enabled: stylePreviewFrame.userTheme && !panel.stylePreviewRunning
        hoverEnabled: true
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: panel.changeStylePreview(panel.shownStyleKey)
      }

      PanelToolTip {
        visible: previewMouse.enabled && previewMouse.containsMouse
        text: "Change preview"
      }
    }

    Dropdown {
      id: styleDropdown
      anchors.left: stylePreviewFrame.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: panel.shownStyleKey
      // Create and Import have their own control below now.
      options: Model.styleDropdownOptions(panel.styleCatalog).filter(function(o) {
        return o.value !== Model.STYLE_CREATE && o.value !== Model.STYLE_IMPORT
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.pickArtStyle(value) }
    }
  }

  Text {
    visible: panel.configuredStyleMissing
    width: parent.width
    wrapMode: Text.WordWrap
    text: "Warning: " + Model.missingStyleWarning(panel.configState.artStyle) + "."
    color: urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: panel.styleCatalogNotice !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleCatalogNotice
    color: urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  // ---- A theme that failed validation: what is wrong, and the ways to get
  // it fixed. ----------------------------------------------------------------
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
      color: urgent
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Flow {
      width: parent.width
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

      // A missing preview is itself a validation error, so a failed user
      // theme needs this here too.
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

  // ---- Create a new style: the expander. --------------------------------
  Button {
    id: createToggle
    width: parent.width
    leftAlign: true
    iconText: "󰐕"
    iconSize: Style.font.caption
    text: "Create a new style"
    fontSize: Style.font.bodySmall
    foreground: panel.bar.foreground
    onClicked: panel.toggleStyleCreate()

    Text {
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.controlPaddingX
      anchors.verticalCenter: parent.verticalCenter
      text: panel.styleCreateMode !== "" ? "󰅃" : "󰅀"
      color: faint
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.body
    }
  }

  // The open expander: one card holding whichever flow is showing.
  Rectangle {
    width: parent.width
    visible: panel.styleCreateMode !== ""
    height: createCard.implicitHeight + Style.space(12) * 2
    radius: Style.cornerRadius
    color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.04)
    border.width: 1
    border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.15)

    Column {
      id: createCard
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: Style.space(12)
      spacing: Style.space(10)

      // ---- Create: one line, then the default agent or a prompt for any
      // chat model. ---------------------------------------------------------
      Column {
        width: parent.width
        spacing: Style.space(10)
        visible: panel.styleCreateMode === "create"

        readonly property bool hasAgent: panel.defaultAgent !== "" && !panel.styleNoAgent

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          text: "Describe the style in one line"
          color: dim
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
        Button {
          width: parent.width
          visible: parent.hasAgent
          text: panel.styleAgentRunning ? "Opening…" : "Create with " + Model.agentLabel(panel.defaultAgent)
          fontSize: Style.font.caption
          foreground: panel.bar.background
          color: hot ? Qt.lighter(Color.accent, 1.12) : Color.accent
          opacity: enabled ? 1 : 0.45
          enabled: !panel.styleAgentRunning
          onClicked: panel.createStyleWithAgent()
        }

        Item {
          width: parent.width
          height: Math.max(copyPromptBtn.implicitHeight, importThemeBtn.implicitHeight)

          Button {
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

          Button {
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
            ? Model.agentLabel(panel.defaultAgent) + " writes the theme with you in a terminal, and asks before "
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
          color: dim
          font.family: panel.bar.fontFamily
          font.pixelSize: Style.font.caption
        }

        Item {
          width: parent.width
          height: Math.max(importClipboardBtn.implicitHeight, importFileBtn.implicitHeight)

          Button {
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

          Button {
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

        Button {
          text: "‹ Back to create"
          fontSize: Style.font.caption
          foreground: dim
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
            color: urgent
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
            color: dim
            font.family: panel.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          Flow {
            width: parent.width
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
    }
  }

  Text {
    visible: panel.styleActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleActionStatus
    color: panel.styleActionStatusIsError ? urgent : dim
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
