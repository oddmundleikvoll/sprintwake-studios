"""Real-window and real-media verification. Does not synthesize OS input."""
import json
from pathlib import Path
import tempfile
import time

import raven as r

from project import ROOT, Project, Shot, Take, import_project, save_project


def run(studio):
    checks = []

    def pump(seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if not r.Rwait_frame(1 / 60):
                raise AssertionError("Raven-vinduet ble lukket under testen.")
            studio.tick()

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    pump(.7)
    check(r.Rwindow_get_size(studio.window)[0] >= 1180, "Native Raven/Vulkan window")
    original_notes = studio.shot.notes
    r.Rtext_editor_set_text(studio.w("notes"), "Accessibility edit check")
    pump(.1)
    check(studio.shot.notes == "Accessibility edit check" and studio.dirty,
          "Native editor setter commits to the saved model")
    r.Rtext_editor_set_text(studio.w("notes"), original_notes)
    pump(.1)
    studio.set_dirty(False)
    studio.capture(ROOT / "qa/studio.png")
    studio.select_shot(2)
    studio.on_action(0, "tab.takes", None)
    studio.on_action(0, "take.0", None)
    pump(.15)
    check(studio.index == 2 and studio.shot.selected_take == 0, "Shot and take selection")
    studio.on_action(0, "review.toggle", None)
    check(studio.shot.reviewed, "Independent local review")
    with tempfile.TemporaryDirectory() as directory:
        saved = save_project(studio.project, Path(directory))
        loaded = import_project(saved)
        check(loaded.shots[2].reviewed and loaded.shots[2].selected_take == 0, "Save/reopen preserves review and take")
    studio.set_dirty(False)
    studio.switch_project(1)
    studio.playing = True
    studio.player.play(True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (studio.frame_count < 5 or studio.player.time() < .6):
        pump(.1)
    check(studio.frame_count >= 5, "AVFoundation decoded real video frames into Raven")
    check(studio.player.time() >= .6, "Native playback clock advances")
    check(studio.player.has_audio(), "Original audio track confirmed")
    studio.on_action(0, "transport.mute", None)
    check(studio.muted and r.Rtimeline_is_track_muted(studio.w("timeline"), studio.timeline_tracks[1]), "Transport and timeline audio mute agree")
    studio.on_action(0, "transport.mute", None)
    studio.playing = False
    studio.player.play(False)
    studio.seek(3.25)
    pump(.7)
    check(abs(studio.player.time() - 3.25) < .09, "Frame-accurate paused seek")
    studio.capture(ROOT / "qa/video.png")
    video = studio.active_video
    image = studio.projects[0].shots[0].take.image
    sequence = Project("sequence-smoke", "Playback check", "", [
        Shot("a", "Video A", .5, takes=[Take("a", "A", video=video, provenance="Sequence video")]),
        Shot("b", "Video B", .5, takes=[Take("b", "B", video=video, provenance="Sequence video")]),
        Shot("c", "Still", .5, takes=[Take("c", "C", image=image)]),
        Shot("d", "Video D", .5, takes=[Take("d", "D", video=video, provenance="Sequence video")]),
    ])
    studio.projects.append(sequence)
    studio.switch_project(len(studio.projects) - 1)
    studio.playing = True
    studio.player.play(True)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and studio.playing:
        pump(.1)
    check(studio.index == 3 and not studio.playing and abs(studio.clock - 2.0) < .01,
          "Mixed video/still sequence advances and respects planned bounds")
    check("Sequence video" in r.Rlabel_get_text(studio.w("viewer.source")), "Imported video provenance preserved")
    studio.mode_set("library")
    pump(.2)
    studio.capture(ROOT / "qa/library.png")
    studio.switch_project(0)
    studio.mode_set("bible")
    pump(.2)
    studio.capture(ROOT / "qa/bible.png")
    studio.mode_set("film")
    studio.shot.selected_take = 0
    studio.select_shot(0)
    studio.on_action(0, "tab.regi", None)
    r.Rwindow_set_size(studio.window, 1180, 760)
    pump(.2)
    studio.capture(ROOT / "qa/studio-compact.png")
    r.Rwindow_set_size(studio.window, 1440, 920)
    pump(.2)
    studio.capture(ROOT / "qa/studio.png")
    check(not studio.errors, "No Raven errors or XML/theme warnings")
    report = {"passed": checks, "video_frames": studio.frame_count, "raven_errors": studio.errors}
    (ROOT / "qa/smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
