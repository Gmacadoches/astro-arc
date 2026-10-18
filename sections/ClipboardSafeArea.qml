import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Ui

// Multi-line ClipboardSafeField: a Qt Quick Controls TextArea styled exactly
// like the kit's TextField, and kept readOnly for its whole life for the same
// reason (see ClipboardSafeField.qml): an editable text item reads the whole
// clipboard on its own, on every clipboard change and readOnly flip. This one
// does its own typing, inserts a newline on Return, and pastes only on an
// explicit Ctrl+V / Shift+Insert, through wl-paste with a 2 s deadline and a
// byte cap.
TextArea {
  id: safeArea

  property color foreground: Color.foreground
  property color accent: Color.accent
  property int maximumLength: 2000
  // Bytes taken from the clipboard per paste: room for maximumLength
  // characters of 4-byte UTF-8, at most 64 KiB.
  readonly property int pasteByteLimit: Math.min(maximumLength * 4, 65536)

  readonly property var _borderSpec: Border.controlSpec(activeFocus ? "focus" : (hovered ? "hover-cursor" : "normal"), foreground, accent)

  readOnly: true
  // readOnly hides the cursor; show it whenever the area has focus.
  cursorVisible: activeFocus
  wrapMode: TextEdit.Wrap
  font.family: Style.font.family
  font.pixelSize: Style.font.bodySmall
  color: foreground
  selectionColor: Style.selectionFillFor(foreground, accent)
  selectedTextColor: foreground
  placeholderTextColor: Qt.darker(foreground, 1.6)

  leftPadding: Style.spacing.controlPaddingX + Border.left(_borderSpec)
  rightPadding: Style.spacing.controlPaddingX + Border.right(_borderSpec)
  topPadding: Style.spacing.inputPaddingY + Border.top(_borderSpec)
  bottomPadding: Style.spacing.inputPaddingY + Border.bottom(_borderSpec)

  background: BorderSurface {
    color: Style.controlFill(safeArea.activeFocus, safeArea.hovered, safeArea.foreground, safeArea.accent)
    borderSpec: safeArea._borderSpec
    radius: Style.cornerRadius
  }

  function replaceSelection(insert) {
    var start = selectionStart
    var end = selectionEnd
    var room = maximumLength - (text.length - (end - start))
    if (insert.length > room) insert = insert.slice(0, Math.max(0, room))
    text = text.slice(0, start) + insert + text.slice(end)
    cursorPosition = start + insert.length
  }

  function deleteBack(wordwise) {
    if (selectionStart !== selectionEnd) { replaceSelection(""); return }
    var pos = cursorPosition
    if (pos === 0) return
    var from = pos - 1
    if (wordwise) {
      while (from > 0 && /\s/.test(text.charAt(from))) from--
      while (from > 0 && !/\s/.test(text.charAt(from - 1))) from--
    }
    select(from, pos)
    replaceSelection("")
  }

  function deleteForward(wordwise) {
    if (selectionStart !== selectionEnd) { replaceSelection(""); return }
    var pos = cursorPosition
    if (pos === text.length) return
    var to = pos + 1
    if (wordwise) {
      while (to < text.length && /\s/.test(text.charAt(to))) to++
      while (to < text.length && !/\s/.test(text.charAt(to))) to++
    }
    select(pos, to)
    replaceSelection("")
  }

  function pasteFromClipboard() {
    if (pasteProc.running) return
    pasteProc.command = ["sh", "-c",
      "timeout 2 wl-paste --no-newline --type text 2>/dev/null | head -c \"$1\"",
      "sh", String(safeArea.pasteByteLimit)]
    pasteProc.running = true
  }

  Keys.onPressed: function(event) {
    var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
    var shift = (event.modifiers & Qt.ShiftModifier) !== 0
    if ((ctrl && event.key === Qt.Key_V) || (shift && event.key === Qt.Key_Insert)) {
      pasteFromClipboard(); event.accepted = true
    } else if (ctrl && event.key === Qt.Key_X) {
      copy()
      replaceSelection(""); event.accepted = true
    } else if (event.key === Qt.Key_Backspace) {
      deleteBack(ctrl); event.accepted = true
    } else if (event.key === Qt.Key_Delete) {
      deleteForward(ctrl); event.accepted = true
    } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      replaceSelection("\n"); event.accepted = true
    } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
      // Tab moves focus, as in every other field, rather than typing a tab.
      event.accepted = false
    } else if (!ctrl && (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)) === 0
               && event.text.length > 0 && !/[\x00-\x1f\x7f]/.test(event.text)) {
      replaceSelection(event.text); event.accepted = true
    }
  }

  Process {
    id: pasteProc
    stdout: StdioCollector {
      waitForEnd: true
      // Line breaks survive; tabs become spaces, other control characters
      // are dropped.
      onStreamFinished: if (safeArea.activeFocus && text.length > 0)
        safeArea.replaceSelection(text.replace(/\r\n?/g, "\n").replace(/\t/g, " ").replace(/[\x00-\x09\x0b-\x1f\x7f]/g, ""))
    }
  }
}
