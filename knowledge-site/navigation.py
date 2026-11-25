"""Read the optional library outline without exposing inaccessible notes."""

import json
from pathlib import Path


def visible_navigation(path, notes):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            return []
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return []
    if not isinstance(document, dict) or not isinstance(document.get("roots"), list):
        return []
    available = {note["id"]: note for note in notes}
    seen = set()

    def visit(nodes, depth=0):
        if depth >= 12 or not isinstance(nodes, list):
            return []
        output = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            note_id = node.get("id")
            is_note = "id" in node
            allowed = isinstance(note_id, str) and note_id in available and note_id not in seen
            if allowed:
                seen.add(note_id)
            children = visit(node.get("children", []), depth + 1)
            label = node.get("label")
            if not isinstance(label, str) or not label.strip():
                label = available[note_id]["title"] if allowed else None
            if is_note:
                if allowed:
                    output.append({"id": note_id, "label": label, "children": children})
                else:
                    # Never retain an unauthorized/deleted parent's label.
                    output.extend(children)
            elif children and label:
                output.append({"label": label, "children": children})
            else:
                output.extend(children)
        return output

    return visit(document["roots"])
