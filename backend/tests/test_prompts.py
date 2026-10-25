"""Every shipped style's Stage 2 prompts, through the real build(), against
the hash pinned in prompts.sha256. Runs backend/bin/fallback_audit.py (every
model call stubbed, about 6 s) in a throwaway home.

A change to the prompts you mean to make re-pins it in the same commit, so
the commit shows the hash moving:

    python3 backend/bin/fallback_audit.py --out <name> --pin
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from throwaway import throwaway_home  # noqa: E402

AUDIT = Path(__file__).resolve().parents[1] / "bin" / "fallback_audit.py"
PINNED = Path(__file__).with_name("prompts.sha256")


class PromptsTest(unittest.TestCase):
    def test_the_prompts_are_the_pinned_ones(self):
        home, env = throwaway_home(self)
        result = subprocess.run([sys.executable, str(AUDIT), "--out", "test", "--no-control"],
                                env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        produced = next(home.rglob("verification/test/prompts.sha256")).read_text().split()[0]
        self.assertEqual(produced, PINNED.read_text().strip(),
                         "the Stage 2 prompts changed. Diff prompts.jsonl against a run from before the "
                         "change; if you meant it, re-pin with "
                         "python3 backend/bin/fallback_audit.py --out <name> --pin")


if __name__ == "__main__":
    unittest.main()
