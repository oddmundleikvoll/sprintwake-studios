"""Persistent director conversations for Raven.

The store is deliberately independent of the UI and model runtime.  It keeps
conversation history as JSON files in Raven's managed workspace and provides a
small, safe API for the director workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from typing import Any


_UNSET = object()
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class ConversationStore:
    """Store sessions below ``workspace/System/Conversations``.

    ``workspace`` only needs ``root``, ``system_dir`` and ``projects_dir``
    attributes, which keeps this class straightforward to use in tests and in
    alternate Raven hosts.
    """

    def __init__(self, workspace: Any):
        self.workspace = workspace
        self.root = Path(workspace.root).expanduser()
        self.system_dir = Path(workspace.system_dir)
        self.conversations_dir = self.system_dir / "Conversations"
        self._ensure_directory(self.conversations_dir)

    def _assert_safe(self, path: Path) -> None:
        path = Path(path)
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Sti utenfor arbeidsområdet") from exc
        current = self.root
        for part in ("", *relative.parts):
            current = self.root if not part else current / part
            if current.is_symlink():
                raise ValueError(f"Symlink er ikke tillatt i arbeidsområdet: {current}")

    def _ensure_directory(self, path: Path) -> None:
        self._assert_safe(path)
        path.mkdir(parents=True, exist_ok=True)
        self._assert_safe(path)

    @staticmethod
    def _validate_id(identifier: str) -> str:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            raise ValueError("Ugyldig samtale-ID")
        return identifier

    @staticmethod
    def _validate_slug(slug: str | None) -> str | None:
        if slug is None:
            return None
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            raise ValueError("Ugyldig prosjekt-ID")
        return slug

    def _path(self, identifier: str) -> Path:
        identifier = self._validate_id(identifier)
        path = self.conversations_dir / f"{identifier}.json"
        self._assert_safe(path)
        return path

    @staticmethod
    def _stamp() -> float:
        return time.time()

    def _write(self, path: Path, session: dict[str, Any]) -> None:
        self._assert_safe(path.parent)
        payload = json.dumps(session, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._assert_safe(path)
            os.replace(temporary, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, identifier: str) -> dict[str, Any]:
        path = self._path(identifier)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(identifier)
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Samtalen er ikke gyldig JSON") from exc
        if not isinstance(session, dict) or session.get("id") != identifier:
            raise ValueError("Samtalefilen har feil ID")
        return session

    def create(self, project_slug: str | None = None, title: str = "Ny samtale") -> dict[str, Any]:
        project_slug = self._validate_slug(project_slug)
        now = self._stamp()
        session = {
            "id": uuid.uuid4().hex,
            "title": title or "Ny samtale",
            "projectSlug": project_slug,
            "messages": [],
            "composer": {},
            "createdAt": now,
            "updatedAt": now,
            "archived": False,
        }
        return self.save(session)

    def save(self, session: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(session, dict):
            raise TypeError("Samtalen må være et objekt")
        identifier = self._validate_id(session.get("id"))
        if "projectSlug" in session:
            session["projectSlug"] = self._validate_slug(session.get("projectSlug"))
        if not isinstance(session.get("messages", []), list):
            raise ValueError("messages må være en liste")
        if not isinstance(session.get("composer", {}), dict):
            raise ValueError("composer må være et objekt")
        session.setdefault("title", "Ny samtale")
        session.setdefault("messages", [])
        session.setdefault("composer", {})
        if session.get("title") in {"", "Ny samtale", "Untitled"}:
            first_user = next((message for message in session["messages"]
                               if isinstance(message, dict) and message.get("role") == "user"), None)
            content = first_user.get("content") if isinstance(first_user, dict) else ""
            if isinstance(content, str) and content.strip():
                session["title"] = content.strip().replace("\n", " ")[:80]
        session.setdefault("createdAt", self._stamp())
        session["updatedAt"] = self._stamp()
        session.setdefault("archived", False)
        self._write(self._path(identifier), session)
        return session

    def load(self, identifier: str) -> dict[str, Any]:
        return self._read(identifier)

    def list(self, project_slug: Any = _UNSET, include_archived: bool = False) -> list[dict[str, Any]]:
        if project_slug is not _UNSET:
            project_slug = self._validate_slug(project_slug)
        self._assert_safe(self.conversations_dir)
        result: list[dict[str, Any]] = []
        for path in sorted(self.conversations_dir.glob("*.json")):
            if path.is_symlink() or not _ID_RE.fullmatch(path.stem):
                continue
            try:
                session = self._read(path.stem)
            except (OSError, ValueError):
                continue
            if not include_archived and session.get("archived"):
                continue
            if project_slug is not _UNSET and session.get("projectSlug") != project_slug:
                continue
            result.append({
                "id": session["id"],
                "title": session.get("title", "Ny samtale"),
                "projectSlug": session.get("projectSlug"),
                "createdAt": session.get("createdAt"),
                "updatedAt": session.get("updatedAt"),
                "archived": bool(session.get("archived", False)),
                "messageCount": len(session.get("messages", [])),
                "unread": bool(session.get("unread")),
            })
        return sorted(result, key=lambda item: item.get("updatedAt") or 0, reverse=True)

    def bind(self, identifier: str, project_slug: str | None) -> dict[str, Any]:
        project_slug = self._validate_slug(project_slug)
        session = self.load(identifier)
        previous = session.get("projectSlug")
        if previous == project_slug:
            return session
        now = self._stamp()
        session["projectSlug"] = project_slug
        events = session.setdefault("events", [])
        events.append({"type": "project_bound", "from": previous, "to": project_slug, "at": now})
        if previous != project_slug:
            for message in session.get("messages", []):
                if isinstance(message, dict) and message.get("proposalStatus") in {"pending", "ready", "proposed"}:
                    message["proposalStatus"] = "stale"
                    message["stale"] = True
                    message["staleReason"] = "project changed"
            proposal = session.get("pendingProposal")
            if isinstance(proposal, dict):
                proposal["stale"] = True
                proposal["staleReason"] = "project changed"
            events.append({"type": "proposal_stale", "reason": "project changed", "at": now})
        return self.save(session)

    def recover_interrupted(self) -> int:
        """A process restart cannot prove that an in-flight side effect failed."""
        changed=0
        for summary in self.list(include_archived=True):
            session=self.load(summary["id"])
            touched=False
            for message in session.get("messages",[]):
                if not isinstance(message,dict): continue
                if (message.get("execution") or {}).get("status") == "running":
                    message["execution"]={"status":"interrupted","message":"Utførelsen ble avbrutt. Kontroller prosjektet og be om et oppdatert forslag før du fortsetter."}
                    message["stale"]=True
                    touched=True
            if touched:
                self.save(session); changed+=1
        return changed

    def rebind_project(self, previous: str, target: str | None) -> None:
        """Keep conversations reachable after a project rename or archive."""
        self._validate_slug(previous)
        self._validate_slug(target)
        for summary in self.list(previous, include_archived=True):
            self.bind(summary["id"], target)

    def archive(self, identifier: str) -> dict[str, Any]:
        session = self.load(identifier)
        session["archived"] = True
        session["archivedAt"] = self._stamp()
        return self.save(session)

    def migrate_legacy(self, project_slug: str) -> dict[str, Any] | None:
        project_slug = self._validate_slug(project_slug)
        legacy = Path(self.workspace.projects_dir) / project_slug / "System" / "conversation.json"
        self._assert_safe(legacy)
        if not legacy.is_file() or legacy.is_symlink():
            return None
        raw = legacy.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        source = str(legacy)
        for summary in self.list(include_archived=True):
            candidate = self.load(summary["id"])
            marker = candidate.get("migration") or {}
            legacy_source = candidate.get("legacySource") or {}
            if ((marker.get("source") == source and marker.get("digest") == digest)
                    or (legacy_source.get("path") == source and legacy_source.get("sha256") == digest)):
                return candidate
        messages = json.loads(raw.decode("utf-8"))
        if not isinstance(messages, list):
            raise ValueError("Legacy conversation må være en liste")
        first_user = next((m for m in messages if isinstance(m, dict) and m.get("role") == "user"), None)
        content = first_user.get("content") if isinstance(first_user, dict) else ""
        title = str(content).strip().replace("\n", " ")[:80] or f"{project_slug} · Samtale"
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "assistant":
                message.setdefault("sourceSlug", project_slug)
                if message.get("actions") and not message.get("applied"):
                    message["stale"] = True
                    message["staleReason"] = "Regissørverktøyene er oppdatert; be om et nytt forslag."
        session = self.create(project_slug, title)
        session["messages"] = messages
        session["legacySource"] = {"path": source, "sha256": digest}
        session["migration"] = {"source": source, "digest": digest, "tag": "legacy-conversation-v1"}
        session.setdefault("events", []).append({"type": "migrated_legacy", "source": source, "at": self._stamp()})
        return self.save(session)
