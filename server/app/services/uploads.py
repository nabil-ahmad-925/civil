import threading
from uuid import uuid4

from ..models import Document, Manual
from ..repository.base import Repository

_lock = threading.Lock()


def finalize_upload(
    repo: Repository, tmp_path: str, *, manual: Manual, version: str, filename: str, sha256: str, size: int
) -> Document:
    """Puts an already-validated, already-hashed PDF in the bucket and records it. The same file can be uploaded again."""
    with _lock:
        doc_id = str(uuid4())
        doc = Document(
            id=doc_id,
            manual=manual,
            version=version,
            original_name=filename,
            size_bytes=size,
            sha256=sha256,
            storage_path=f"{manual}/{doc_id}.pdf",
            status="uploaded",
        )
        repo.upload_file(doc.storage_path, tmp_path)
        try:
            repo.insert_document(doc)
        except Exception:
            repo.delete_file(doc.storage_path)  # do not leave an orphan in the bucket
            raise
        return doc
