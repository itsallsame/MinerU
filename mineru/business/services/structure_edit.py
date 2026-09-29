"""Append-only business structure overlays over immutable historical parses."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from typing import Any

from ...types import BlockType
from ..domain import ReviewSource, StructurePageVersion
from ..store import BusinessStore, BusinessStoreError, StructureVersionConflict
from .discovery import BusinessDiscovery, BusinessStructurePage, DiscoveryError, StructureBlock


class StructureEditError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _node_id(revision_id: str, page_no: int, path: tuple[int, ...]) -> str:
    seed = f"{revision_id}:{page_no}:{'.'.join(map(str, path))}"
    return "n_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _native_node(revision_id: str, page_no: int, block: StructureBlock) -> dict[str, Any]:
    return {
        "node_id": _node_id(revision_id, page_no, block.path), "type": block.type,
        "level": block.level, "preview": block.preview, "text_override": None,
        "content_changed": False,
        "origins": [{
            "path": list(block.path), "locator": block.locator, "type": block.type,
            "preview": block.preview, "bbox": list(block.bbox) if block.bbox is not None else None,
        }],
        "children": [_native_node(revision_id, page_no, child) for child in block.children],
    }


def _walk(nodes: list[dict[str, Any]], depth: int = 1) -> list[tuple[dict[str, Any], int]]:
    found: list[tuple[dict[str, Any], int]] = []
    for node in nodes:
        found.append((node, depth))
        found.extend(_walk(node["children"], depth + 1))
    return found


def _find(nodes: list[dict[str, Any]], node_id: str) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    for index, node in enumerate(nodes):
        if node["node_id"] == node_id:
            return nodes, index, node
        try:
            return _find(node["children"], node_id)
        except StructureEditError as exc:
            if exc.code != "structure_node_not_found":
                raise
    raise StructureEditError("structure_node_not_found")


def _preview(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:200]


class StructureEditor:
    """Only bounded operations can produce derived versions; clients cannot submit trees."""

    def __init__(self, *, store: BusinessStore, discovery: BusinessDiscovery) -> None:
        self._store = store
        self._discovery = discovery

    def get(self, revision_id: str, page_no: int, version: int | None = None) -> StructurePageVersion:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise StructureEditError("revision_not_found")
        if page_no < 1 or revision.parse_id_for_page(page_no) is None:
            raise StructureEditError("invalid_structure_page")
        if version is not None and version < 0:
            raise StructureEditError("invalid_structure_version")
        try:
            saved = None if version == 0 else self._store.get_structure_version(revision_id, page_no, version)
        except BusinessStoreError as exc:
            raise StructureEditError("structure_version_unavailable") from exc
        if saved is not None and version != 0:
            return saved
        if version not in (None, 0):
            raise StructureEditError("structure_version_not_found")
        try:
            native = self._discovery.structure(revision_id, page_no)
        except DiscoveryError as exc:
            raise StructureEditError(exc.code) from exc
        return StructurePageVersion(
            document_id=revision.document_id, revision_id=revision_id, page_no=page_no,
            version=0, nodes=[_native_node(revision_id, page_no, block) for block in native.blocks],
            operation=None, source=None, reason=None, created_at_ms=None,
        )

    def apply(
        self, revision_id: str, page_no: int, *, expected_version: int,
        operation: dict[str, Any], source: ReviewSource, reason: str, request_key: str | None = None,
    ) -> StructurePageVersion:
        if expected_version < 0 or source not in ("web", "skill", "api") or not (1 <= len(reason.strip()) <= 500):
            raise StructureEditError("invalid_structure_operation")
        if request_key is not None:
            try:
                prior = self._store.get_structure_request(request_key)
            except BusinessStoreError as exc:
                code = (
                    "invalid_structure_request_key" if str(exc) == "Invalid structure idempotency key"
                    else "structure_version_unavailable"
                )
                raise StructureEditError(code) from exc
            if prior is not None:
                if (
                    prior.revision_id == revision_id and prior.page_no == page_no
                    and prior.version == expected_version + 1 and prior.operation == operation
                    and prior.source == source and prior.reason == reason.strip()
                ):
                    return prior
                raise StructureEditError("structure_request_conflict")
        current = self.get(revision_id, page_no)
        if current.version != expected_version:
            raise StructureEditError("structure_version_conflict")
        try:
            native = self._discovery.structure(revision_id, page_no)
        except DiscoveryError as exc:
            raise StructureEditError(exc.code) from exc
        nodes = copy.deepcopy(current.nodes)
        self._verify_tree(nodes, native)
        self._operate(revision_id, page_no, nodes, operation)
        self._verify_tree(nodes, native)
        try:
            return self._store.append_structure_version(
                revision_id, page_no, expected_version=expected_version,
                nodes=nodes, operation=operation, source=source, reason=reason.strip(),
                request_key=request_key,
            )
        except StructureVersionConflict as exc:
            raise StructureEditError("structure_version_conflict") from exc
        except BusinessStoreError as exc:
            raise StructureEditError("invalid_structure_operation") from exc

    def _verify_tree(self, nodes: list[dict[str, Any]], native: BusinessStructurePage) -> None:
        if len(nodes) > 500:
            raise StructureEditError("structure_limit_exceeded")
        native_by_path: dict[tuple[int, ...], StructureBlock] = {}

        def add_native(block: StructureBlock) -> None:
            native_by_path[block.path] = block
            for child in block.children:
                add_native(child)

        for block in native.blocks:
            add_native(block)
        seen: set[str] = set()
        count = 0
        total_text = 0
        for node, depth in _walk(nodes):
            count += 1
            if count > 2000 or depth > 16 or not isinstance(node["node_id"], str) or node["node_id"] in seen:
                raise StructureEditError("structure_limit_exceeded")
            seen.add(node["node_id"])
            if node["type"] not in {kind.value for kind in BlockType}:
                raise StructureEditError("invalid_structure_type")
            text = node["text_override"]
            if text is not None:
                if not isinstance(text, str) or len(text) > 100_000:
                    raise StructureEditError("structure_limit_exceeded")
                total_text += len(text)
            if total_text > 500_000 or not isinstance(node["children"], list):
                raise StructureEditError("structure_limit_exceeded")
            for origin in node["origins"]:
                source_node = native_by_path.get(tuple(origin["path"]))
                if source_node is None or (
                    origin["locator"] != source_node.locator or origin["type"] != source_node.type
                    or origin["preview"] != source_node.preview
                    or origin["bbox"] != (list(source_node.bbox) if source_node.bbox is not None else None)
                ):
                    raise StructureEditError("historical_structure_mismatch")

    def _text(self, revision_id: str, page_no: int, node: dict[str, Any]) -> str:
        if node["children"]:
            raise StructureEditError("structure_node_not_editable")
        if node["text_override"] is not None:
            return node["text_override"]
        if len(node["origins"]) != 1 or node["content_changed"]:
            raise StructureEditError("structure_node_not_editable")
        path = ".".join(map(str, node["origins"][0]["path"]))
        try:
            original = self._discovery.structure_node_text(revision_id, page_no, path)
        except DiscoveryError as exc:
            raise StructureEditError(exc.code) from exc
        if not original.editable or original.text is None:
            raise StructureEditError("structure_node_not_editable")
        return original.text

    def _operate(
        self, revision_id: str, page_no: int, nodes: list[dict[str, Any]], operation: dict[str, Any]
    ) -> None:
        kind = operation.get("op")
        allowed = {
            "set_type": {"op", "node_id", "type", "level"},
            "set_text": {"op", "node_id", "text"},
            "split": {"op", "node_id", "offset"},
            "merge": {"op", "first_id", "second_id", "separator"},
            "move": {"op", "node_id", "parent_id", "index"},
            "insert": {"op", "parent_id", "index", "type", "text", "level"},
            "delete": {"op", "node_id"},
        }
        required = {
            "set_type": {"node_id", "type"}, "set_text": {"node_id", "text"},
            "split": {"node_id", "offset"}, "merge": {"first_id", "second_id"},
            "move": {"node_id", "index"}, "insert": {"index", "type", "text"},
            "delete": {"node_id"},
        }
        if not isinstance(kind, str) or kind not in allowed or set(operation) - allowed[kind] or (
            not required[kind].issubset(operation)
        ):
            raise StructureEditError("invalid_structure_operation")
        if kind == "set_type":
            node = _find(nodes, operation["node_id"])[2]
            try:
                new_type = BlockType(operation["type"]).value
            except (ValueError, KeyError) as exc:
                raise StructureEditError("invalid_structure_type") from exc
            level = operation.get("level")
            if (new_type == "paragraph_title" and (type(level) is not int or not 1 <= level <= 6)) or (
                new_type != "paragraph_title" and level is not None
            ):
                raise StructureEditError("invalid_structure_level")
            node["type"], node["level"] = new_type, level
        elif kind == "set_text":
            node = _find(nodes, operation["node_id"])[2]
            self._text(revision_id, page_no, node)
            value = operation["text"]
            if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
                raise StructureEditError("invalid_structure_text")
            node["text_override"], node["preview"], node["content_changed"] = value, _preview(value), True
        elif kind == "split":
            siblings, index, node = _find(nodes, operation["node_id"])
            content = self._text(revision_id, page_no, node)
            offset = operation["offset"]
            if type(offset) is not int or not 0 < offset < len(content):
                raise StructureEditError("invalid_structure_offset")
            if not content[:offset].strip() or not content[offset:].strip():
                raise StructureEditError("invalid_structure_offset")
            replacements = []
            for value in (content[:offset], content[offset:]):
                child = copy.deepcopy(node)
                child["node_id"] = "u_" + uuid.uuid4().hex
                child["text_override"], child["preview"], child["content_changed"] = value, _preview(value), True
                replacements.append(child)
            siblings[index:index + 1] = replacements
        elif kind == "merge":
            siblings, index, first = _find(nodes, operation["first_id"])
            if index + 1 >= len(siblings) or siblings[index + 1]["node_id"] != operation["second_id"]:
                raise StructureEditError("invalid_structure_merge")
            second = siblings[index + 1]
            if first["type"] != second["type"] or first["level"] != second["level"]:
                raise StructureEditError("invalid_structure_merge")
            separator = operation.get("separator", "\n")
            if not isinstance(separator, str) or len(separator) > 8:
                raise StructureEditError("invalid_structure_merge")
            value = self._text(revision_id, page_no, first) + separator + self._text(revision_id, page_no, second)
            if len(value) > 100_000:
                raise StructureEditError("structure_limit_exceeded")
            merged = copy.deepcopy(first)
            merged["node_id"] = "u_" + uuid.uuid4().hex
            merged["origins"] = list({tuple(origin["path"]): origin for origin in (
                first["origins"] + second["origins"]
            )}.values())
            merged["text_override"], merged["preview"], merged["content_changed"] = value, _preview(value), True
            siblings[index:index + 2] = [merged]
        elif kind == "move":
            siblings, index, node = _find(nodes, operation["node_id"])
            parent_id = operation.get("parent_id")
            if parent_id is not None and any(item["node_id"] == parent_id for item, _ in _walk([node])):
                raise StructureEditError("invalid_structure_cycle")
            siblings.pop(index)
            destination = nodes if parent_id is None else _find(nodes, parent_id)[2]["children"]
            target_index = operation["index"]
            if type(target_index) is not int or not 0 <= target_index <= len(destination):
                raise StructureEditError("invalid_structure_index")
            destination.insert(target_index, node)
        elif kind == "insert":
            value = operation["text"]
            if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
                raise StructureEditError("invalid_structure_text")
            try:
                new_type = BlockType(operation["type"]).value
            except (ValueError, KeyError) as exc:
                raise StructureEditError("invalid_structure_type") from exc
            if new_type not in {"text", "paragraph_title", "doc_title"}:
                raise StructureEditError("invalid_structure_type")
            level = operation.get("level")
            if (new_type == "paragraph_title" and (type(level) is not int or not 1 <= level <= 6)) or (
                new_type != "paragraph_title" and level is not None
            ):
                raise StructureEditError("invalid_structure_level")
            parent_id = operation.get("parent_id")
            destination = nodes if parent_id is None else _find(nodes, parent_id)[2]["children"]
            target_index = operation["index"]
            if type(target_index) is not int or not 0 <= target_index <= len(destination):
                raise StructureEditError("invalid_structure_index")
            destination.insert(target_index, {
                "node_id": "u_" + uuid.uuid4().hex, "type": new_type, "level": level,
                "preview": _preview(value), "text_override": value, "content_changed": True,
                "origins": [], "children": [],
            })
        elif kind == "delete":
            siblings, index, _node = _find(nodes, operation["node_id"])
            siblings.pop(index)


__all__ = ["StructureEditor", "StructureEditError"]
