"""The wire constants three languages declare independently must agree.

``protocol_version`` and the frame size cap exist once in the Python protocol,
once in the Rust bridge and once in the renderer's types. Nothing links them: a
bump that misses one file makes Rust refuse every frame from the backend (the
bridge filters on ``protocol_version`` before relaying) or makes the renderer
drop the replies, which looks like a dead app rather than a version mismatch —
and the release CI currently proves only that each side compiles.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from mdescriptor_studio_backend.config import PROTOCOL_VERSION as PY_PROTOCOL_VERSION
from mdescriptor_studio_backend.protocol import frames as py_frames

ROOT = Path(__file__).resolve().parents[1]


def _constant(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(rf"^(?:export\s+)?const\s+{name}\s*:\s*\w+\s*=\s*([^;\n]+)[;\s]*$", text, re.M)
    matches += re.findall(rf"^(?:export\s+)?const\s+{name}\s*=\s*([^;\n]+)[;\s]*$", text, re.M)
    assert matches, f"{name} not found in {path.relative_to(ROOT)}"
    return matches[-1].strip()


def _as_int(literal: str) -> int:
    """Evaluate an integer literal or a product of them (``8 * 1024 * 1024``)."""
    return _evaluate(ast.parse(literal.strip(), mode="eval").body)


def _evaluate(node: ast.expr) -> int:
    if isinstance(node, ast.Constant):
        assert isinstance(node.value, int), ast.dump(node)
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _evaluate(node.left) * _evaluate(node.right)
    raise AssertionError(f"unsupported constant: {ast.dump(node)}")


def test_protocol_version_matches_across_the_bridge_and_the_renderer():
    rust = _as_int(_constant(ROOT / "src-tauri" / "src" / "main.rs", "PROTOCOL_VERSION"))
    typescript = _as_int(_constant(ROOT / "frontend" / "src" / "types" / "protocol.ts", "PROTOCOL_VERSION"))
    assert (rust, typescript) == (PY_PROTOCOL_VERSION, py_frames.PROTOCOL_VERSION)


def test_frame_size_cap_matches_the_bridge():
    rust = _as_int(_constant(ROOT / "src-tauri" / "src" / "main.rs", "MAX_FRAME_BYTES"))
    assert rust == py_frames.MAX_LINE_BYTES, (
        "the bridge and the protocol must bound a frame at the same size, or a"
        " frame one side accepts is silently dropped by the other"
    )
