"""Reference-conditioned scene frames, cached independently of video takes."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .ltx_media import atomic_json, digest, file_hash
from .film_comfy import QWEN_MODELS


AUTO_END_ROLE = "film-auto-end"
AUTO_START_ROLE = "film-auto-start"
END_STRENGTH = .65


def automatic_end(anchor):
    """Moved or strength-edited generated anchors become manual choices."""
    return (anchor.get("ref", {}).get("role") == AUTO_END_ROLE
            and anchor.get("frame") == "last" and anchor.get("strength") == anchor.get("ref", {}).get("autoStrength", END_STRENGTH))


def frame_prompt(style, scene, card, characters, *, closing=False):
    cast = [c for c in characters if c["key"] in scene["characterKeys"]]
    parts = ["Create one complete cinematic frame, a single view without panels, portrait borders or a collage.",
             "Use each named character reference for that character's identity, face, body design, materials, palette and costume. "
             "Keep the characters distinct. Reference poses and portrait backgrounds do not dictate the scene composition.",
             style, scene["setting"]]
    parts.extend(f"{c['name']}: {c['description']}" for c in cast)
    if scene.get("assetStates"):
        parts.append("Recurring set/prop states: " + json.dumps(scene["assetStates"], ensure_ascii=False))
    prop_descriptions = scene.get("propDescriptions") or {}
    if prop_descriptions:
        parts.append("Recurring props: " + "; ".join(
            f"{key}: {value}" for key, value in sorted(prop_descriptions.items())
            if isinstance(key, str) and isinstance(value, str)))
    visible = ([c for c in cast if c.get("id") in card["characterIds"]]
               if "characterIds" in card else cast)
    parts.append("Visible cast in this frame: " + (", ".join(c["name"] for c in visible) or "none; environment only") +
                 ". All other character references remain off screen.")
    if closing:
        parts.append("The scene reference establishes the environment, light and visual style. "
                     "Stage the final instant AFTER the clip's action, with physically reachable poses and the same character designs.")
        if card.get("end"):
            parts.append("Starting state: " + (card.get("start") or "the established opening"))
            parts.append("Action: " + (card.get("action") or "the established action"))
            if card.get("stateAfter"):
                parts.append("State after the action: " + card["stateAfter"])
            parts.append("Show this final state: " + card["end"])
        else:
            parts.extend(["Clip instructions: " + card["start"],
                          "Show the completed main action, rather than its preparation. Use the clip's requested framing."])
    else:
        parts.extend([scene.get("imagePrompt") or "", "Show the opening instant BEFORE the clip's action.",
                      card.get("stateBefore") and "State before action: " + card["stateBefore"] or "",
                      card.get("start") or card.get("action") or ""])
    parts.append(card.get("camera") or "")
    parts.append("Only characters requested on screen appear; off-screen cast references establish identity, not extra figures. No lettering or captions.")
    return "\n".join(part for part in parts if part)


def reference_frame(client, prompt, references, directory, *, seed, cancel, width=768, height=512):
    """Reuse only a decoded image whose receipt still matches all inputs."""
    from PIL import Image
    if cancel():
        raise RuntimeError("Filmjobben ble stoppet")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    recipe = {"version": 2, "models": QWEN_MODELS, "modelFiles": client.qwen_model_signature(), "width": width, "height": height,
              "steps": 8, "referenceResolution": 768, "seed": seed, "prompt": prompt,
              "references": [{"hash": file_hash(path), "label": label} for path, label in references],
              "code": [file_hash(Path(__file__)), file_hash(Path(__file__).with_name("film_comfy.py"))]}
    signature = digest(recipe)
    image_path = directory / f"{signature}.png"
    receipt_path = image_path.with_suffix(".json")
    if image_path.is_file() and receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text())
            if isinstance(receipt, dict) and receipt.get("signature") == signature and receipt.get("hash") == file_hash(image_path):
                with Image.open(image_path) as image:
                    if image.size != (width, height):
                        raise ValueError("Feil størrelse på scenebildet")
                    image.load()
                return image_path
        except (OSError, ValueError):
            pass
    temporary = directory / f".{uuid.uuid4().hex}.png"
    try:
        client.qwen_image(prompt, temporary, references, width=width, height=height, seed=seed, cancel=cancel)
        with Image.open(temporary) as image:
            if image.size != (width, height):
                raise ValueError("Qwen returnerte feil størrelse på scenebildet")
            image.load()
        if cancel():
            raise RuntimeError("Filmjobben ble stoppet")
        temporary.replace(image_path)
        atomic_json(receipt_path, {"signature": signature, "hash": file_hash(image_path), "recipe": recipe})
        return image_path
    finally:
        temporary.unlink(missing_ok=True)


CHARACTER_CARD_TEMPLATE = (
    "Character reference card for ONE character: {who}. Four full-body views of the SAME character, "
    "left to right: front, three-quarter with both eyes visible, pure side profile, and back. "
    "Keep face, body proportions, materials, palette, clothing and equipment identical across all views. "
    "Neutral standing poses, plain neutral studio background, even light, complete feet and head visible, "
    "consistent scale and clear spacing. Use any provided reference for identity; invent unseen back details "
    "only as needed to complete the design. No other characters, captions or lettering."
)


def character_card_prompt(style, name, description):
    who = f"{name}: {description.rstrip(' .')}" if name else description.rstrip(' .')
    return (style.strip() + "\n" + CHARACTER_CARD_TEMPLATE.format(who=who)).strip()


def character_card_signature(style, name, description, models):
    return digest({"prompt": character_card_prompt(style, name, description), "models": models})
