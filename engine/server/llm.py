"""Local language models, used to rewrite a prompt for the video model.

Writing a good LTX-2.5 prompt is a skill with a shape to it — camera move
first, then subject and action, then environment and light, then the sound in
its own trailing clause — and that shape is easy to describe and tedious to
apply by hand to every shot. So a shot's prompt can be handed to a local
language model that knows the shape and asked to rewrite it.

Linked, not installed, exactly like the speech engines: a model is usually
already running somewhere with an owner, so this points at it by name, URL and
model in ``llm-services.json`` rather than shipping one.

The rewrite is deliberately **not** applied on the model's word. It comes back
as a proposal the user accepts or discards, because a prompt is the user's
authorship and a rewrite that silently replaced it would be destroying work
that took thought to write.
"""

from __future__ import annotations

import json
import os
import re
import base64
import contextvars
import mimetypes
import time
import urllib.error
import urllib.request
from contextlib import contextmanager


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
from pathlib import Path
from typing import Any

from .local_config import ensure_local_copy

CONFIG_NAME = "llm-services.json"

DEFAULT_SERVICES: list[dict[str, Any]] = [
    {
        "id": "ollama-local",
        "label": "Ollama (this machine)",
        "kind": "ollama",
        "url": "http://localhost:11434",
        "model": "qwen3.8:27b-mlx",
    },
]


# --------------------------------------------------------------------------- #
# What the model is told
# --------------------------------------------------------------------------- #

# Shared instructions for the installed LTX-2.5 Q8 distilled pipeline.
VIDEO_VISUAL_RULES = """\
LTX-2.5 facts (apply them silently; never mention them in the output):
- Write a clear opening composition followed by one ordered visible action,
  then its final state. Use concrete frame-left/right, foreground/background,
  character positions, eye lines, camera movement and lighting.
- Start images anchor the opening frame; End images guide the closing frame.
  Keep the described composition compatible with the chosen anchors.
  Separate cast portraits and style reference lists are not direct LTX inputs.
  Film jobs use four-view character cards visually in Qwen-Image-2.1-Turbo to compose complete scene
  openings. Automatic closing anchors default OFF and require an explicit
  reachable end if enabled. Manual anchors take priority. Character sheets
  are never complete scene frames.
- Cast descriptions guide appearance. Do not promise exact face or voice
  cloning. Native scene extension carries only the previous 25 frames and
  matching audio; a new scene starts a new sequence. Hard camera cuts,
  reverse angles and changes to another sole subject need a new directorScene,
  not a request to transform the subject during native continuation.
- Keep the film's story arc, character motivations, persistent location/prop
  IDs and intended state changes consistent across scenes. A camera setup may
  contain one to six clips. Keep camera fields identical within a native setup;
  shared location IDs persist across camera cuts. Film timing is allocated on the 8k+1 / 8k frame grid;
  split dialogue or action that cannot fit rather than rushing it.
- Intended scene states are planning data; keep them distinct from observations
  of actual footage. All image inputs require explicit human review before video;
  every video setup is reviewed before the next or final assembly. Every setup boundary is
  checked against the actual ending of the previous setup and the approved
  opening of the next one. Generated or technically done never means visually
  approved. Approved image compositions stay fixed.
- Structured start/action/end, location and prop fields are authoritative
  planning inputs. Render prompts must turn them into clean flowing scene
  prose: never emit field labels, internal IDs, version strings, or review
  metadata in the prompt sent to LTX.
- Environment reference images may be used only when the director selected an
  existing image from the project's catalog. Never invent a reference path or
  claim that an unlisted image is available. Character cards remain visual
  references for composing complete scene images, not arbitrary LTX inputs.
- Visual review is a real, bounded, serialized operation. A cold 27B model
  startup may take up to 720 seconds; wait for that bound when review is
  requested, then release its memory before Qwen or LTX starts. Never run the
  reviewer concurrently with image or video generation.
- Describe the visible scene positively. This distilled pipeline has no
  separate negative-prompt field. Do not add hidden reference IDs or markup.
- The prompt describes what appears on screen, not editing instructions.
"""

VIDEO_SOUND_RULES = """\
LTX-2.5 facts (apply them silently; never mention them in the output):
- Picture and stereo sound are generated together. Name ambience and effects
  that match the visible environment and action.
- Keep non-diegetic music for the assembled soundtrack. Visible music sources
  can be described when relevant.
- Dialogue belongs in the separate dialogue field. The renderer appends the
  literal line and speaker direction. Native voices are generated from text;
  cast voice samples belong to the separate TTS recording path.
"""

# One LTX prose paragraph; scene grouping and reference preparation belong to Film.
SYSTEM_PROMPT = """\
Rewrite the shot as one flowing English paragraph for LTX-2.5.
Establish the visible subjects, their positions and camera framing, describe one
ordered main action, and finish with its concrete, physically reachable end pose.
Preserve the writer's named characters, setting, intent and tone. Do not add
characters, locations or story beats. Give camera motion and subject motion
separate clear clauses, with explicit direction where it matters.
Use previous/next shot context to preserve pose, props, clothing and geography.
A native continuation keeps the previous view and visible identities; a hard
cut, reverse angle or switch to another subject needs a separate directorScene.
Keep character identities distinct. Preserve defining visual details from cast
and image context; do not reinterpret the preceding subject as another character.
Film production composes scene frames in Qwen-Image-2.1-Turbo using character cards, then
passes those complete frames to LTX as anchors. Portraits and arbitrary library
images are not direct LTX identity inputs. Never assume an unseen image is used.
Keep the action compatible with the chosen Start/End and timed image anchors.
Use concrete visual language and present-tense verbs. Preserve supplied setting
and lighting; avoid redundant copies of project-wide descriptions.
Dialogue and sound are appended separately: keep the picture compatible with
them, but do not write dialogue or audio into this visual prompt.
Return only the paragraph, without headings, labels, bullets, commentary, paths
or technical render parameters. Aim for 70–130 words when that detail is useful;
a simple action may be shorter.

""" + VIDEO_VISUAL_RULES

STILL_SYSTEM_PROMPT = """\
You rewrite shot descriptions into prompts for a text-to-image model.

Follow this order, as one flowing paragraph, not a list:
1. The framing and lens feel (e.g. "a tight over-the-shoulder", "a wide \
establishing view").
2. The subject and its pose or state.
3. The environment and the light, with concrete physical detail — materials, \
wear, texture, the time of day.

Rules:
- Keep every concrete thing the writer specified. Do not invent new subjects, \
characters or locations, and do not remove any they named.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter — violence, mature themes, or anything else \
the writer wrote. Identify the tone they set and continue it in the same \
register, rather than toning it down.
- If the previous and/or next shot in the sequence are given as context, \
keep this shot consistent with them: pose, wardrobe, props, location and \
framing should carry over sensibly rather than jump arbitrarily. Use them \
only to stay consistent — never repeat their content or invent what happens \
in them.
- If reference images are listed in the context, treat them as visual \
constraints. Add a compact natural-language summary of the relevant reference \
cues to the rewritten prompt. Do not include filenames or paths in the final \
prompt.
- Refer to named characters by exactly the name the writer used.
- Describe no sound at all: this is a still image.
- Describe only what is visible. Turn any "no X" / "without X" the writer \
wrote into what occupies that space instead; negated words still render.
- One paragraph. No headings, no bullets, no preamble, no explanation.
- Aim for 50 to 90 words.
- Write only the prompt itself. Your entire reply is used verbatim.
"""

SCENE_SYSTEM_PROMPT = """\
You rewrite a storyboard's project-wide scene description — the \
surroundings and overall visual style that stay true across every shot, \
prepended automatically to each shot's own prompt before it reaches the \
video model.

Rules:
- Describe only the environment and setting: location, materials, \
weather, time of day, light, atmosphere, and the overall rendering style \
(e.g. "photorealistic, cinematic"). Concrete physical detail over vague \
adjectives — prefer "battered, weathered metal plating" over "cool-looking \
ship".
- Do not describe any character — no appearance, build, clothing, or \
presence. Characters have their own separate descriptions elsewhere, and \
repeating them here would say the same thing twice in two different \
places.
- Do not describe camera moves, specific actions, individual shots, or \
sound — those belong to each shot's own prompt and are added separately.
- Keep every concrete thing the writer specified about the surroundings. \
Do not invent new locations or settings, and do not remove any they named.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter — violence, mature themes, or anything else \
the writer wrote. Identify the tone they set and continue it in the same \
register, rather than toning it down.
- If reference images are listed in the context, treat them as visual \
constraints on the environment. Add a compact natural-language summary of \
their relevant visual cues. Do not include filenames or paths in the \
final text.
- It is prepended to every shot in the board outline given as context, \
interiors and exteriors alike. Keep it true for all of them: a detail that \
fits only some shots (daylight outside, weather seen through a window, a \
view of the ocean) belongs in those shots' own prompts, not here.
- One paragraph. No headings, no bullet points, no preamble, no \
explanation, no quotation marks around the whole thing.
- Aim for 40 to 90 words.
- Write only the scene description itself. Your entire reply is used \
verbatim.

""" + VIDEO_VISUAL_RULES

RENDER_STYLE_SYSTEM_PROMPT = """\
You rewrite a storyboard's project-wide render style — how every shot should \
look on screen, sent to the video model with every shot's own prompt and with \
the scene description.

Rules:
- Describe only the look: the medium (photoreal live action, hand-drawn \
animation, stop motion, ...), the camera and lens character (large-format \
digital cinema, 35mm film, anamorphic, shallow depth of field), the quality \
and colour of the light, the colour grade and palette, grain and texture, \
and the overall mood that follows from them. Concrete visual terms over \
vague praise: prefer "cool steel-blue shadows with sodium-amber highlights, \
fine 35mm grain" over "beautiful moody colours".
- Do not describe the location, any character, any object, any action, any \
camera move or any sound. The place is the scene description, people are the \
cast, and each shot says what happens; repeating them here would say the \
same thing twice in two places.
- Keep every concrete thing the writer specified about the look. Do not \
invent a different medium or palette, and do not remove any they named.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter — violence, mature themes, or anything else \
the writer wrote. Continue the tone they set rather than toning it down.
- If the scene description is given as context, make the style fit it, but \
do not repeat any of it. If reference images are listed, treat them as \
visual constraints on the look and summarise only their relevant cues; \
never include filenames or paths.
- It applies to every shot in the board outline given as context, so keep \
it true for all of them: a look that suits only some shots belongs in those \
shots' own prompts, not here.
- One short paragraph. No headings, no bullet points, no preamble, no \
explanation, no quotation marks around the whole thing.
- Aim for 25 to 60 words.
- Write only the render style itself. Your entire reply is used verbatim.

""" + VIDEO_VISUAL_RULES

SOUNDSCAPE_SYSTEM_PROMPT = """\
You rewrite a storyboard's project-wide background sound effects — the ambient \
audio bed that can be rendered into every shot, or held out and mixed over \
the finished cut separately.

Rules:
- Ground the sound in the scene description given as context. Read what \
the surroundings actually are — location, materials, weather, activity \
implied by the setting — and propose concrete, plausible sound effects \
that belong there, not an abstract mood. A garage scene implies things \
like a dripping fluid, a ticking cooling engine, distant street traffic; a \
desert scene implies wind, shifting sand, the heat-tick of metal. Name \
what is actually heard, grounded in that environment, rather than \
describing how it feels.
- Describe only what is heard: ambience, room tone, weather, distant \
activity, machinery or drone — layered and separated by commas.
- It is rendered into every shot in the board outline given as context, \
interiors and exteriors alike: keep only sounds plausible in all of them, \
and leave sounds specific to some shots to those shots' own sound accents.
- Do not describe anything visual: no camera moves, no actions, no \
lighting, no characters' appearance.
- Do not describe spoken dialogue or intelligible words — this is an \
ambient bed, not a voice.
- Do not describe music, a score or instruments playing: the project's \
soundtrack is written and generated separately.
- Keep every concrete thing the writer specified. Do not invent sound \
sources that contradict the scene, and do not remove any the writer \
named.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter. Identify the tone the scene sets and \
continue it in the same register, rather than toning it down.
- One paragraph, or a short comma-separated phrase. No headings, no \
bullet points, no preamble, no explanation, no quotation marks around the \
whole thing.
- Aim for 15 to 45 words.
- Write only the background sound description itself. Your entire reply \
is used verbatim.

""" + VIDEO_SOUND_RULES

SOUNDTRACK_SYSTEM_PROMPT = """\
You write the music prompt for a storyboard's soundtrack — one continuous \
instrumental score laid under the whole finished cut, generated by Stable \
Audio 3.

Rules:
- Music only. No sound effects, ambience, room tone or Foley: those are \
rendered separately into each shot as the Background sound effects.
- Instrumental only: no vocals, singing, lyrics or spoken words. Dialogue \
sits on top of this music and must stay clear.
- The model was trained on library-music descriptions and does not know \
films, composers, artists or song titles. If the writer names one ("like the \
Jaws theme", "Hans Zimmer style"), replace the name with what makes \
that music recognisable: genre, era, orchestration, instruments and how they \
play, harmony, mood, energy and tempo. Never keep the name, and never ask for \
a specific existing melody.
- Cover genre, the lead and supporting instruments, mood and energy, how it \
builds or changes across the cut, and a tempo as "N BPM".
- Ground it in the scene description and the board outline given as context, \
so the music fits the story's tone and pace.
- Keep every concrete musical choice the writer specified.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter. Match the tone the story sets.
- One paragraph of plain prose, 30 to 80 words. No headings, no bullet \
points, no preamble, no explanation, no quotation marks around the whole thing.
- Write only the music description itself. Your entire reply is used verbatim.
"""

SOUND_ACCENT_SYSTEM_PROMPT = """\
You rewrite a single shot's scene-background sound accents — additional, \
localized environmental sounds that belong to this one clip, layered on top \
of the project's general Background sound effects.

Rules:
- Read the project's general Background sound effects and treat it as an exclusion \
list. Do not repeat it, paraphrase it, expand it, or provide a replacement \
for any sound already named there.
- Ground the accents in the project's scene description and this shot's prompt. \
Choose only additional sounds that are plausibly audible in this specific \
location and moment: for example nearby birds in this scene, a distant siren, \
a localized machine hum, a room creak, or a passing vehicle.
- Keep these as background or environmental accents, not foreground action Foley. \
Do not add footsteps, impacts, object handling, switches, splashes, or other \
action sounds unless the text clearly establishes them as distant background \
sound.
- Describe only local, momentary or location-specific environmental sound — not \
the constant ambient bed and not music.
- Do not describe anything visual: no camera moves, no lighting, no \
characters' appearance.
- Do not describe spoken dialogue, or any words being said, in any form — \
dialogue is a separate field and must never appear here, even as a \
suggestion.
- Keep every concrete background or environmental sound the writer specified. \
Do not invent sounds that contradict the shot, but omit foreground action Foley, \
dialogue, music, and any sound already covered by the general Background sound effects.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter. Identify the tone the shot sets and continue \
it in the same register, rather than toning it down.
- One paragraph, or a short comma-separated phrase. No headings, no bullet \
points, no preamble, no explanation, no quotation marks around the whole \
thing.
- If the shot has dialogue, keep the accents low enough to sit under it.
- Aim for 10 to 35 words.
- Write only the sound accents themselves. Your entire reply is used \
verbatim.

""" + VIDEO_SOUND_RULES

DIALOGUE_SYSTEM_PROMPT = """\
You rewrite one spoken line for a storyboard character. Preserve the line's \
story purpose, facts, intention, and approximate spoken length, but make the \
word choice, rhythm, syntax, humour, formality, and verbal mannerisms sound \
authentic to the selected speaker.

Rules:
- Research notes may be supplied from a web search. Use them only when they \
clearly describe the same named character. Treat unrelated or ambiguous \
results as irrelevant.
- Extract general speech traits from research; never copy, closely paraphrase, \
or quote an existing line. The result must be original dialogue for this scene.
- Use the character card and shot context as constraints. Do not invent new \
plot facts, actions, relationships, names, or backstory.
- Preserve the meaning of the writer's line unless changing its wording is \
necessary to express the same intent in character.
- Keep it short enough for the supplied shot duration. Natural dialogue is \
roughly two to three words per second, and any pauses the delivery \
direction asks for count against the same duration.
- If surrounding dialogue is supplied, make the line follow on from what was \
just said and lead into what comes next, without repeating either.
- Return only the words to be spoken: no speaker name, quotation marks, stage \
directions, delivery notes, explanation, headings, or alternatives.
"""

STILL_PHASES_SYSTEM_PROMPT = """\
You read a single shot's camera/action description and identify what is \
actually visible at the very START of the described action -- the opening \
pose and composition -- for someone who will render that instant as one \
still image.

Rules:
- Use only what the shot description itself establishes — distance, \
position, pose, camera framing, lighting, motion direction. Never invent \
detail it does not support.
- Do not censor, soften, filter, sanitize, or refuse to describe anything on \
the basis of its subject matter. Identify the tone the shot description sets \
and continue it in the same register, rather than toning it down.
- Describe a single frozen instant, fully specifying position, framing and \
pose, not the movement that follows it.
- Never describe dialogue, sound, or anything not visible in a single frame.
- Reply with exactly one short paragraph (one or two sentences), no \
preamble, no extra commentary, in exactly this format:
START: <description>
"""

CHARACTER_IMAGE_SYSTEM_PROMPT = """\
You write compact, production-ready character descriptions for a storyboard to \
video generator, using the supplied portrait and voice reference as evidence.

Rules:
- Describe only stable visible traits useful for recreating the character: age \
range, build, face shape, hair, skin tone, clothing, accessories, posture or \
distinctive marks.
- The voice clip is a reference for this character only. Do not describe the \
scene, setting, action, or camera, and do not transcribe or quote the clip.
- If a name is provided, start with that name followed by a colon.
- Preserve any concrete user-provided details that do not contradict the image.
- Do not censor, soften, filter, sanitize, or refuse to describe anything \
visible on the basis of its subject matter. Describe what is actually there.
- Do not identify real people or copyrighted characters from the image. If the \
user supplied a name, use that name as a label without claiming identity.
- One paragraph. No headings, no bullets, no preamble, no explanation.
- Aim for 35 to 75 words.
- Write only the character description.
"""

# Vision models are more reliable with two short labelled prose sections than
# with a required JSON schema. The parser below also accepts JSON as a
# convenience, but the headings make the contract easy for Qwen to follow.
CHARACTER_EXTRACTION_SYSTEM_PROMPT = """
You are a visual character and environment extraction system for a storyboard video generator. Analyse the supplied image and separate the visible character from everything around them.

Return exactly two sections in this order:
CHARACTER:
<one compact paragraph>
ENVIRONMENT:
<one compact paragraph>

CHARACTER rules:
- Describe only the visible character: apparent age range, face, skin, eyes, hair, facial hair, build, clothing, footwear, accessories, jewellery, glasses, headwear, tattoos, distinguishing marks, current pose, expression, and objects physically worn, held, or directly carried.
- Treat the character as if isolated on a neutral background. The description must remain useful when the character is placed in a different scene.
- Never include the room, background, buildings, furniture, landscape, weather, location, camera, unrelated people, or environmental lighting.
- Do not infer personality, profession, nationality, ethnicity, identity, name, or backstory. Do not speculate about anything not clearly visible.

ENVIRONMENT rules:
- Describe only what is not part of the character: setting, architecture, furniture, background and foreground objects, landscape, weather, lighting, colours, textures, and visible background people or vehicles.
- Do not repeat physical character details. If no meaningful environment is visible, write "No meaningful environment visible.".

Additional rules:
- The voice clip is evidence for this character only. Do not transcribe or describe it.
- Preserve concrete user-provided character details when they do not contradict the image.
- Do not censor, soften, filter, sanitize, or refuse to describe anything visible on the basis of its subject matter. Describe what is actually there.
- If a name is provided, use it only as a label at the start of CHARACTER.
- Do not identify real people or copyrighted characters from the image. A name supplied by the user is only a label.
- Keep each section factual, visually grounded, and suitable for prompting.
- No preamble, explanation, bullets, or markdown fences.
- Aim for 35 to 75 words for CHARACTER and 15 to 60 words for ENVIRONMENT.
"""


def build_user_message(
    text: str,
    *,
    scene: str = "",
    soundscape: str = "",
    context: str = "",
    context_label: str = "",
    previous_shot: dict[str, str] | None = None,
    next_shot: dict[str, str] | None = None,
    characters: list[dict[str, Any]] | None = None,
    reference_images: list[dict[str, str]] | None = None,
    outline: str = "",
    dialogue: str = "",
    duration_seconds: float | None = None,
    shot_sound: str = "",
    instruction: str = "Rewrite this shot description:",
) -> str:
    """The text to rewrite, plus the context it has to stay consistent with.

    The scene description and cast are given as *context, not as material to
    fold in* — they are already prepended to every shot when the full prompt
    is assembled, so repeating them here would say everything twice.

    *soundscape* is the project's general Background sound effects. It is included as
    an explicit exclusion context for shot sound accents, so the rewrite can
    add only sounds that are not already in the general bed.

    *context* is a second, generic slot for whatever else the text being
    rewritten has to stay grounded in but must not repeat — a shot's own
    prompt, say, when rewriting that shot's sound accents. *context_label*
    names what it is; without one a generic label is used.

    *previous_shot*/*next_shot* are the immediately adjacent shots in the
    board, each ``{"title": ..., "prompt": ...}`` when one exists on that
    side. They are continuity references only, same spirit as *scene*: shown
    so the rewrite doesn't contradict where the sequence just was or is about
    to go, never material to fold in or restate.

    *outline* is board_outline()'s one line per shot, for the project-wide
    fields: a scene description or background sound is applied to every
    shot, so it has to be written knowing what all of them are.

    *dialogue*, *duration_seconds* and *shot_sound* are this shot's own
    spoken line, length and sound (bed plus accents) — for a shot rewrite,
    so the picture leaves room for the line and doesn't contradict the
    soundtrack LTX generates from the same text.
    """
    blocks = []
    if outline.strip():
        blocks.append(
            "Every shot in this board, in order — what the text below is "
            "applied to. Context only; do not describe individual shots:\n"
            + outline.strip()
        )
    if scene.strip():
        blocks.append(
            "The project's scene description, already applied to every shot. "
            "Do not repeat it; stay consistent with it:\n" + scene.strip()
        )
    if soundscape.strip():
        blocks.append(
            "The project's general Background sound effects, already applied as the "
            "shared sound bed. Do not repeat, paraphrase, or replace any of "
            "these sounds when writing shot-specific sound accents:\n"
            + soundscape.strip()
        )
    if context.strip():
        label = context_label or (
            "Context this must stay consistent with, already written "
            "elsewhere and not to be repeated:"
        )
        blocks.append(label + "\n" + context.strip())
    for side, shot in (("previous", previous_shot), ("next", next_shot)):
        prompt = ((shot or {}).get("prompt") or "").strip()
        if not prompt:
            continue
        title = ((shot or {}).get("title") or "").strip()
        heading = title or f"the {side} shot"
        blocks.append(
            f"The {side} shot in this sequence, \"{heading}\" — for continuity "
            "only. Stay consistent with where it leaves off (or is about to "
            "pick up): camera position, character pose/wardrobe, props, and "
            "location. Do not repeat, summarize, or describe its content:\n"
            + prompt
        )
    for ch in characters or []:
        name = (ch.get("name") or "").strip()
        desc = (ch.get("description") or "").strip()
        if name:
            blocks.append(
                f"A character in this shot, already described elsewhere. Refer "
                f"to them as \"{name}\"; do not re-describe them:\n"
                f"{name}: {desc}" if desc else
                f"A character in this shot. Refer to them as \"{name}\"."
            )
    refs = [r for r in (reference_images or []) if (r.get("label") or "").strip()]
    if refs:
        lines = []
        for r in refs:
            role = (r.get("role") or "Reference image").strip()
            label = (r.get("label") or "").strip()
            summary = (r.get("summary") or "").strip()
            tag = (r.get("tag") or "").strip().lstrip("@")
            tag_text = f" @{tag}" if tag else ""
            line = f"- {role}{tag_text}: {label}"
            if summary and summary != label:
                line += f" ({summary})"
            lines.append(line)
        blocks.append(
            "Reference images attached to this shot. Use these as visual "
            "constraints when rewriting, and fold a concise summary of their "
            "visual cues into the prompt. An @tag names the corresponding "
            "attached image; preserve that @tag when it is part of the user's "
            "shot prompt. Do not mention filenames or paths in the rewritten "
            "prompt:\n" + "\n".join(lines)
        )
    shot_facts = []
    if duration_seconds:
        shot_facts.append(f"Duration: {duration_seconds:.1f} seconds.")
    if dialogue.strip():
        shot_facts.append("Dialogue, spoken during this shot: " + dialogue.strip())
    if shot_sound.strip():
        shot_facts.append("Sound: " + shot_sound.strip())
    if shot_facts:
        blocks.append(
            "This shot's length, dialogue and sound, appended to the prompt "
            "separately. Keep the picture consistent with them; do not repeat "
            "them:\n" + "\n".join(shot_facts)
        )
    blocks.append(instruction + "\n" + text.strip())
    return "\n\n".join(blocks)


_PROMPT_LABEL_RE = re.compile(
    r"(Camera Direction & Framing|Clothing / Appearance|Setting|Pose / Action)[^:]*:\s*", re.I
)


def board_outline(board: dict[str, Any], *, words: int = 16) -> str:
    """One short line per shot: number, title, and the opening words of its
    prompt with the section labels stripped. Enough to tell an interior from
    an exterior, or a dialogue scene from an action beat, for a few tokens a
    shot rather than the whole board."""
    names = {c.get("id"): c.get("name") for c in board.get("characters") or []}
    lines = []
    for i, shot in enumerate(board.get("shots") or [], 1):
        gist = _PROMPT_LABEL_RE.sub("", shot.get("prompt") or "").split()
        line = f"{i}. {(shot.get('title') or 'Untitled').strip()}"
        cast = [names[c] for c in shot.get("characterIds") or [] if names.get(c)]
        if cast:
            line += f" [{', '.join(cast)}]"
        if gist:
            line += ": " + " ".join(gist[:words]) + ("…" if len(gist) > words else "")
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
#
# A model call is normally one blocking request: nothing comes back until the
# whole reply (reasoning included) is finished, so a slow local model hits the
# request timeout while it is still working. Inside ``streaming()`` every
# service asks for a streamed response instead. The timeout then applies to
# silence between chunks rather than to the whole generation, and the caller
# sees each chunk as it arrives. Outside it nothing changes, so the callers
# that don't care (rewrites, estimates) are untouched.


class ClientGone(BaseException):
    """The party watching a streamed call went away; stop generating.

    A BaseException on purpose: the callers in between treat a failed model
    call as "fall back and carry on" (``except Exception``), which for a
    reader that has disconnected would just start the next call for nobody.
    """


class _Sink:
    def __init__(self, on_chunk, max_seconds: float | None):
        self.on_chunk = on_chunk
        self.max_seconds = max_seconds
        self.deadline = time.monotonic() + max_seconds if max_seconds else None

    def feed(self, kind: str, text: str) -> None:
        """One chunk of ``"thinking"`` or ``"reply"`` text."""
        if not text:
            return
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RuntimeError(
                f"The model was still generating after {self.max_seconds / 60:.0f} "
                "minutes, so it was stopped. Try a lower thinking level or a "
                "faster model.")
        self.on_chunk(kind, text)


_STREAM: contextvars.ContextVar[_Sink | None] = contextvars.ContextVar(
    "llm_stream", default=None)


@contextmanager
def streaming(on_chunk, *, max_seconds: float | None = None):
    """Stream every model call made inside this block.

    ``on_chunk(kind, text)`` gets each piece as it is generated, *kind* being
    ``"thinking"`` or ``"reply"``. It may raise to abort the generation (the
    connection to the model is closed). *max_seconds* bounds the whole block,
    since the per-call timeout no longer does.
    """
    token = _STREAM.set(_Sink(on_chunk, max_seconds))
    try:
        yield
    finally:
        _STREAM.reset(token)


def _streaming() -> bool:
    return _STREAM.get() is not None


def _strip_think(text: str) -> str:
    """A thinking model on a server that doesn't split its reasoning out leaves
    it inline, ending in </think>; only what follows is the reply."""
    text = text.strip()
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    return text


class LLMService:
    """Base class for a language model this tool can ask for a rewrite."""

    id: str = "base"
    label: str = "Base"
    model: str = ""
    # Optional bearer token (see _apply_key). Sent only when set, so a service
    # that needs none is called exactly as before.
    api_key: str = ""
    key_source: str = ""        # "settings" | "env" | "file" | ""
    key_env: str = ""           # the apiKeyEnv variable name, if any
    needs_key: bool = False     # requiresKey / apiKeyEnv declared
    supports_key: bool = False  # an HTTP service a token could be sent to
    # How hard a thinking model reasons before replying (Settings → Prompt
    # rewriting). "none" switches thinking off, as every request did before.
    thinking: str = "none"

    def health(self) -> tuple[bool, str]:
        return True, ""

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _key_problem(self) -> str:
        if self.needs_key and not self.api_key:
            where = f"set ${self.key_env}, or " if self.key_env else ""
            return (f"{self.label} needs an API key — {where}enter one in "
                    "Settings → Prompt rewriting.")
        return ""

    def _rejected(self, code: int) -> str:
        return (f"{self.label} rejected the API key (HTTP {code}) — check it in "
                "Settings → Prompt rewriting." if self.api_key else
                f"{self.label} requires an API key (HTTP {code}) — enter one in "
                "Settings → Prompt rewriting.")

    def _open(self, req: urllib.request.Request, timeout: float, *,
              explain: bool = True):
        """urlopen, with an auth failure reported as the key problem it is and
        any other HTTP error as the server's own reason, not a bare status.
        ``explain=False`` re-raises the HTTPError for a caller that reads it."""
        try:
            return self._transport(req, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(self._rejected(exc.code)) from exc
            if not explain:
                raise
            raise RuntimeError(self._explain(exc)) from exc

    def _transport(self, req, timeout):
        if self.api_key:
            return urllib.request.build_opener(_NoCredentialRedirect).open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def _explain(self, exc: urllib.error.HTTPError) -> str:
        """What the server said was wrong, in words the user can act on."""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()
        except OSError:
            detail = ""
        if self.api_key:
            detail = detail.replace(self.api_key, "[redacted]")
        # Ollama wraps llama.cpp's JSON error in a JSON string, and
        # OpenAI-style servers nest it under "error"; unwrap down to the text.
        info: dict[str, Any] = {}
        node: Any = detail
        for _ in range(3):
            if isinstance(node, str):
                try:
                    node = json.loads(node)
                except ValueError:
                    break
            if isinstance(node, dict) and "error" in node:
                node = node["error"]
            else:
                break
        if isinstance(node, dict):
            info = node
            detail = str(node.get("message") or node.get("error") or detail)
        elif isinstance(node, str):
            detail = node
        n_prompt, n_ctx = info.get("n_prompt_tokens"), info.get("n_ctx")
        if n_prompt and n_ctx:
            return (
                f"{self.label}: the prompt is {int(n_prompt):,} tokens but this "
                f"service's context window is {int(n_ctx):,}. Raise the Context "
                "window in Settings → Prompt rewriting, or use a shorter board."
            )
        if "context" in detail.lower() and any(
                w in detail.lower() for w in ("exceed", "too long", "maximum")):
            return (
                f"{self.label}: the prompt is longer than the model's context "
                f"window ({detail}). Raise the context window or load the model "
                "with a larger one."
            )
        return f"{self.label} answered HTTP {exc.code}: {detail or exc.reason}"

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        raise NotImplementedError

    def complete_with_image(
        self,
        system: str,
        user: str,
        image: Path,
        *,
        timeout: float = 300.0,
    ) -> str:
        raise RuntimeError(
            f"{self.label} is configured for text completion only. Use a "
            "vision-capable Ollama or OpenAI-compatible model for image-based "
            "character descriptions."
        )

    def complete_with_media(
        self, system: str, user: str, *, images: list[Path] | None = None,
        audio: Path | None = None, timeout: float = 300.0,
    ) -> str:
        """Complete with reference media attached to the user message."""
        if audio is not None:
            raise RuntimeError(
                f"{self.label} does not support audio input for character "
                "descriptions. Configure an OpenAI-compatible multimodal service."
            )
        if images:
            return self.complete_with_image(system, user, images[0], timeout=timeout)
        return self.complete(system, user, timeout=timeout)

    def to_json(self) -> dict[str, Any]:
        ok, msg = self.health()
        return {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "healthy": ok,
            "message": msg,
            # Where the key comes from, never the key itself.
            "supportsKey": self.supports_key,
            "needsKey": self.needs_key,
            "keySource": self.key_source,
            "keyEnv": self.key_env,
        }


class NullLLM(LLMService):
    id = "none"
    label = "None — no prompt rewriting"

    def health(self) -> tuple[bool, str]:
        return False, "No language model is configured for prompt rewriting."

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        raise RuntimeError(self.health()[1])


class BrokenLLM(LLMService):
    def __init__(self, sid: str, label: str, why: str):
        self.id, self.label, self._why = sid, label, why

    def health(self) -> tuple[bool, str]:
        return False, self._why

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        raise RuntimeError(self._why)


class OllamaLLM(LLMService):
    """Ollama's native chat API.

    Health checks that the *configured model* is actually pulled, not merely
    that the port answers — the same lesson the speech engines taught, where a
    server returning 200 on ``/`` while the capability behind it was
    unconfigured showed a false green light.
    """

    def __init__(self, sid: str, label: str, url: str, model: str,
                 num_ctx: int = 8192):
        self.id = sid
        self.label = label
        self.url = (url or "http://localhost:11434").rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    def _think(self) -> bool | str:
        # Ollama takes a level ("low"/"medium"/"high") as well as true/false.
        return False if self.thinking == "none" else self.thinking

    def health(self) -> tuple[bool, str]:
        if not self.model:
            return False, f"{self.id}: no model set in {CONFIG_NAME}"
        if self._key_problem():
            return False, self._key_problem()
        try:
            req = urllib.request.Request(f"{self.url}/api/tags", headers=self._headers())
            with self._transport(req, 4) as r:
                doc = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, self._rejected(e.code)
            return False, f"Ollama at {self.url} answered HTTP {e.code}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            return False, f"cannot reach Ollama at {self.url} ({e})"

        names = {m.get("name", "") for m in doc.get("models") or []}
        if self.model in names:
            return True, ""
        # Ollama reports "name:tag"; accept a bare name that matches one tag.
        if any(n.split(":")[0] == self.model for n in names):
            return True, ""
        return False, (
            f"Ollama at {self.url} does not have {self.model!r}. "
            f"Pull it with: ollama pull {self.model}"
        )

    def _read_chat(self, r) -> str:
        """The reply from an open /api/chat response: one JSON document, or,
        inside ``streaming()``, one JSON line per chunk."""
        sink = _STREAM.get()
        if sink is None:
            doc = json.loads(r.read().decode())
            return ((doc.get("message") or {}).get("content") or "").strip()
        parts: list[str] = []
        for raw in r:
            line = raw.strip()
            if not line:
                continue
            doc = json.loads(line)
            if doc.get("error"):
                raise RuntimeError(f"{self.label}: {doc['error']}")
            message = doc.get("message") or {}
            sink.feed("thinking", message.get("thinking") or "")
            piece = message.get("content") or ""
            if piece:
                parts.append(piece)
                sink.feed("reply", piece)
            if doc.get("done"):
                break
        return "".join(parts).strip()

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        options = {"temperature": 0.7, "top_p": 0.9, "num_ctx": self.num_ctx}
        # The cap counts thinking tokens too; with thinking on it would cut
        # the reasoning off before any reply is written.
        if max_tokens is not None and self.thinking == "none":
            options["num_predict"] = max_tokens
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": _streaming(),
                # A rewrite should be a rewrite, not a reinvention.
                "options": options,
                # Off by default: Qwen3 thinking models otherwise reason at
                # length first. When on, Ollama returns the reasoning in its
                # own "thinking" field, so the content is still the reply.
                "think": self._think(),
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body,
            headers=self._headers(),
        )
        with self._open(req, timeout) as r:
            return self._read_chat(r)

    def complete_with_image(
        self,
        system: str,
        user: str,
        image: Path,
        *,
        timeout: float = 300.0,
    ) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": user,
                        "images": [
                            base64.b64encode(image.read_bytes()).decode("ascii")
                        ],
                    },
                ],
                "stream": _streaming(),
                "options": {"temperature": 0.4, "top_p": 0.9,
                            "num_ctx": self.num_ctx},
                "think": self._think(),
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body,
            headers=self._headers(),
        )
        with self._open(req, timeout) as r:
            return self._read_chat(r)

    def complete_with_media(
        self, system: str, user: str, *, images: list[Path] | None = None,
        audio: Path | None = None, timeout: float = 300.0,
    ) -> str:
        if audio is not None:
            raise RuntimeError(
                f"{self.label} cannot receive voice clips. Configure an "
                "OpenAI-compatible multimodal service for character rewrite."
            )
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user, "images": [
                    base64.b64encode(p.read_bytes()).decode("ascii")
                    for p in (images or [])
                ]},
            ],
            "stream": _streaming(),
            "options": {"temperature": 0.4, "top_p": 0.9,
                        "num_ctx": self.num_ctx},
            "think": self._think(),
        }).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body,
            headers=self._headers(),
        )
        with self._open(req, timeout) as r:
            return self._read_chat(r)


def _reply(doc: dict[str, Any]) -> str:
    """The reply text of a chat-completions response."""
    choices = doc.get("choices") or [{}]
    return _strip_think((choices[0].get("message") or {}).get("content") or "")


THINKING_LEVELS = ("none", "low", "medium", "high")


# Service types offered in Settings. Every kind but "ollama" and "anthropic"
# speaks OpenAI's chat-completions API; the hosted ones differ only in their
# default URL and the request fields they reject (OpenAICompatLLM._dumps).
LOCAL_OPENAI_KINDS = ("openai", "openai-compatible", "lmstudio")
CLOUD_KINDS = ("openai-api", "gemini", "openrouter", "groq", "mistral")
KNOWN_KINDS = ("ollama", "anthropic", "splash", "fable", *LOCAL_OPENAI_KINDS, *CLOUD_KINDS)
KIND_DEFAULT_URLS = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234",
    "openai-api": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
}


class OpenAICompatLLM(LLMService):
    """Anything exposing ``/v1/chat/completions`` — llama.cpp, vLLM, LM Studio,
    and the hosted APIs that copy OpenAI's (OpenAI, Gemini, OpenRouter, Groq,
    Mistral)."""

    def __init__(self, sid: str, label: str, url: str, model: str,
                 kind: str = "openai"):
        self.id = sid
        self.label = label
        self.kind = kind
        self.url = (url or KIND_DEFAULT_URLS.get(kind, "")).rstrip("/")
        self.model = model
        # A URL that already names its API version (".../v1", Gemini's
        # ".../v1beta/openai") is used as is; a bare host gets "/v1".
        self.api = (self.url if re.search(r"/v\d+[a-z]*(/openai)?$", self.url)
                    else f"{self.url}/v1")

    def _dumps(self, body: dict[str, Any]) -> str:
        """JSON for a chat request, trimmed for hosted APIs: they reject the
        local-server thinking switch, and several of their models accept only
        the default temperature."""
        # Qwen3's hybrid thinking mode otherwise runs in full before the
        # actual reply -- burning thousands of unseen tokens and, on a local
        # model, minutes. enable_thinking is the vLLM/SGLang/llama.cpp server
        # convention for that switch, reasoning_effort OpenAI's for its level;
        # a server that doesn't recognize either ignores the unknown field.
        body["chat_template_kwargs"] = {"enable_thinking": self.thinking != "none"}
        if self.thinking != "none":
            body["reasoning_effort"] = self.thinking
            # max_tokens counts the reasoning too; keep the reply from being
            # cut off before it starts.
            body.pop("max_tokens", None)
        if self.kind in CLOUD_KINDS:
            body = {k: v for k, v in body.items()
                    if k not in ("chat_template_kwargs", "temperature")}
            if self.kind == "openai-api" and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")
        return json.dumps(body)

    def health(self) -> tuple[bool, str]:
        if not self.url:
            return False, f"{self.id}: no url set in {CONFIG_NAME}"
        if not self.model:
            return False, f"{self.id}: no model set in {CONFIG_NAME}"
        if self._key_problem():
            return False, self._key_problem()
        try:
            req = urllib.request.Request(f"{self.api}/models",
                                         headers=self._headers())
            with self._transport(req, 4) as r:
                doc = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, self._rejected(e.code)
            return False, f"{self.url} answered HTTP {e.code}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            return False, f"cannot reach {self.url} ({e})"
        ids = {m.get("id") for m in doc.get("data") or []}
        # Gemini lists its models as "models/<name>".
        if ids and self.model not in ids and f"models/{self.model}" not in ids:
            return False, f"{self.url} does not serve {self.model!r}"
        return True, ""

    def _read_chat(self, r) -> str:
        """The reply from an open chat-completions response: one JSON
        document, or, inside ``streaming()``, server-sent events."""
        sink = _STREAM.get()
        if sink is None:
            return _reply(json.loads(r.read().decode()))
        parts: list[str] = []
        inline_think = None  # None: undecided, True: inside <think>, False: past it
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                doc = json.loads(data)
            except ValueError:
                continue
            error = doc.get("error")
            if error:
                raise RuntimeError(f"{self.label}: " + str(
                    error.get("message") if isinstance(error, dict) else error))
            for choice in doc.get("choices") or []:
                delta = choice.get("delta") or {}
                # llama.cpp / vLLM / DeepSeek name the reasoning field
                # reasoning_content; OpenRouter and others, reasoning.
                sink.feed("thinking", delta.get("reasoning_content")
                          or delta.get("reasoning") or "")
                piece = delta.get("content") or ""
                if not piece:
                    continue
                parts.append(piece)
                # A server that doesn't split reasoning out leaves it inline,
                # between <think> and </think>; it is still thinking.
                if inline_think is None:
                    head = "".join(parts).lstrip()
                    if head.startswith("<think>"):
                        inline_think = True
                    elif len(head) >= len("<think>") or not "<think>".startswith(head):
                        inline_think = False
                # The chunk that closes the block is still part of it.
                sink.feed("thinking" if inline_think else "reply", piece)
                if inline_think and "</think>" in "".join(parts[-3:]):
                    inline_think = False
        return _strip_think("".join(parts))

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        body_dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
            "stream": _streaming(),
        }
        if max_tokens is not None:
            body_dict["max_tokens"] = max_tokens
        body = self._dumps(body_dict).encode()
        req = urllib.request.Request(f"{self.api}/chat/completions",
                                     data=body, headers=self._headers())
        with self._open(req, timeout) as r:
            return self._read_chat(r)

    def complete_with_image(
        self,
        system: str,
        user: str,
        image: Path,
        *,
        timeout: float = 300.0,
    ) -> str:
        mime = mimetypes.guess_type(str(image))[0] or "application/octet-stream"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        body = self._dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{encoded}"
                                },
                            },
                        ],
                    },
                ],
                "temperature": 0.4,
                "stream": _streaming(),
            }
        ).encode()
        req = urllib.request.Request(f"{self.api}/chat/completions",
                                     data=body, headers=self._headers())
        with self._open(req, timeout) as r:
            return self._read_chat(r)

    def complete_with_media(
        self, system: str, user: str, *, images: list[Path] | None = None,
        audio: Path | None = None, timeout: float = 300.0,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for image in images or []:
            mime = mimetypes.guess_type(str(image))[0] or "application/octet-stream"
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{encoded}"
            }})
        if audio is not None:
            mime = mimetypes.guess_type(str(audio))[0] or "audio/wav"
            fmt = mime.split("/", 1)[-1].split(";", 1)[0]
            if fmt == "mpeg":
                fmt = "mp3"
            content.append({"type": "input_audio", "input_audio": {
                "data": base64.b64encode(audio.read_bytes()).decode("ascii"),
                "format": fmt,
            }})
        body = self._dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
            "temperature": 0.4, "stream": _streaming(),
        }).encode()
        req = urllib.request.Request(f"{self.api}/chat/completions",
                                     data=body, headers=self._headers())
        try:
            with self._open(req, timeout, explain=False) as r:
                text = self._read_chat(r)
        except urllib.error.HTTPError as exc:
            # LM Studio and several otherwise OpenAI-compatible servers accept
            # image_url blocks but not OpenAI's input_audio block. Character
            # cards commonly have both a portrait and a voice attached, so an
            # unsupported voice must not prevent the portrait description.
            # Retry only for the server's explicit media-type rejection; other
            # 400s still surface instead of being disguised by a second call.
            detail = exc.read().decode("utf-8", "replace")
            unsupported_audio = (
                audio is not None
                and exc.code == 400
                and (
                    "input_audio" in detail
                    or (
                        "content" in detail
                        and "text" in detail
                        and "image_url" in detail
                        and "type" in detail
                    )
                )
            )
            if unsupported_audio:
                return self.complete_with_media(
                    system, user, images=images, audio=None, timeout=timeout
                )
            raise RuntimeError(
                f"{self.label} rejected the multimodal request "
                f"(HTTP {exc.code}): {detail or exc.reason}"
            ) from exc
        return text


class AnthropicLLM(LLMService):
    """Claude, through the official ``anthropic`` SDK. The SDK is imported
    only when a service of this kind is used, so installs without Claude
    services don't need it."""

    DEFAULT_MAX_TOKENS = 16000

    def __init__(self, sid: str, label: str, url: str, model: str):
        self.id = sid
        self.label = label
        self.url = (url or KIND_DEFAULT_URLS["anthropic"]).rstrip("/")
        self.model = model

    def _client(self, timeout: float, retries: int = 2):
        import anthropic  # optional dependency: pip install anthropic
        import httpx  # provided by the optional SDK
        kwargs: dict[str, Any] = {"api_key": self.api_key or None,
                                  "timeout": timeout, "max_retries": retries,
                                  "http_client": httpx.Client(follow_redirects=False)}
        if self.url != KIND_DEFAULT_URLS["anthropic"]:
            kwargs["base_url"] = self.url
        return anthropic, anthropic.Anthropic(**kwargs)

    def health(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, self._rejected(401)
        if not self.model:
            return False, f"{self.id}: no model set in {CONFIG_NAME}"
        if self._key_problem():
            return False, self._key_problem()
        try:
            anthropic, client = self._client(timeout=4.0, retries=0)
        except ImportError:
            return False, ("Claude needs the anthropic Python package — run "
                           "`pip install anthropic` for the Python that runs "
                           "Storyboard, then restart it.")
        try:
            client.models.retrieve(self.model)
        except anthropic.NotFoundError:
            return False, f"Anthropic has no model {self.model!r}"
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            return False, self._rejected(exc.status_code)
        except anthropic.APIConnectionError as exc:
            return False, f"cannot reach {self.url} ({exc})"
        except anthropic.APIStatusError as exc:
            return False, f"{self.url} answered HTTP {exc.status_code}"
        return True, ""

    def _create(self, system: str, content: Any, timeout: float,
                max_tokens: int | None) -> str:
        anthropic, client = self._client(timeout=timeout)
        extra: dict[str, Any] = {}
        if self.thinking != "none":
            # Claude's thinking depth is output_config.effort. Sent as raw body
            # so it works whatever SDK version is installed. Thinking tokens
            # count against max_tokens, so a caller's cap would cut the reasoning
            # off before any reply: use the full allowance instead.
            extra["extra_body"] = {"output_config": {"effort": self.thinking}}
            max_tokens = None
        request = dict(
            model=self.model,
            max_tokens=max_tokens or self.DEFAULT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            **extra,
        )
        sink = _STREAM.get()
        try:
            if sink is None:
                response = client.messages.create(**request)
            else:
                with client.messages.stream(**request) as stream:
                    for event in stream:
                        if event.type != "content_block_delta":
                            continue
                        delta = event.delta
                        if delta.type == "text_delta":
                            sink.feed("reply", delta.text)
                        elif delta.type == "thinking_delta":
                            sink.feed("thinking", delta.thinking)
                    response = stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise RuntimeError(self._rejected(exc.status_code)) from exc
        if response.stop_reason == "refusal":
            raise RuntimeError(f"{self.label} declined this request.")
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def complete(self, system: str, user: str, *, timeout: float = 300.0,
                 max_tokens: int | None = None) -> str:
        return self._create(system, user, timeout, max_tokens)

    def complete_with_image(self, system: str, user: str, image: Path, *,
                            timeout: float = 300.0) -> str:
        return self.complete_with_media(system, user, images=[image], timeout=timeout)

    def complete_with_media(
        self, system: str, user: str, *, images: list[Path] | None = None,
        audio: Path | None = None, timeout: float = 300.0,
    ) -> str:
        if audio is not None and not images:
            return super().complete_with_media(system, user, audio=audio, timeout=timeout)
        # Claude reads images but not audio; a voice sample is left out rather
        # than blocking the portrait description.
        content: list[dict[str, Any]] = []
        for image in images or []:
            mime = mimetypes.guess_type(str(image))[0] or "image/png"
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": mime,
                "data": base64.standard_b64encode(image.read_bytes()).decode("utf-8"),
            }})
        content.append({"type": "text", "text": user})
        return self._create(system, content, timeout, None)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def load_config(project_root: Path) -> list[dict[str, Any]]:
    """Read this machine's ``llm-services.json``, first creating it from the
    committed ``llm-services-sample.json`` if it is absent."""
    path = ensure_local_copy(project_root, CONFIG_NAME, {"services": DEFAULT_SERVICES})
    if not path.exists():
        return list(DEFAULT_SERVICES)
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return [{"id": "config-error", "kind": "broken",
                 "label": f"unreadable {CONFIG_NAME}", "why": str(e)}]
    services = doc.get("services")
    return services if isinstance(services, list) else list(DEFAULT_SERVICES)


# Machine-local settings (gitignored); "llmKeys" maps a service id to a key
# entered in Settings, so a key never has to live in the tracked config.
LOCAL_SETTINGS_NAME = "server-config.json"


def local_keys(project_root: Path) -> dict[str, str]:
    path = Path(project_root) / LOCAL_SETTINGS_NAME
    try:
        keys = json.loads(path.read_text()).get("llmKeys")
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    return {str(k): str(v) for k, v in keys.items() if v} if isinstance(keys, dict) else {}


def _apply_key(svc: LLMService, entry: dict[str, Any], saved: str) -> LLMService:
    """Settings key, else $apiKeyEnv, else an inline apiKey, else none.

    A service only *needs* one when its entry says so (requiresKey, or an
    apiKeyEnv to read it from); otherwise no key means none is sent.
    """
    env_name = str(entry.get("apiKeyEnv") or "")
    svc.supports_key = True
    svc.key_env = env_name
    svc.needs_key = bool(entry.get("requiresKey")) or bool(env_name)
    if saved:
        svc.api_key, svc.key_source = saved, "settings"
    elif env_name and os.environ.get(env_name):
        svc.api_key, svc.key_source = os.environ[env_name], "env"
    elif entry.get("apiKey"):
        svc.api_key, svc.key_source = str(entry["apiKey"]), "file"
    return svc


def build_one(entry: dict[str, Any], saved_key: str = "") -> LLMService:
    kind = entry.get("kind") or entry.get("id")
    sid = str(entry.get("id") or kind or "unnamed")
    label = str(entry.get("label") or sid)
    url = str(entry.get("url") or "")
    model = str(entry.get("model") or "")
    thinking = str(entry.get("thinking") or "none")
    if thinking not in THINKING_LEVELS:
        thinking = "none"

    if kind == "fable":
        from .fable import FableLLM, MODEL, URL
        svc = FableLLM(sid, label, url or URL, model or MODEL)
        svc.thinking = thinking
        return svc
    if kind == "splash":
        from .splash import SplashLLM
        svc = SplashLLM(sid, label, url or "http://127.0.0.1:8001", model)
        svc.thinking = thinking
        return svc
    if kind == "ollama":
        svc: LLMService = OllamaLLM(sid, label, url, model,
                                    num_ctx=int(entry.get("numCtx") or 8192))
        svc.thinking = thinking
        return _apply_key(svc, entry, saved_key)
    if kind in LOCAL_OPENAI_KINDS or kind in CLOUD_KINDS:
        svc = OpenAICompatLLM(sid, label, url, model, kind=kind)
        svc.thinking = thinking
        return _apply_key(svc, entry, saved_key)
    if kind == "anthropic":
        svc = AnthropicLLM(sid, label, url, model)
        svc.thinking = thinking
        return _apply_key(svc, entry, saved_key)
    if kind == "broken":
        return BrokenLLM(sid, label, str(entry.get("why") or "misconfigured"))
    return BrokenLLM(
        sid, label,
        f"unknown service kind {kind!r} in {CONFIG_NAME} "
        f"(expected one of: {', '.join(KNOWN_KINDS)})",
    )


def load_services(project_root: Path) -> dict[str, LLMService]:
    services: dict[str, LLMService] = {}
    saved = local_keys(project_root)
    for entry in load_config(project_root):
        s = build_one(entry, saved.get(str(entry.get("id") or "")) or "")
        if "vision" in entry:s.vision = entry["vision"] is True
        services[s.id] = s
    services["none"] = NullLLM()
    return services


# --------------------------------------------------------------------------- #
# The rewrite itself
# --------------------------------------------------------------------------- #


#: What is being rewritten, and how to talk about it — the system prompt to
#: use, the label for error/instruction text, and the instruction line handed
#: to the model as the last block of `build_user_message`.
_REWRITE_KINDS: dict[str, dict[str, str]] = {
    "shot": {
        "system": SYSTEM_PROMPT,
        "label": "shot prompt",
        "instruction": "Rewrite this shot description:",
    },
    "still": {
        "system": STILL_SYSTEM_PROMPT,
        "label": "shot prompt",
        "instruction": "Rewrite this shot description:",
    },
    "scene": {
        "system": SCENE_SYSTEM_PROMPT,
        "label": "scene description",
        "instruction": "Rewrite this project's scene description:",
    },
    "renderStyle": {
        "system": RENDER_STYLE_SYSTEM_PROMPT,
        "label": "render style",
        "instruction": "Rewrite this project's render style:",
    },
    "soundscape": {
        "system": SOUNDSCAPE_SYSTEM_PROMPT,
        "label": "background sound effects",
        "instruction": "Rewrite this project's background sound effects:",
    },
    "soundtrack": {
        "system": SOUNDTRACK_SYSTEM_PROMPT,
        "label": "soundtrack prompt",
        "instruction": "Rewrite this project's soundtrack music prompt:",
    },
    "soundNote": {
        "system": SOUND_ACCENT_SYSTEM_PROMPT,
        "label": "sound accents",
        "instruction": "Rewrite this shot's sound accents:",
    },
}


def rewrite_prompt(
    service: LLMService,
    text: str,
    *,
    scene: str = "",
    soundscape: str = "",
    context: str = "",
    context_label: str = "",
    previous_shot: dict[str, str] | None = None,
    next_shot: dict[str, str] | None = None,
    characters: list[dict[str, Any]] | None = None,
    reference_images: list[dict[str, str]] | None = None,
    reference_files: list[Path] | None = None,
    outline: str = "",
    dialogue: str = "",
    duration_seconds: float | None = None,
    shot_sound: str = "",
    kind: str = "shot",
) -> str:
    """Ask *service* to restyle *text*. Returns the proposal, never applies it.

    *kind* picks both the system prompt and how the text is described to the
    model and in error messages — "shot" or "still" for a per-shot prompt,
    "scene" for the project's scene description, "renderStyle" for its look
    (medium, lens, light, grade), "soundscape" for its
    background sound bed, "soundNote" for one shot's own sound accents.

    *previous_shot*/*next_shot* are only meaningful for "shot"/"still" — see
    build_user_message.
    """
    spec = _REWRITE_KINDS.get(kind, _REWRITE_KINDS["shot"])
    text = (text or "").strip()
    if not text:
        raise ValueError(f"There is nothing in the {spec['label']} to rewrite yet.")

    ok, msg = service.health()
    if not ok:
        raise RuntimeError(msg)

    out = service.complete_with_media(
        spec["system"],
        build_user_message(
            text,
            scene=scene,
            soundscape=soundscape,
            context=context,
            context_label=context_label,
            previous_shot=previous_shot,
            next_shot=next_shot,
            characters=characters,
            reference_images=reference_images,
            outline=outline,
            dialogue=dialogue,
            duration_seconds=duration_seconds,
            shot_sound=shot_sound,
            instruction=spec["instruction"],
        ),
        images=reference_files,
    )
    out = _strip_wrapping(out)
    if not out:
        raise RuntimeError(
            f"{service.label} returned an empty rewrite. It may have run out "
            "of context, or be a model that only emits reasoning."
        )
    return out


def rewrite_dialogue(
    service: LLMService,
    text: str,
    *,
    speaker: dict[str, Any],
    shot_prompt: str = "",
    scene: str = "",
    dialogue_style: str = "",
    duration_seconds: float | None = None,
    research: str = "",
    neighbor_lines: list[str] | None = None,
) -> str:
    """Rewrite one line in its selected speaker's voice, as a proposal.

    *neighbor_lines* are the lines spoken in the shots either side, already
    labelled ("Before — Sam: ..."), so the rewrite answers what was just said
    and sets up what comes next instead of reading as a line on its own.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("There is nothing in the dialogue to rewrite yet.")
    name = str(speaker.get("name") or "").strip()
    if not name:
        raise ValueError("Choose a speaking character before rewriting dialogue.")
    ok, msg = service.health()
    if not ok:
        raise RuntimeError(msg)

    blocks = [
        f"SELECTED SPEAKER: {name}",
        "CHARACTER CARD:\n" + (
            str(speaker.get("description") or "").strip()
            or "No character description is available."
        ),
    ]
    if scene.strip():
        blocks.append("PROJECT SCENE:\n" + scene.strip())
    if shot_prompt.strip():
        blocks.append("THIS SHOT:\n" + shot_prompt.strip())
    if dialogue_style.strip():
        blocks.append("DELIVERY DIRECTION:\n" + dialogue_style.strip())
    if duration_seconds:
        blocks.append(f"SHOT DURATION: {duration_seconds:.2f} seconds")
    if neighbor_lines:
        blocks.append("SURROUNDING DIALOGUE (context only; do not repeat):\n"
                      + "\n".join(neighbor_lines))
    blocks.append(
        "WEB RESEARCH RESULTS:\n" + (
            research.strip()
            or "No reliable external result was found; rely on the character card."
        )
    )
    blocks.append("DIALOGUE TO REWRITE:\n" + text)

    out = _strip_wrapping(service.complete(
        DIALOGUE_SYSTEM_PROMPT, "\n\n".join(blocks), timeout=300.0
    ))
    # Models occasionally retain the requested quotation marks despite the
    # output rule. Only strip a single pair enclosing the whole response.
    if len(out) >= 2 and out[0] == out[-1] and out[0] in {'"', "'"}:
        out = out[1:-1].strip()
    if not out:
        raise RuntimeError(f"{service.label} returned an empty dialogue rewrite.")
    return out


def describe_character(
    service: LLMService,
    image: Path,
    *,
    voice: Path | None = None,
    name: str = "",
    current: str = "",
) -> str:
    """Ask *service* to describe a character from a reference image."""
    ok, msg = service.health()
    if not ok:
        raise RuntimeError(msg)
    if not image.is_file():
        raise FileNotFoundError(f"no such reference image: {image}")
    if voice is not None and not voice.is_file():
        raise FileNotFoundError(f"no such reference voice: {voice}")

    blocks = [
        "Write or improve the character description from the attached portrait "
        "and voice clip. Describe the character, never the scene."
    ]
    if name.strip():
        blocks.append(f"Character label: {name.strip()}")
    if current.strip():
        blocks.append(
            "Current description to preserve where accurate:\n" + current.strip()
        )

    out = service.complete_with_media(
        CHARACTER_EXTRACTION_SYSTEM_PROMPT,
        "\n\n".join(blocks),
        images=[image],
        audio=voice,
    )
    result = _parse_character_result(out)
    if not result["character"]:
        raise RuntimeError(
            f"{service.label} returned an empty character description."
        )
    return result


def _parse_character_result(text: str) -> dict[str, str]:
    """Split a character/environment extraction, with legacy fallback."""
    raw = _strip_wrapping(text)
    if not raw:
        return {"character": "", "environment": ""}

    candidates = [raw]
    if "{" in raw and "}" in raw:
        start, end = raw.find("{"), raw.rfind("}")
        if start < end:
            candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            doc = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        character = doc.get("character") or doc.get("CHARACTER") or doc.get("Character") or ""
        environment = doc.get("environment") or doc.get("ENVIRONMENT") or doc.get("Environment") or ""
        if isinstance(character, dict):
            character = character.get("summary") or ""
        if isinstance(environment, dict):
            environment = environment.get("summary") or ""
        return {
            "character": _strip_wrapping(str(character)),
            "environment": _strip_wrapping(str(environment)),
        }

    matches = list(re.finditer(
        r"(?im)^\s*(?:[*#]+\s*)?(CHARACTER|ENVIRONMENT)\s*:\s*", raw
    ))
    char_match = next((m for m in matches if m.group(1).upper() == "CHARACTER"), None)
    env_match = next((m for m in matches if m.group(1).upper() == "ENVIRONMENT"), None)
    if char_match and env_match and char_match.start() < env_match.start():
        return {
            "character": _strip_wrapping(raw[char_match.end():env_match.start()]),
            "environment": _strip_wrapping(raw[env_match.end():]),
        }

    # Older or non-compliant models may still return one plain paragraph.
    return {"character": raw, "environment": ""}


# A model that still answers with an END: line has it cut off here.
_STILL_PHASE_RE = re.compile(r"START:\s*(?P<start>.+?)\s*(?:\bEND:|$)", re.S | re.I)


def describe_still_phases(service: LLMService, shot_prompt: str) -> dict[str, str]:
    """A short visual description of the opening instant *shot_prompt*
    establishes, for "Create Stills" to render as its one still -- a model
    has no sense of time, so "the start of this shot" alone means nothing.

    Best-effort: an unconfigured/unhealthy service, a network error, or a
    reply that does not parse all come back as an empty dict rather than a
    raised error, since the caller's fallback (a generic phase label glued
    onto the shot's own prompt) is a fine second choice, not a failure.
    """
    text = (shot_prompt or "").strip()
    if not text:
        return {}
    try:
        ok, _msg = service.health()
        if not ok:
            return {}
        raw = service.complete(STILL_PHASES_SYSTEM_PROMPT, text, timeout=45.0)
    except Exception:  # noqa: BLE001 - a missing phase description is fine
        return {}
    m = _STILL_PHASE_RE.search(raw or "")
    if not m:
        return {}
    start = m.group("start").strip()
    return {"start": start} if start else {}


def _strip_wrapping(text: str) -> str:
    """Undo the packaging a chat model adds even when told not to.

    Small local models reliably do two of these: wrap the answer in quotes, or
    lead with "Here is the rewritten prompt:". Both would end up inside the
    prompt that reaches the video model.
    """
    t = (text or "").strip()

    # Thinking models that ignore think:false leave the reasoning inline.
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1].strip()

    # A fenced block, sometimes with a language tag.
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1]
            if "\n" in t:
                first, rest = t.split("\n", 1)
                if len(first.split()) <= 1:
                    t = rest
            t = t.strip()

    for lead in (
        "here is the rewritten prompt:", "here's the rewritten prompt:",
        "rewritten prompt:", "here is the prompt:", "here's the prompt:",
        "prompt:",
    ):
        if t.lower().startswith(lead):
            t = t[len(lead):].strip()
            break

    if len(t) >= 2 and t[0] in "\"“'" and t[-1] in "\"”'":
        t = t[1:-1].strip()

    return t
