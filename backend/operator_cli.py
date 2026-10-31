"""Server-side operations that deliberately have no web route.

Run with:  python -m backend.operator_cli <command>

These need shell access to the server. Reading --help is safe. Commands that move
or delete data report every failure and stop short of anything they can't prove.
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import List

from backend import safety
from backend.config import settings
from backend.db import connect, init_db
from backend.diagnofast import recordings
from backend.records import documents, staff, vault
from backend.records.store import insert, now_iso, rows


def _key_ring(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_ring(path: Path, ring: dict) -> None:
    documents.write_private(path, json.dumps(ring, indent=1).encode())


def generate_key(args) -> int:
    path = Path(args.path).expanduser()
    if path.exists():
        print(f"{path} already exists. Use add-key to rotate.", file=sys.stderr)
        return 1
    if not safety.key_file_is_safe(path):
        print("Choose a location outside the repository and away from stored documents.", file=sys.stderr)
        return 1
    _write_ring(path, {"active": "k1", "keys": {"k1": vault.new_key()}})
    print(f"Created {path}. Set DIAGNOSIA_DOCUMENT_KEY_FILE={path}")
    print("Back this file up separately from your data. Losing it makes stored documents unreadable.")
    return 0


def add_key(args) -> int:
    path = Path(settings.document_key_file)
    ring = _key_ring(path)
    if args.key_id in ring["keys"]:
        print("That key id already exists.", file=sys.stderr)
        return 1
    ring["keys"][args.key_id] = vault.new_key()
    ring["active"] = args.key_id
    _write_ring(path, ring)
    print(f"New documents use {args.key_id}. Run rewrap-documents next, then back up the key ring.")
    return 0


def rewrap_documents(_args) -> int:
    """Moves every document to the active key. Content isn't re-encrypted, only its file key."""
    db = connect()
    failures: List[str] = []
    active = vault.active_key_id()
    count = 0
    for document in rows(db.execute("SELECT * FROM clinical_documents WHERE encryption_state = 'encrypted' AND key_id != ?", (active,))):
        context = documents.context_for(document["patient_id"], document["id"])
        path = documents.path_for(document)
        try:
            blob = vault.rewrap(path.read_bytes(), context)
            if hashlib.sha256(vault.decrypt(blob, context)).hexdigest() != document["sha256"]:
                raise vault.IntegrityFailure("Checksum mismatch after rewrap")
            documents.write_private(path, blob)
            db.execute("UPDATE clinical_documents SET key_id = ? WHERE id = ?", (active, document["id"]))
            db.commit()
            count += 1
        except (OSError, vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{document['id']}: {type(error).__name__}")
    for path, context in recordings.everything():
        try:
            blob = path.read_bytes()
            if vault.key_id_of(blob) != active:
                documents.write_private(path, vault.rewrap(blob, context))
                count += 1
        except (OSError, vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{path.name}: {type(error).__name__}")
    db.close()
    print(f"Rewrapped {count}. Failed {len(failures)}.")
    print("\n".join(failures))
    return 1 if failures else 0


def retire_key(args) -> int:
    db = connect()
    used = db.execute("SELECT COUNT(*) FROM clinical_documents WHERE key_id = ?", (args.key_id,)).fetchone()[0]
    db.close()
    used += sum(vault.key_id_of(path.read_bytes()) == args.key_id for path, _context in recordings.everything())
    path = Path(settings.document_key_file)
    ring = _key_ring(path)
    if used or ring["active"] == args.key_id:
        print(f"Key {args.key_id} is still in use by {used} documents or is active.", file=sys.stderr)
        return 1
    ring["keys"].pop(args.key_id, None)
    _write_ring(path, ring)
    print(f"Removed {args.key_id}. Older backups need the old key ring kept safely.")
    return 0


def migrate_documents(args) -> int:
    """Encrypts legacy plaintext files. The originals stay until their copies are proved and deletion is allowed."""
    db = connect()
    legacy = rows(db.execute("SELECT * FROM clinical_documents WHERE encryption_state = 'plaintext_legacy'"))
    migrated, failures = 0, []
    for document in legacy:
        source = documents.path_for(document)
        try:
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != document["sha256"]:
                raise vault.IntegrityFailure("The original doesn't match its recorded checksum")
            if args.dry_run:
                migrated += 1
                continue
            stored_name = f"{document['id']}.enc"
            target = source.with_name(stored_name)
            context = documents.context_for(document["patient_id"], document["id"])
            documents.write_private(target, vault.encrypt(data, context))
            if vault.decrypt(target.read_bytes(), context) != data:
                target.unlink(missing_ok=True)
                raise vault.IntegrityFailure("The encrypted copy didn't read back")
            db.execute(
                """
                UPDATE clinical_documents SET encryption_state = 'encrypted', stored_name = ?, key_id = ?,
                    legacy_stored_name = ? WHERE id = ? AND encryption_state = 'plaintext_legacy'
                """,
                (stored_name, vault.active_key_id(), document["stored_name"], document["id"]),
            )
            db.commit()
            migrated += 1
        except (OSError, vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{document['id']}: {type(error).__name__}: {error}")
    db.close()
    verb = "Would migrate" if args.dry_run else "Migrated"
    print(f"{verb} {migrated} of {len(legacy)}. Failed {len(failures)}.")
    print("\n".join(failures))
    return 1 if failures else 0


def delete_originals(args) -> int:
    """Removes plaintext originals only with a named retention approval, and only after re-proving each copy."""
    if not args.retention_approval.strip():
        print("A retention approval reference is required.", file=sys.stderr)
        return 1
    db = connect()
    deleted, failures = 0, []
    for document in rows(db.execute("SELECT * FROM clinical_documents WHERE legacy_stored_name IS NOT NULL")):
        original = settings.documents_dir / document["patient_id"] / document["legacy_stored_name"]
        try:
            documents.read(document)
            original.unlink(missing_ok=True)
            db.execute("UPDATE clinical_documents SET legacy_stored_name = NULL WHERE id = ?", (document["id"],))
            db.commit()
            deleted += 1
        except (OSError, vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{document['id']}: {type(error).__name__}")
    db.close()
    print(f"Deleted {deleted} originals under approval {args.retention_approval}. Failed {len(failures)}.")
    print("\n".join(failures))
    return 1 if failures else 0


def verify_documents(_args) -> int:
    db = connect()
    found = rows(db.execute("SELECT * FROM clinical_documents WHERE retention_status = 'retained'"))
    failures = []
    for document in found:
        try:
            documents.read(document)
        except (vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{document['id']}: {error}")
    db.close()
    checked = len(found)
    for path, context in recordings.everything():
        checked += 1
        try:
            vault.decrypt(path.read_bytes(), context)
        except (vault.IntegrityFailure, vault.VaultUnavailable) as error:
            failures.append(f"{path.name}: {error}")
    print(f"Checked {checked}. Failed {len(failures)}.")
    print("\n".join(failures))
    return 1 if failures else 0


def backup(args) -> int:
    """Copies the database and documents, encrypted. The key ring is never included."""
    target = Path(args.to).expanduser()
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    snapshot = target / "database.plain"
    db = connect()
    copy = sqlite3.connect(snapshot)
    try:
        db.backup(copy)
    finally:
        copy.close()
        db.close()
    try:
        data = snapshot.read_bytes()
        documents.write_private(target / "database.enc", vault.encrypt(data, "backup:database"))
        manifest = {"created_at": now_iso(), "database_sha256": hashlib.sha256(data).hexdigest(), "documents": []}
    finally:
        snapshot.unlink(missing_ok=True)
    if settings.documents_dir.exists():
        for path in sorted(settings.documents_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(settings.documents_dir)
                destination = target / "documents" / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(path, destination)
                manifest["documents"].append({"path": str(relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    documents.write_private(target / "manifest.json", json.dumps(manifest, indent=1).encode())
    plain = [item for item in manifest["documents"] if not item["path"].endswith(".enc")]
    print(f"Backed up to {target}: database and {len(manifest['documents'])} files.")
    if plain:
        print(f"WARNING: {len(plain)} files are unmigrated plaintext. Run migrate-documents.")
    return 0


def restore(args) -> int:
    """Restores into new paths only, after checking every hash. It never overwrites."""
    source = Path(args.source).expanduser()
    database_target, documents_target = Path(args.database).expanduser(), Path(args.documents).expanduser()
    if database_target.exists() or documents_target.exists():
        print("Restore targets must not exist yet.", file=sys.stderr)
        return 1
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    data = vault.decrypt((source / "database.enc").read_bytes(), "backup:database")
    if hashlib.sha256(data).hexdigest() != manifest["database_sha256"]:
        print("The database backup failed its checksum.", file=sys.stderr)
        return 1
    for item in manifest["documents"]:
        if hashlib.sha256((source / "documents" / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            print(f"A document failed its checksum: {item['path']}", file=sys.stderr)
            return 1
    documents.write_private(database_target, data)
    for item in manifest["documents"]:
        destination = documents_target / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source / "documents" / item["path"], destination)
    print(f"Restored to {database_target} and {documents_target}. Point the application there after checking it.")
    return 0


def bootstrap_organization(args) -> int:
    init_db()
    db = connect()
    try:
        organization_id = staff.operator_verify_organization(
            db, args.admin_email, args.organization, args.type, args.evidence_type, args.evidence_reference, args.operator, args.profession,
        )
        db.commit()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Verified organization {organization_id}.")
    return 0


def signoff(args) -> int:
    if args.item not in safety.REQUIRED_SIGNOFFS:
        print(f"Unknown item. Choose from: {', '.join(safety.REQUIRED_SIGNOFFS)}", file=sys.stderr)
        return 1
    db = connect()
    insert(
        db,
        "governance_signoffs",
        item=args.item,
        signed_by=args.signed_by,
        signer_role=args.role,
        document_reference=args.document_reference,
        recorded_by=args.operator,
        recorded_at=now_iso(),
    )
    db.commit()
    db.close()
    print(f"Recorded {args.item}. This stores a person's decision. It doesn't make the decision.")
    return 0


def revoke_signoff(args) -> int:
    db = connect()
    db.execute(
        "UPDATE governance_signoffs SET revoked_at = ?, revoked_by = ? WHERE item = ? AND revoked_at IS NULL",
        (now_iso(), args.operator, args.item),
    )
    db.commit()
    db.close()
    print(f"Revoked {args.item}.")
    return 0


def _env_line(path: Path, name: str, value: str) -> None:
    """Adds a setting to .env, leaving anything already there alone."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not any(line.split("=")[0].strip() == name for line in existing.splitlines()):
        path.write_text(f"{existing.rstrip()}\n{name}={value}\n".lstrip(), encoding="utf-8")


def setup_local(args) -> int:
    """One command to a working local platform, with synthetic demo data. Development only."""
    from fastapi.testclient import TestClient

    from backend import demo
    from backend.app import app

    if settings.environment != "development" or settings.allow_real_patient_data:
        print("setup-local only runs in development, without real patient data.", file=sys.stderr)
        return 1
    home = Path(args.home).expanduser()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = home / "document-keys.json"
    if not settings.document_key_file:
        if not key_path.exists():
            _write_ring(key_path, {"active": "k1", "keys": {"k1": vault.new_key()}})
        settings.document_key_file = str(key_path)
        if args.env_file:
            _env_line(Path(args.env_file), "DIAGNOSIA_DOCUMENT_KEY_FILE", str(key_path))
    if args.env_file:
        _env_line(Path(args.env_file), "GROQ_API_KEY", "your-groq-key")

    key_ok, key_detail = vault.status()
    if not key_ok:
        print(f"Document encryption isn't working: {key_detail}", file=sys.stderr)
        return 1

    init_db()
    db = connect()
    already = db.execute("SELECT 1 FROM organizations WHERE name = ?", (demo.DEMO_ORGANIZATION,)).fetchone()
    db.close()
    if already:
        print("Demo setup already exists. Nothing changed.")
        print(f"Logins are in {home / 'local-accounts.txt'}")
        return 0

    accounts = {
        "doctor": {"label": "Doctor and clinic administrator", "first_name": "Meera", "last_name": "Iyer", "role": "doctor", "profession": "doctor"},
        "specialist": {"label": "Specialist and centre administrator", "first_name": "Farah", "last_name": "Khan", "role": "doctor", "profession": "doctor"},
        "laboratory": {"label": "Laboratory reviewer", "first_name": "Lata", "last_name": "Menon", "role": "doctor", "profession": "laboratory_professional"},
        "patient": {"label": "Patient", "first_name": "Ravi", "last_name": "Kumar", "role": "patient", "profession": None},
    }
    with TestClient(app) as anonymous:
        people = {}
        for name, account in accounts.items():
            client = TestClient(app)
            account["email"] = f"demo.{name.replace('_', '')}@example.com"
            account["password"] = secrets.token_urlsafe(12)
            response = client.post(
                "/api/auth/register",
                json={key: account[key] for key in ("first_name", "last_name", "email", "password", "role")},
            )
            if response.status_code != 201:
                print(f"Couldn't create {account['email']}: {response.status_code}", file=sys.stderr)
                return 1
            account["id"] = response.json()["id"]
            people[name] = client

        db = connect()
        organizations = {
            "clinic": staff.operator_verify_organization(
                db, accounts["doctor"]["email"], demo.DEMO_ORGANIZATION, "clinic", "Local demo setup", "SYNTHETIC", "setup-local"
            ),
            "specialist": staff.operator_verify_organization(
                db, accounts["specialist"]["email"], demo.SPECIALIST_ORGANIZATION, "clinic", "Local demo setup", "SYNTHETIC", "setup-local"
            ),
        }
        db.execute("UPDATE organizations SET specialities = 'Cardiology' WHERE id = ?", (organizations["specialist"],))
        for name in ("laboratory",):
            staff.record_decision(
                db, accounts[name]["id"], "verified", None, "operator:setup-local", evidence_type="Local demo setup",
                evidence_reference="SYNTHETIC", verified_profession=accounts[name]["profession"],
            )
            db.execute(
                "INSERT INTO organization_members (organization_id, user_id, role, added_by, created_at) VALUES (?, ?, 'staff', ?, ?)",
                (organizations["clinic"], accounts[name]["id"], accounts["doctor"]["id"], now_iso()),
            )
        db.commit()
        db.close()
        # Saved first, so a failure below can't leave accounts nobody can sign in to.
        logins = home / "local-accounts.txt"
        documents.write_private(logins, demo.describe_accounts(accounts).encode())
        demo.seed(people, organizations)
        for client in people.values():
            client.close()
        anonymous.close()

    print(f"Done. Logins are in {logins}")
    print("Restart the server, then open /care.html. Add a free Groq key under Settings to turn on AI.")
    return 0


def _print_status(items) -> int:
    for item in items:
        print(f"{item.name}: {item.state}  {item.detail}")
        for label, passed, detail in item.checks:
            print(f"    {'pass' if passed else 'FAIL'}  {label}: {detail}")
    return 1 if any(item.state == "FAILED" for item in items) else 0


def integrations_status(_args) -> int:
    """Tries each service now. A setting alone never counts as connected."""
    from backend.integrations import status

    init_db()
    return _print_status(status.overview())


def _health_command(name: str):
    def run(_args) -> int:
        from backend.integrations import status

        init_db()
        return _print_status([status.CHECKS[name](True)])

    return run


def integrations_validate(_args) -> int:
    """Runs the live tests for each service that's set up, and names what's blocking the others."""
    from backend.integrations import status

    init_db()
    items = status.overview(deep=True)
    code = _print_status(items)
    names = {"MONGODB_ATLAS": "atlas", "YUGABYTEDB": "yugabytedb", "MEKO": "meko"}
    live = [names[item.name] for item in items if item.state == status.CONNECTED]
    for item in items:
        if item.state != status.CONNECTED:
            print(f"Not tested live: {item.name} is {item.state}")
    if not live:
        return code
    env = {**os.environ, "DIAGNOSIA_LIVE_INTEGRATIONS": "1"}
    command_line = [sys.executable, "-m", "pytest", "-m", "live", "backend/tests/live/test_live_integrations.py", "-q", "-k", " or ".join(live)]
    return subprocess.run(command_line, env=env).returncode or code


def meko_ingest(args) -> int:
    """Uploads the knowledge base through Meko's documented tools. --dry-run only checks the files."""
    from backend.integrations import knowledge, meko

    documents = knowledge.corpus()
    print(f"{len(documents)} files passed the checks.")
    if args.dry_run:
        for document in documents:
            print(f"  {document.path.name}  {document.fields['category']}  v{document.fields['version']}")
        return 0
    client = meko.client()
    if client is None:
        print("Meko isn't set up. Set ENABLE_MEKO_INTEGRATION, MEKO_API_KEY and MEKO_DATAPACK_ID.", file=sys.stderr)
        return 1
    init_db()
    db = connect()
    results = knowledge.ingest(client, db, documents)
    db.close()
    for result in results:
        print(f"  {result['document']}: {result['state']}")
    return 1 if any(result["state"].startswith("failed") or result["state"] in ("abandoned", "oversized") for result in results) else 0


def meko_seed_memory(_args) -> int:
    """Adds the reviewed conventions as memory. Promoting one to everyone stays a person's choice."""
    from backend.integrations import knowledge, meko

    client = meko.client()
    if client is None:
        print("Meko isn't set up. Set ENABLE_MEKO_INTEGRATION, MEKO_API_KEY and MEKO_DATAPACK_ID.", file=sys.stderr)
        return 1
    entries = json.loads((knowledge.FOLDER / "memory.json").read_text(encoding="utf-8"))
    print(f"Saved {knowledge.seed_memory(client, entries)} conventions.")
    return 0


def careflow_deliver(_args) -> int:
    """One delivery pass, for checking a connection by hand. The running app does this by itself."""
    from backend.careflow import delivery

    init_db()
    print(json.dumps(delivery.deliver_once()))
    db = connect()
    print(json.dumps(delivery.backlog(db)))
    db.close()
    return 0


def careflow_replay(args) -> int:
    """Puts failed deliveries back in the queue. Destinations ignore repeats, so this is safe."""
    from backend.careflow import delivery

    db = connect()
    count = delivery.replay_dead(db, args.channel)
    db.commit()
    db.close()
    print(f"Queued {count} deliveries again.")
    return 0


def careflow_demo(args) -> int:
    """A repeatable synthetic journey. It refuses anything but a development setup."""
    from backend.careflow import demo as careflow_demo_journey
    from backend.careflow.events import active

    if settings.environment != "development" or not active():
        print("The CareFlow demo only runs in development, in demo mode, without real patient data.", file=sys.stderr)
        return 1
    init_db()
    print(json.dumps(careflow_demo_journey.run(real_ai=args.real_ai)))
    return 0


def integrations_migrate(_args) -> int:
    """Creates the technical tables in the separately provisioned YugabyteDB."""
    from backend.integrations import yugabyte

    store = yugabyte.store()
    if store is None:
        print("YugabyteDB isn't switched on. Set ENABLE_YUGABYTEDB_INTEGRATION=1 and YUGABYTEDB_YSQL_DSN.", file=sys.stderr)
        return 1
    print("Migrations up to date.", store.migrate())
    return 0


def readiness(_args) -> int:
    init_db()
    db = connect()
    result = safety.report(db)
    db.close()
    for check in result["checks"]:
        print(f"{'PASS' if check['passed'] else 'FAIL'}  {check['name']}: {check['detail']} ({check['owner']})")
    print(f"\n{result['passed']} of {result['total']} passed. Ready for real patients: {result['ready_for_real_patients']}")
    return 0 if result["ready_for_real_patients"] else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m backend.operator_cli", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, handler, *arguments):
        sub = commands.add_parser(name)
        sub.set_defaults(handler=handler)
        for flags, options in arguments:
            sub.add_argument(*flags, **options)

    required = {"required": True}
    command("generate-key", generate_key, (("--path",), required))
    command("add-key", add_key, (("--key-id",), required))
    command("rewrap-documents", rewrap_documents)
    command("retire-key", retire_key, (("--key-id",), required))
    command("migrate-documents", migrate_documents, (("--dry-run",), {"action": "store_true"}))
    command("delete-migrated-originals", delete_originals, (("--retention-approval",), required))
    command("verify-documents", verify_documents)
    command("backup", backup, (("--to",), required))
    command(
        "restore", restore, (("--source",), required), (("--database",), required), (("--documents",), required)
    )
    command(
        "bootstrap-organization",
        bootstrap_organization,
        (("--admin-email",), required),
        (("--organization",), required),
        (("--type",), {"required": True, "choices": ["clinic", "hospital", "laboratory", "pharmacy"]}),
        (("--evidence-type",), required),
        (("--evidence-reference",), required),
        (("--operator",), required),
        (("--profession",), {"default": "doctor"}),
    )
    command(
        "signoff",
        signoff,
        (("--item",), required),
        (("--signed-by",), required),
        (("--role",), required),
        (("--document-reference",), required),
        (("--operator",), required),
    )
    command("revoke-signoff", revoke_signoff, (("--item",), required), (("--operator",), required))
    command("readiness", readiness)
    command("careflow-deliver", careflow_deliver)
    command("careflow-replay", careflow_replay, (("--channel",), {"choices": ["mongodb", "yugabyte", "meko"]}))
    command("careflow-demo", careflow_demo, (("--real-ai",), {"action": "store_true"}))
    command("integrations-migrate", integrations_migrate)
    command("integrations-status", integrations_status)
    command("integrations-validate", integrations_validate)
    command("mongodb-health", _health_command("mongodb"))
    command("yugabyte-health", _health_command("yugabyte"))
    command("meko-health", _health_command("meko"))
    command("meko-ingest", meko_ingest, (("--dry-run",), {"action": "store_true"}))
    command("meko-seed-memory", meko_seed_memory)
    command(
        "setup-local",
        setup_local,
        (("--home",), {"default": "~/.diagnosia"}),
        (("--env-file",), {"default": ".env"}),
    )
    return root


def main(arguments=None) -> int:
    parsed = parser().parse_args(arguments)
    return parsed.handler(parsed)


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
