"""Frontend card-renderer tests (Node, fixture result, no browser or DB)."""

import shutil
import subprocess

import pytest

node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="node is not installed")


@needs_node
def test_card_renderer():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, "--test", "frontend/tests/card.test.js"],
                          cwd=root, capture_output=True, text=True, timeout=120)
    print(proc.stdout[-2000:])
    print(proc.stderr[-2000:])
    assert proc.returncode == 0, "node card tests failed"
