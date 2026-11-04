"""Native presentation and execution policy for read-only editorial tools."""
from __future__ import annotations

from director_contract import READ_ONLY_TOOLS


EDITORIAL_TOOLS = {
    "inspect_cut": ("cut", "Klipp"),
    "inspect_coverage": ("coverage", "Romkart"),
    "inspect_structure": ("structure", "Struktur"),
    "inspect_sound": ("sound", "Lydspor"),
}


def inspection_result(tool, context):
    section, title = EDITORIAL_TOOLS[tool]
    data = context[section]
    status = data.get("status")
    if status == "ok":
        text = f"{title} er lest fra lagret revisjon {data['revision']}."
    elif status == "absent":
        text = f"{title} har ingen lagrede valg ennå. Åpne arbeidsflaten for å begynne."
    elif status == "truncated":
        text = f"{title} er bare delvis lest. Åpne arbeidsflaten for hele oversikten."
    else:
        text = f"{title} kunne ikke leses sikkert. Åpne arbeidsflaten og kontroller dokumentet."
    return {"tool": tool, "editorial": data, "panel": section, "statusText": text}
