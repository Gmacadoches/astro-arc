"""A throwaway home for a test that runs Astro-Arc's commands as processes.

A fake HOME alone leaks on this machine: XDG_DATA_HOME points at the real
archive, `secret-tool` reaches the real keyring over D-Bus whatever HOME is
(so a real key, and real money), and systemctl / omarchy-* / hyprctl act on
the running desktop. throwaway_home() closes each of those:

  - HOME and XDG_DATA_HOME point into a temporary directory, and every other
    XDG_* and ASTRO_ARC_* variable is dropped;
  - DBUS_SESSION_BUS_ADDRESS points at a socket that does not exist;
  - each desktop command is shadowed on PATH by a stub that logs its argv to
    <home>/stub-calls.log and succeeds. secret-tool's stub fails, as a
    keyring with no key does.

    home, env = throwaway_home(self)
    subprocess.run([...], env=env)
"""

import os
import shutil
import tempfile
from pathlib import Path

# Every desktop command backend/bin calls (grep for omarchy-, systemctl, ...).
DESKTOP_COMMANDS = (
    "systemctl", "hyprctl", "notify-send", "xdg-open", "zenity",
    "omarchy-agent", "omarchy-default-agent", "omarchy-launch-tui", "omarchy-notification-send",
    "omarchy-restart-shell", "omarchy-shell", "omarchy-theme-bg-set", "omarchy-theme-color",
    "omarchy-theme-install", "omarchy-theme-set", "omarchy-theme-set-gnome",
    "omarchy-theme-set-templates", "omarchy-theme-set-vscode", "omarchy-theme-switcher",
    "omarchy-weather-location",
)
FAILING_COMMANDS = ("secret-tool",)


def throwaway_home(test):
    """(home, env) for `test`, removed when it ends."""
    home = Path(tempfile.mkdtemp(prefix="astro-arc-test-home-"))
    test.addCleanup(shutil.rmtree, home, ignore_errors=True)
    stubs = home / "stub-bin"
    stubs.mkdir()
    log = home / "stub-calls.log"
    for name, status in [(n, 0) for n in DESKTOP_COMMANDS] + [(n, 1) for n in FAILING_COMMANDS]:
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit {status}\n')
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ASTRO_ARC_", "XDG_"))}
    env.update(HOME=str(home), XDG_DATA_HOME=str(home / ".local/share"),
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={home}/no-bus",
               PATH=f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}")
    return home, env
