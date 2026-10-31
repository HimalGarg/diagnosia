"""Whether each integration is really working, worked out by trying it.

A setting never counts as a connection. CONNECTED means the service answered just now, and that it
is the service it's meant to be: a local MongoDB is not Atlas, and PostgreSQL is not YugabyteDB.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

from backend.careflow import stores
from backend.config import settings
from backend.integrations import meko, yugabyte

CONNECTED, DISABLED, BLOCKED, FAILED = "CONNECTED", "DISABLED", "BLOCKED", "FAILED"
MISSING = "BLOCKED_MISSING_CREDENTIALS"


@dataclass
class ServiceStatus:
    name: str
    state: str
    detail: str
    checks: List[Tuple[str, bool, str]] = field(default_factory=list)

    def view(self) -> Dict[str, object]:
        return {"name": self.name, "state": self.state, "detail": self.detail}


def _missing(names: List[str]) -> str:
    return f"{MISSING}: {', '.join(names)}"


def _failure(name: str, error: Exception) -> ServiceStatus:
    """Only the kind of error. Its text can hold an address or a password."""
    return ServiceStatus(name, FAILED, type(error).__name__[:60])


def mongodb(deep: bool = False) -> ServiceStatus:
    name = "MONGODB_ATLAS"
    if not settings.mongodb_enabled:
        return ServiceStatus(name, DISABLED, "ENABLE_MONGODB_ATLAS_INTEGRATION is off")
    if not settings.mongodb_uri:
        return ServiceStatus(name, BLOCKED, _missing(["MONGODB_ATLAS_URI"]))
    try:
        store = stores.mongo_store()
        store.ping()
        checks = store.verify() if deep else []
    except ImportError:
        return ServiceStatus(name, BLOCKED, "Install backend/requirements-integrations.txt")
    except Exception as error:
        stores.reset_mongo()
        return _failure(name, error)
    if store.name != "mongodb_atlas":
        return ServiceStatus(name, BLOCKED, "Connected to a local MongoDB test server, which is not Atlas", checks)
    failed = [check[0] for check in checks if not check[1]]
    return ServiceStatus(name, FAILED, f"Failed: {', '.join(failed)}", checks) if failed else ServiceStatus(name, CONNECTED, f"Atlas database {settings.mongodb_database}", checks)


def yugabytedb(deep: bool = False) -> ServiceStatus:
    name = "YUGABYTEDB"
    if not settings.yugabyte_enabled:
        return ServiceStatus(name, DISABLED, "ENABLE_YUGABYTEDB_INTEGRATION is off")
    if not settings.yugabyte_dsn:
        return ServiceStatus(name, BLOCKED, _missing(["YUGABYTEDB_YSQL_DSN"]))
    try:
        store = yugabyte.store()
        found = store.identity()
        checks = store.verify() if deep else []
    except ImportError:
        return ServiceStatus(name, BLOCKED, "Install backend/requirements-integrations.txt")
    except Exception as error:
        yugabyte.reset()
        return _failure(name, error)
    if not found["is_yugabytedb"]:
        return ServiceStatus(name, FAILED, "The server answered, but it is not YugabyteDB", checks)
    failed = [check[0] for check in checks if not check[1]]
    if failed:
        return ServiceStatus(name, FAILED, f"Failed: {', '.join(failed)}", checks)
    return ServiceStatus(name, CONNECTED, f"YugabyteDB {found['yugabyte_version']}, {found['nodes']} node(s)", checks)


def _meko_checks(client: meko.MekoClient) -> List[Tuple[str, bool, str]]:
    tools = client.tools()
    checks = [("authenticated and tools discovered", bool(tools), f"{len(tools)} tools")]
    wanted = {"knowledgebase_search", "knowledgebase_upload_document", "knowledgebase_document_status", "memory_add", "memory_search", "datapack_describe"}
    absent = sorted(wanted - set(tools))
    checks.append(("tools we use are offered", not absent, f"missing: {absent}" if absent else "all offered"))
    client.describe_datapack()
    checks.append(("datapack accessible", True, "described by the server"))
    found = client.knowledge("Diagnosia workflow")
    checks.append(("knowledge search", found.available, f"{len(found.items)} results" if found.available else found.reason))
    return checks


def mekoservice(deep: bool = False) -> ServiceStatus:
    name = "MEKO"
    if not settings.meko_enabled:
        return ServiceStatus(name, DISABLED, "ENABLE_MEKO_INTEGRATION is off")
    needed = [key for key, value in (("MEKO_API_KEY", settings.meko_api_key), ("MEKO_DATAPACK_ID", settings.meko_datapack_id)) if not value]
    if needed:
        return ServiceStatus(name, BLOCKED, _missing(needed))
    try:
        client = meko.client()
        checks = _meko_checks(client) if deep else []
        tools = client.tools()
    except Exception as error:
        return _failure(name, error)
    failed = [check[0] for check in checks if not check[1]]
    return ServiceStatus(name, FAILED, f"Failed: {', '.join(failed)}", checks) if failed else ServiceStatus(name, CONNECTED, f"{len(tools)} tools discovered", checks)


CHECKS: Dict[str, Callable[..., ServiceStatus]] = {"mongodb": mongodb, "yugabyte": yugabytedb, "meko": mekoservice}


def overview(deep: bool = False) -> List[ServiceStatus]:
    return [check(deep) for check in CHECKS.values()]
