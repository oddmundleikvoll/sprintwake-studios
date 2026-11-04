"""Revision-guarded coverage handoff to the existing production engine."""
import copy
from pathlib import Path
import tempfile
from urllib.parse import quote

from coverage import camera_key, camera_text, storyboard_shots, warnings
from coverage_render import write_layout
from studio_state import authoring_digest


def apply_coverage(workspace, backend, slug, expected, plan):
    path = f"/api/boards/{quote(slug)}"
    board = backend.get(path)["board"]
    if authoring_digest(board) != expected:
        raise ValueError("Script changed. Reopen the project before using the coverage proposal.")
    shots = storyboard_shots(board, plan)
    if not shots:
        return board, 0
    workspace.snapshots(slug, board)
    updated = copy.deepcopy(board)
    updated.setdefault("shots", []).extend(shots)
    result = backend.put(path, updated, expected_digest=expected, expected_updated_at=board.get("updatedAt"))
    actual = result["board"]
    workspace.sync_script(slug, actual)
    return actual, len(shots)


def layout_reference(workspace, backend, slug, plan, camera):
    issues = warnings(plan, camera)
    if issues:
        raise ValueError(" ".join(issues))
    with tempfile.TemporaryDirectory(prefix="raven-layout-") as directory:
        source = Path(directory) / ("romkart-" + camera_key(plan, camera)[:16] + ".png")
        write_layout(source, plan, camera)
        retained = workspace.import_input(slug, source)
        ref = backend.upload(slug, retained)
    return ref, ("Use reference 1 only for camera framing, room geometry and the coloured character positions. "
                 "Replace the simple proxies with the actual characters from the following references; "
                 "render a finished cinematic scene, not a diagram. " + camera_text(plan, camera))
