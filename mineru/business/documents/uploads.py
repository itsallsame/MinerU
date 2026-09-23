"""Atomically publish business uploads into a Doclib-visible directory."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ...filetypes import PARSEABLE_EXTENSIONS, normalize_parse_extension


class UploadError(ValueError):
    """An upload cannot be accepted without publishing a partial source."""


@dataclass(frozen=True)
class StoredUpload:
    """Identity of a fully published, business-owned source file."""

    path: Path
    sha256: str
    size: int
    extension: str


class ImmutableUploadStore:
    """Store complete uploads under opaque names on a single shared filesystem.

    The API and Doclib worker must mount the directory at the same absolute
    container path. Only the API writes it; the worker mounts it read-only.
    """

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if root.is_symlink():
            raise UploadError("Upload root cannot be a symbolic link")
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise UploadError("Upload root must be a directory")
        self._root = resolved_root
        self._max_bytes = max_bytes

    def store(self, source: BinaryIO, *, filename: str) -> StoredUpload:
        """Copy a stream and publish it only after its bytes are synced.

        One call creates one source path, including for duplicate content. The
        caller records its business identity separately in a transaction.
        """
        extension = normalize_parse_extension(Path(filename.replace("\\", "/")).name)
        if extension not in PARSEABLE_EXTENSIONS:
            raise UploadError(f"Unsupported upload extension: {extension}")
        token = uuid.uuid4().hex
        staging = self._root / f".{token}.part"
        published = self._root / f"{token}.{extension}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(staging, flags, 0o600)
        committed = False
        linked = False
        try:
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as target:
                while chunk := source.read(min(1024 * 1024, self._max_bytes - size + 1)):
                    if not isinstance(chunk, bytes):
                        raise UploadError("Upload stream must return bytes")
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise UploadError(f"Upload exceeds {self._max_bytes} bytes")
                    digest.update(chunk)
                    target.write(chunk)
                if size == 0:
                    raise UploadError("Empty upload is not allowed")
                target.flush()
                os.fsync(target.fileno())
                os.fchmod(target.fileno(), 0o444)
            os.link(staging, published)
            linked = True
            staging.unlink()
            directory_flags = os.O_RDONLY | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
            directory_fd = os.open(self._root, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            committed = True
            return StoredUpload(path=published, sha256=digest.hexdigest(), size=size, extension=extension)
        finally:
            if not committed:
                if linked:
                    published.unlink(missing_ok=True)
                staging.unlink(missing_ok=True)


__all__ = ["ImmutableUploadStore", "StoredUpload", "UploadError"]
