"""Capture the real native UI using an isolated fictional project and public art.

No model inference. The director reply is an explicit scripted preview, not a
model qualification. Existing productions and private model settings are never read.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import raven as r
from connections import ConnectionStore, write_private
from contracts.app_lifetime import AppLifetime
from coverage import CoverageStore, new_plan, propose
from sequence import SequenceStore
from studio import ProductionStudio
from workspace import new_shot


def capture(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a fresh screenshot directory; existing captures are preserved.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sprintwake-showcase-") as temp:
        root = Path(temp)
        config = {
            "version": 1,
            "paths": {
                "root": str(root / "Productions"),
                "workspace": str(root / "EngineWork"),
                "engine": str(ROOT / "engine"),
                "python": str(ROOT / "runtime/bundle-venv/bin/python"),
            },
            "comfyUrl": "http://127.0.0.1:8188",
            "selected": {"llm": "none", "image": "none", "video": "none"},
            "llmProfiles": [], "workflowProfiles": [],
        }
        config_path = root / "Settings/connections.json"
        for name in ("llm-services.json", "tts-services.json", "soundtrack-services.json"):
            write_private(config_path.parent / "EngineSettings" / name, {"services": []})
        os.environ["SPRINTWAKE_UPDATE_LOCK_ROOT"] = str(root / "UpdateLocks")
        guard = AppLifetime().enter_app()
        studio = None
        try:
            studio = ProductionStudio(connection_store=ConnectionStore(config_path), connection_settings=config,
                                      start_backend=False)
            studio.backend.credential_loader = lambda _: ""
            studio.submit("boot", studio._bootstrap, studio._booted, scoped=False)
            studio.update_guard = guard

            def pump(seconds=.2):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if not r.Rwait_frame(1 / 60):
                        raise RuntimeError("The showcase window was closed.")
                    studio.tick()

            def wait(predicate, name):
                deadline = time.monotonic() + 45
                while not predicate() and time.monotonic() < deadline:
                    pump(.05)
                if not predicate():
                    raise RuntimeError("Showcase timed out: " + name)

            wait(lambda: studio.ready, "owned CPU backend")
            r.Rwindow_set_size(studio.window, 1600, 1000)
            created = studio.backend.post("/api/boards", {"name": "The Garden Signal"})
            slug, board = created["slug"], created["board"]
            reference = studio.backend.upload(slug, ROOT / "assets/probe-preview.png")
            board["sceneDescription"] = "A small garden robot follows a mysterious signal and discovers that helping a stranger can lead him home."
            board["renderStyle"] = "Warm cinematic animation, copper light, deep garden greens, expressive cyan eyes."
            board["soundscape"] = "Quiet garden ambience, a gentle electronic motif and intimate dialogue."
            board["characters"] = [
                {"id": "rob", "name": "Rob", "description": "A small red garden robot with round cyan eyes.", "image": reference},
                {"id": "mira", "name": "Mira", "description": "The patient keeper of the garden, following the same signal."},
            ]
            board["filmBible"] = {
                "acts": [{"id": "signal", "title": "The signal", "description": "Curiosity becomes a choice to help."}],
                "locations": [{"id": "garden", "title": "The greenhouse garden", "description": "A sunlit garden with a wooden bench and wild flowers."}],
                "props": [{"id": "beacon", "title": "The copper beacon", "description": "A small signal light carried through the garden."}],
            }
            scenes = [
                ("A quiet beginning", "Rob waits beside the bench.", "A faint signal catches his attention.", "Rob turns toward the flowers."),
                ("Following the signal", "Rob stands among tall flowers.", "He follows a soft blue pulse along the path.", "The path reaches an old greenhouse."),
                ("An unexpected friend", "Mira waits at the greenhouse door.", "Rob offers the copper beacon to Mira.", "They decide to follow the signal together."),
                ("The garden answers", "Rob and Mira stand beside the bench.", "The garden lights answer the beacon.", "They watch the final light fade into the evening."),
            ]
            board["shots"] = []
            for i, (title, opening, action, ending) in enumerate(scenes):
                shot = new_shot(title, i + 1)
                shot.update(id=f"garden-{i+1}", frames=121, start=opening, action=action,
                            end=ending, prompt=" ".join((opening, action, ending)),
                            camera="Locked medium-wide shot at eye level", locationId="garden",
                            characterIds=["rob"] if i < 2 else ["rob", "mira"],
                            directorScene=i + 1, startRef=copy.deepcopy(reference))
                board["shots"].append(shot)
            board = studio.backend.put("/api/boards/" + slug, board).get("board", board)
            studio.workspace.sync_script(slug, board)
            structure = SequenceStore(studio.workspace, slug)
            state = {"targetFrames": 20 * 60 * 24, "sequences": [{
                "id": "act-signal", "title": "Act I — The signal", "targetFrames": 5 * 60 * 24,
                "scenes": [{"id": f"scene-{i+1}", "title": item[0], "targetFrames": 75 * 24,
                            "shotIds": [board["shots"][i]["id"]]} for i, item in enumerate(scenes)],
            }]}
            structure.commit(structure.load(), state, "Plan the story arc")
            coverage = CoverageStore(studio.workspace, slug)
            plan = new_plan(board, "garden")
            plan["title"] = "The greenhouse garden"
            plan["cameras"] = propose(plan)
            for camera, title in zip(plan["cameras"], ["Wide master", "Reverse angle · Mira", "Reverse angle · Rob", "Close-up · Rob", "Close-up · Mira"]):
                camera["name"] = title
            coverage.commit(coverage.load(), {"garden": plan})
            studio.summaries = studio.backend.get("/api/boards")["boards"]
            studio.open_project(slug)
            wait(lambda: studio.board is not None and not studio.opening and "open" not in studio.pending, "open demo project")
            studio.mode_set("film")
            pump(.8)
            studio.select_shot(1)
            pump(.4)
            studio.save()
            wait(lambda: not studio.dirty and "save" not in studio.pending, "save demo project")
            pump(.3)
            studio.capture(output / "film-workspace.png")

            session = studio.conversations.bind(studio.current_conversation["id"], slug)
            session["title"] = "The Garden Signal"
            studio.conversations.save(session)
            for other in studio.conversations.list():
                if other["id"] != session["id"] and not other["messageCount"]:
                    studio.conversations.archive(other["id"])
            studio.activate_conversation(session["id"])
            studio.set_text("director.message", "Help me develop this into a twenty-minute film with a clear emotional arc, consistent characters and purposeful scene transitions.")
            actual_chat = studio.backend.raven_chat
            studio.backend.raven_chat = lambda *args, **kwargs: {
                "message": "Build the film around one question: what does the signal mean to Rob?\n\nAct I — Curiosity: establish the garden, the beacon and Rob's need for connection.\n\nAct II — Choice: meeting Mira turns the search into a shared responsibility. Let each new scene change what Rob believes.\n\nAct III — Return: bring the visual and musical motifs back with a new meaning.\n\nWe can develop the film bible, expand the scene plan and write the next shots in small reviewed batches.",
                "actions": [], "proposalStatus": "no_changes", "catalogVersion": "raven-director-1",
            }
            studio.send_chat()
            wait(lambda: "chat" not in studio.pending and not studio.chat_starting, "scripted director preview")
            studio.backend.raven_chat = actual_chat
            pump(.5)
            studio.capture(output / "ai-director.png")
            studio.mode_set("coverage")
            wait(lambda: "coverage.load" not in studio.pending and studio.coverage_plan is not None, "room plan")
            pump(.6)
            studio.capture(output / "room-planning.png")
            if studio.errors:
                raise RuntimeError("Native errors: " + "; ".join(studio.errors))
            (output / "capture.json").write_text(json.dumps({
                "demo": True, "scripted_director_preview": True, "models_started": False,
                "screenshots": ["film-workspace.png", "ai-director.png", "room-planning.png"],
                "raven_errors": studio.errors,
            }, indent=2) + "\n")
        finally:
            if studio is not None:
                studio.close()
            guard.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    capture(parser.parse_args().output.expanduser().resolve())
