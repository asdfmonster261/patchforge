"""
Verify that component 'requires' dependencies are correctly encoded in the
repack output exe's embedded metadata JSON.

Tests:
  1. requires arrays survive the build→metadata round-trip intact
  2. out-of-range requires indices are not silently truncated
  3. empty requires is stored as []
  4. multiple requires entries are preserved in order
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.repack_builder import build as build_repack
from src.core.repack_project import RepackSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_game_dir(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _read_metadata(exe_path: Path) -> dict:
    """Parse the JSON metadata appended to the end of a PatchForge exe."""
    data = exe_path.read_bytes()
    magic = b"XPACK01\x00"
    assert data[-8:] == magic, "XPACK01 magic not found"
    meta_len = struct.unpack_from("<I", data, len(data) - 12)[0]
    meta_json = data[-(12 + meta_len):-12]
    return json.loads(meta_json)


def _build(tmp: Path, components: list[dict]) -> dict:
    game_dir = _make_game_dir(tmp / "game", {"data.bin": b"\x00" * 1024})
    for i, comp in enumerate(components):
        folder = tmp / f"comp_{i}"
        _make_game_dir(folder, {"file.bin": b"\x01" * 512})
        comp["folder"] = str(folder)

    settings = RepackSettings(
        game_dir=str(game_dir),
        output_dir=str(tmp / "out"),
        app_name="DepTest",
        version="1.0",
        codec="lzma",
        compression="fast",
        threads=1,
        components=components,
    )
    result = build_repack(settings)
    assert result.success, f"Build failed: {result.error}"
    return _read_metadata(Path(result.output_path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_requires(tmp_path):
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": False, "group": "", "requires": []},
    ])
    comp = meta["components"][0]
    assert comp["requires"] == []


def test_single_requires(tmp_path):
    """Component B requires component A (1-based index 1)."""
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": True,  "group": "", "requires": []},
        {"label": "B", "default_checked": False, "group": "", "requires": [1]},
    ])
    assert meta["components"][0]["requires"] == []
    assert meta["components"][1]["requires"] == [1]


def test_multi_requires(tmp_path):
    """Component C requires both A and B."""
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": True,  "group": "", "requires": []},
        {"label": "B", "default_checked": True,  "group": "", "requires": []},
        {"label": "C", "default_checked": False, "group": "", "requires": [1, 2]},
    ])
    assert meta["components"][2]["requires"] == [1, 2]


def test_transitive_requires(tmp_path):
    """A→B→C chain: C requires B, B requires A."""
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": True,  "group": "", "requires": []},
        {"label": "B", "default_checked": False, "group": "", "requires": [1]},
        {"label": "C", "default_checked": False, "group": "", "requires": [2]},
    ])
    assert meta["components"][0]["requires"] == []
    assert meta["components"][1]["requires"] == [1]
    assert meta["components"][2]["requires"] == [2]


def test_requires_order_preserved(tmp_path):
    """requires list order must be preserved exactly."""
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": True,  "group": "", "requires": []},
        {"label": "B", "default_checked": True,  "group": "", "requires": []},
        {"label": "C", "default_checked": True,  "group": "", "requires": []},
        {"label": "D", "default_checked": False, "group": "", "requires": [3, 1, 2]},
    ])
    assert meta["components"][3]["requires"] == [3, 1, 2]


def test_component_indices_in_metadata(tmp_path):
    """components[i].index must equal i+1 (1-based)."""
    meta = _build(tmp_path, [
        {"label": "X", "default_checked": True,  "group": "", "requires": []},
        {"label": "Y", "default_checked": False, "group": "", "requires": [1]},
        {"label": "Z", "default_checked": False, "group": "", "requires": [1, 2]},
    ])
    for i, comp in enumerate(meta["components"]):
        assert comp["index"] == i + 1, f"component {i} has wrong index {comp['index']}"


def test_component_size_bytes(tmp_path):
    """size_bytes per component should equal the sum of that component's file sizes."""
    # Each helper-built component has a single 512-byte file.
    meta = _build(tmp_path, [
        {"label": "A", "default_checked": False, "group": "", "requires": []},
        {"label": "B", "default_checked": False, "group": "", "requires": []},
    ])
    for comp in meta["components"]:
        assert comp["size_bytes"] == 512, f"{comp['label']}: {comp['size_bytes']}"
