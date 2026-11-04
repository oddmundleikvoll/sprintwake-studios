"""Native presentation and execution policy for read-only editorial tools."""
from __future__ import annotations

from director_contract import READ_ONLY_TOOLS


EDITORIAL_TOOLS = {
    "inspect_cut": ("cut", "Cut"),
    "inspect_coverage": ("coverage", "Room map"),
    "inspect_structure": ("structure", "Structure"),
    "inspect_sound": ("sound", "Sound"),
}


def inspection_result(tool, context):
    section, title = EDITORIAL_TOOLS[tool]
    data = context[section]
    status = data.get("status")
    if status == "ok":
        text = f"{title} read from saved revision {data['revision']}."
    elif status == "absent":
        text = f"{title} has no saved choices yet. Open the workspace to begin."
    elif status == "truncated":
        text = f"{title} is only partially read. Open the workspace for the full overview."
    else:
        text = f"{title} could not be read safely. Open the workspace and check the document."
    return {"tool": tool, "editorial": data, "panel": section, "statusText": text}
