"""Durable workspace snapshots with opaque storage IDs and ownership metadata."""

import base64
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.storage import Database
from app.work_backend import WorkBackend
from app.workspace_rpc import parts


class WorkArtifacts:
    """Persist validated sandbox bytes before allowing its only copy to be destroyed."""

    def __init__(self, database: Database, directory: Path) -> None:
        self.database, self.directory = database, directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, identifier: UUID) -> Path:
        """Storage paths are derived from UUIDs, never from model/user filenames."""
        return self.directory / str(identifier)

    async def capture(self, run: dict, backend: WorkBackend) -> UUID:
        """Atomically publish a bounded snapshot; partial exports never become current."""
        from app.repository_workspace import capture

        repository = await capture(self.database, run, backend)
        if repository:
            listing = await backend._rpc("scan", exclude=["/repo"])
            if listing.get("error"):
                raise RuntimeError("Workspace export failed")
            entries = repository["entries"] + [item for item in listing["entries"] if not item["is_dir"]]
        else:
            listing = await backend.aglob("*")
            if listing.error:
                raise RuntimeError("Workspace export failed")
            entries = [item for item in listing.matches or [] if not item["is_dir"]]
        if len(entries) > (200 if repository else 100) or sum(item["size"] for item in entries) > 20 * 1024 * 1024:
            raise ValueError("Workspace snapshot exceeds limits")
        snapshot = uuid4()
        written = []
        prepared = False
        try:
            async with self.database.pool.connection() as connection:
                await connection.execute("INSERT INTO work_snapshots(id,run_id) VALUES(%s,%s)", (snapshot, run["id"]))
                total = 0
                for entry in entries:
                    parts(entry["path"])
                    result = (await backend.adownload_files([entry["path"]]))[0]
                    if result.error or result.content is None:
                        raise RuntimeError("File export failed")
                    total += len(result.content)
                    if len(result.content) > 1024 * 1024 or total > 20 * 1024 * 1024:
                        raise ValueError("Workspace snapshot exceeds limits")
                    identifier = uuid4()
                    target, temporary = self.path(identifier), self.directory / (str(identifier) + ".part")
                    written.extend([temporary, target])
                    with temporary.open("xb") as output:
                        output.write(result.content)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    await connection.execute(
                        "INSERT INTO work_artifacts(id,snapshot_id,run_id,owner_id,path,digest,size) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            identifier,
                            snapshot,
                            run["id"],
                            run["owner_id"],
                            entry["path"],
                            hashlib.sha256(result.content).hexdigest(),
                            len(result.content),
                        ),
                    )
                if repository:
                    identifier = uuid4()
                    target, temporary = self.path(identifier), self.directory / (str(identifier) + ".part")
                    written.extend([temporary, target])
                    with temporary.open("xb") as output:
                        output.write(repository["content"])
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    await connection.execute("INSERT INTO work_git_snapshots(snapshot_id,id,digest,size,metadata) "
                        "VALUES(%s,%s,%s,%s,%s)", (snapshot, identifier, repository["digest"], repository["size"],
                                                 Jsonb(repository["metadata"])))
                prepared = True
        except BaseException:
            # Keep files if COMMIT may have succeeded but its acknowledgement was lost.
            if not prepared:
                for path in written:
                    path.unlink(missing_ok=True)
            raise
        return snapshot

    async def restore(self, run: dict, backend: WorkBackend) -> None:
        """Restore the latest fully committed snapshot, verifying every stored digest."""
        from app.repository_workspace import restore, rpc

        bundles = await self.database.fetch("SELECT g.* FROM work_git_snapshots g WHERE snapshot_id=COALESCE("
            "(SELECT id FROM work_snapshots WHERE run_id=%s ORDER BY created_at DESC,id DESC LIMIT 1),%s::uuid)",
            (run["id"], run.get("restore_snapshot_id")))
        saved = None
        if bundles:
            bundle = bundles[0]
            content = self.path(bundle["id"]).read_bytes()
            if len(content) != bundle["size"] or hashlib.sha256(content).hexdigest() != bundle["digest"]:
                raise RuntimeError("Git history integrity check failed")
            saved = content, bundle["metadata"]
        metadata = await restore(self.database, run, backend, saved)
        rows = await self.database.fetch(
            "SELECT a.* FROM work_artifacts a WHERE a.snapshot_id=COALESCE((SELECT id FROM work_snapshots "
            "WHERE run_id=%s ORDER BY created_at DESC,id DESC LIMIT 1),%s::uuid) AND a.owner_id=%s ORDER BY a.path",
            (run["id"], run.get("restore_snapshot_id"), run["owner_id"]),
        )
        for row in rows:
            parts(row["path"])
            content = self.path(row["id"]).read_bytes()
            if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["digest"]:
                raise RuntimeError("Artifact integrity check failed")
            if metadata and row["path"].startswith("/repo/"):
                # Checkout provides HEAD files; apply the verified current worktree over them.
                result = await backend._rpc("write", path=row["path"],
                                            data=base64.b64encode(content).decode(), replace=True)
                error = result.get("error")
            else:
                result = (await backend.aupload_files([(row["path"], content)]))[0]
                error = result.error
            if error:
                raise RuntimeError("Workspace restore failed")
        if metadata:
            await rpc(backend, "modes", modes=metadata["modes"])

    async def seed_inputs(self, run: dict, backend: WorkBackend) -> None:
        """Apply frozen input versions only when creating the first workspace."""
        if await self.database.fetch("SELECT 1 FROM work_snapshots WHERE run_id=%s LIMIT 1", (run["id"],)):
            return
        for item in run.get("input_files", []):
            identifier = UUID(item["id"])
            content = (self.directory.parent / "work-inputs" / str(identifier)).read_bytes()
            if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["digest"]:
                raise RuntimeError("Input file integrity check failed")
            # A recovery copy takes the historical file when the same path already exists.
            existing = (await backend.adownload_files([item["path"]]))[0]
            if existing.content is not None:
                continue
            result = (await backend.aupload_files([(item["path"], content)]))[0]
            if result.error:
                raise RuntimeError("Task input import failed")
