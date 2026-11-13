"""Immutable content-addressed raw bytes, separate from experiment metadata."""

from hashlib import sha256
import os
from pathlib import Path
import tempfile

from .contracts import ArtifactRef, ValidationError, validate_record


class ArtifactStore:
    """Local-filesystem artifact store; the configured root must be trusted.

    Writers fsync a private temporary file and publish it with an exclusive hard
    link. A competing writer can only publish the same digest; existing bytes
    are verified, never overwritten. Files left by a killed writer are harmless
    unreferenced temporaries and are not deleted automatically. This is not a
    distributed filesystem or a defense against hostile concurrent root changes.
    """

    def __init__(self, root: str | Path):
        supplied = Path(root).absolute()
        if supplied.is_symlink():
            raise ValidationError("Artifact root cannot be a symlink")
        supplied.mkdir(parents=True, exist_ok=True)
        self.root = supplied.resolve()

    def path_for(self, ref: ArtifactRef) -> Path:
        validate_record(ref)
        if type(ref) is not ArtifactRef:
            raise ValidationError("Expected an ArtifactRef")
        path = self.root / "sha256" / ref.digest[:2] / ref.digest
        for part in (path.parent.parent, path.parent, path):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ValidationError("Artifact paths cannot contain links")
        # Every component below the trusted root is generated from strict hex.
        # Do not resolve a concurrently published hard link: on Windows that can
        # transiently return an extended \\?\ path unlike the root's drive path.
        return path

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.path_for(ref)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ValidationError(f"Artifact is not readable: {ref.digest}") from error
        if len(data) != ref.size or sha256(data).hexdigest() != ref.digest:
            raise ValidationError(f"Artifact integrity check failed: {ref.digest}")
        return data

    def verify(self, ref: ArtifactRef) -> None:
        self.read(ref)

    def put(self, data: bytes) -> ArtifactRef:
        if type(data) is not bytes:
            raise ValidationError("Artifact input must be bytes")
        ref = ArtifactRef(sha256(data).hexdigest(), len(data))
        path = self.path_for(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path_for(ref)
        if path.exists():
            self.verify(ref)
            return ref
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            self.verify(ref)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return ref
