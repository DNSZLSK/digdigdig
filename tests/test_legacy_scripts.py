"""Run standalone scenarios in separate processes to isolate their global patches."""

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest


def standalone_scripts():
    result = []
    for path in Path(__file__).parent.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        if "main" in functions and not any(name.startswith("test_") for name in functions):
            result.append(path)
    return sorted(result)


@pytest.mark.parametrize("script", standalone_scripts(), ids=lambda p: p.name)
def test_standalone_scenario(script, tmp_path):
    code = (
        "import runpy,sys; from pathlib import Path; "
        "ns=runpy.run_path(sys.argv[1]); "
        "ns['main'].__globals__['ROOT']=Path(sys.argv[2]); "
        "ns['main']()"
    )
    # The singleton scenario imports the package again in its child process.
    root = script.parent.parent if script.stem == "test_singleton" else tmp_path
    result = subprocess.run(
        [sys.executable, "-c", code, str(script.resolve()), str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
