import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui

// A single-select picker that stays inside the panel. Looks like the kit's
// Dropdown, but its list opens upward or downward — whichever side of the
// trigger has more room inside `bounds` — and scrolls within that room, so
// it never hangs off the card. An optional footer action ("+ Create a new
// style…") sits under a separator at the end of the list.
//
// Keyboard: the trigger takes focus (Tab or the panel's arrows) and opens on
// Enter or Space. Unlike the kit's Dropdown it leaves Down alone, so the
// panel's arrow navigation passes through it. In the open list, Up/Down
// (or j/k) move, Enter picks, Escape closes.
Item {
  id: picker

  property string value: ""
  // [{ value, label, tag }] — tag is a short dim note on the right ("custom").
  property var options: []
  property string footerText: ""
  // The item the list must stay inside: the panel's content area.
  property Item bounds: null
  // Open upward unless only the space below fits the list.
  property bool preferUp: false
  property color foreground: Color.popups.text
  property string fontFamily: Style.font.family

  signal changed(string value)
  signal footerActivated()

  readonly property bool popupOpen: popup.opened
  readonly property int rowHeight: Style.spacing.popupRowHeight
  readonly property int rowCount: options.length + (footerText !== "" ? 1 : 0)
  readonly property var current: {
    for (var i = 0; i < options.length; i++) if (String(options[i].value) === value) return options[i]
    return null
  }

  implicitWidth: Style.spacing.dropdownWidth
  implicitHeight: Style.spacing.controlHeight
  activeFocusOnTab: true

  function open() {
    if (rowCount === 0) return
    // Room on each side of the trigger, inside the bounds.
    var top = bounds ? picker.mapToItem(bounds, 0, 0).y : 0
    var above = top - Style.spacing.xxs
    var below = (bounds ? bounds.height : 10000) - top - picker.height - Style.spacing.xxs
    var want = list.fullHeight + popup.topPadding + popup.bottomPadding
    popup.upward = preferUp ? (above >= want || above >= below) : (below < want && above > below)
    popup.maxHeight = Math.max(rowHeight * 2, popup.upward ? above : below)
    popup.open()
  }

  function close() { popup.close() }

  Keys.onPressed: function(event) {
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
      popup.opened ? popup.close() : picker.open()
      event.accepted = true
    }
  }

  readonly property var _borderSpec: Border.controlSpec(activeFocus || popup.opened ? "focus" : (triggerHover.hovered ? "hover-cursor" : "normal"), foreground, Color.accent)

  BorderSurface {
    id: trigger
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Style.controlFill(picker.activeFocus, triggerHover.hovered, picker.foreground, Color.accent)
    borderSpec: picker._borderSpec

    HoverHandler { id: triggerHover }

    Text {
      anchors.left: parent.left
      anchors.right: tagText.visible ? tagText.left : chevron.left
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: trigger.borderLeft + Style.spacing.controlPaddingX
      anchors.rightMargin: Style.spacing.md
      text: picker.current ? picker.current.label : picker.value
      color: picker.foreground
      font.family: picker.fontFamily
      font.pixelSize: Style.font.body
      elide: Text.ElideRight
    }

    Text {
      id: tagText
      anchors.right: chevron.left
      anchors.rightMargin: Style.spacing.md
      anchors.verticalCenter: parent.verticalCenter
      visible: text !== ""
      text: picker.current && picker.current.tag ? picker.current.tag : ""
      color: Qt.darker(picker.foreground, 1.4)
      font.family: picker.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      id: chevron
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.rightMargin: trigger.borderRight + Style.spacing.controlGap
      // Points the way the list opens (or would).
      text: (popup.opened ? popup.upward : picker.preferUp) ? "󰅃" : "󰅀"
      color: Qt.darker(picker.foreground, 1.2)
      font.family: picker.fontFamily
      font.pixelSize: Style.font.body
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: {
        picker.forceActiveFocus()
        popup.opened ? popup.close() : picker.open()
      }
    }
  }

  Popup {
    id: popup
    property bool upward: false
    property real maxHeight: 400
    readonly property var borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)

    x: 0
    y: upward ? -height - Style.spacing.xxs : picker.height + Style.spacing.xxs
    width: picker.width
    height: Math.min(list.fullHeight + topPadding + bottomPadding, maxHeight)
    padding: Style.spacing.hairline
    leftPadding: Border.left(borderSpec) + Style.spacing.hairline
    rightPadding: Border.right(borderSpec) + Style.spacing.hairline
    topPadding: Border.top(borderSpec) + Style.spacing.hairline
    bottomPadding: Border.bottom(borderSpec) + Style.spacing.hairline
    focus: true

    background: BorderSurface {
      color: Color.popups.background
      borderSpec: popup.borderSpec
      radius: Style.cornerRadius
    }

    onOpened: {
      var i = 0
      for (var k = 0; k < picker.options.length; k++) if (String(picker.options[k].value) === picker.value) i = k
      list.currentIndex = i
      list.positionViewAtIndex(i, ListView.Contain)
      list.forceActiveFocus()
    }
    onClosed: picker.forceActiveFocus()

    contentItem: ListView {
      id: list
      // Every row, the footer's separator included.
      readonly property real separatorHeight: Style.space(9)
      readonly property real fullHeight: picker.rowCount * picker.rowHeight
        + (picker.footerText !== "" ? separatorHeight : 0)
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      model: picker.rowCount
      currentIndex: -1
      ScrollBar.vertical: ScrollBar { policy: list.contentHeight > list.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }

      function activate(i) {
        popup.close()
        if (i < picker.options.length) {
          var v = String(picker.options[i].value)
          if (v !== picker.value) picker.changed(v)
        } else {
          picker.footerActivated()
        }
      }

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) { popup.close(); event.accepted = true }
        else if (event.key === Qt.Key_Down || event.text === "j") {
          list.currentIndex = Math.min(picker.rowCount - 1, list.currentIndex + 1); event.accepted = true
        } else if (event.key === Qt.Key_Up || event.text === "k") {
          list.currentIndex = Math.max(0, list.currentIndex - 1); event.accepted = true
        } else if (event.key === Qt.Key_Home) {
          list.currentIndex = 0; event.accepted = true
        } else if (event.key === Qt.Key_End) {
          list.currentIndex = picker.rowCount - 1; event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
          if (list.currentIndex >= 0) list.activate(list.currentIndex)
          event.accepted = true
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
          popup.close(); event.accepted = true
        }
      }

      delegate: Item {
        id: row
        required property int index
        readonly property bool isFooter: index >= picker.options.length
        readonly property var option: isFooter ? null : picker.options[index]
        width: ListView.view.width
        height: picker.rowHeight + (isFooter ? list.separatorHeight : 0)

        Rectangle {
          visible: row.isFooter
          x: Style.space(6)
          width: parent.width - Style.space(12)
          y: Math.round(list.separatorHeight / 2)
          height: 1
          color: Qt.rgba(picker.foreground.r, picker.foreground.g, picker.foreground.b, 0.2)
        }

        Rectangle {
          id: rowFill
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          height: picker.rowHeight
          radius: Style.cornerRadius
          color: row.index === list.currentIndex || rowMouse.containsMouse
            ? Style.hoverFillFor(picker.foreground, Color.accent)
            : (!row.isFooter && String(row.option.value) === picker.value
              ? Style.selectedFillFor(picker.foreground, Color.accent) : "transparent")

          Text {
            anchors.left: parent.left
            anchors.leftMargin: Style.spacing.controlPaddingX
            anchors.right: rowTag.left
            anchors.rightMargin: Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter
            text: row.isFooter ? picker.footerText : row.option.label
            color: row.isFooter ? Color.accent : picker.foreground
            font.family: picker.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }

          Text {
            id: rowTag
            anchors.right: parent.right
            anchors.rightMargin: Style.spacing.controlPaddingX
            anchors.verticalCenter: parent.verticalCenter
            text: !row.isFooter && row.option.tag ? row.option.tag : ""
            color: Qt.darker(picker.foreground, 1.4)
            font.family: picker.fontFamily
            font.pixelSize: Style.font.caption
          }

          MouseArea {
            id: rowMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: list.activate(row.index)
          }
        }
      }
    }
  }
}
