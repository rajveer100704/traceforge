"""Family B: broken access-control remediation (pure in-process toy services)."""

from __future__ import annotations

from traceforge.contracts.tasks import Difficulty
from traceforge.modules.tasks.families.base import (
    RenderedTask,
    pick_distractors,
    render,
    rng_for,
    task_markdown,
)

RESOURCES = [("Document", "doc"), ("Note", "note"), ("Invoice", "invoice"), ("Ticket", "ticket")]
USERS = ["alice", "bob", "carol", "dmitri", "erin", "farah", "gwen", "hiro"]

T0_MODULE = '''"""$Entity storage service (synthetic TraceForge task)."""

from dataclasses import dataclass


class AccessDenied(Exception):
    """Raised when a caller is not permitted to access a resource."""


@dataclass
class $Entity:
    ${short}_id: int
    owner_id: str
    body: str


class $Store:
    def __init__(self) -> None:
        self._items: dict[int, $Entity] = {}

    def add(self, item: $Entity) -> None:
        self._items[item.${short}_id] = item

    def get(self, item_id: int) -> $Entity | None:
        return self._items.get(item_id)


def $read_fn(store: $Store, requester_id: str, item_id: int) -> $Entity:
    item = store.get(item_id)
    if item is None:
        raise KeyError(item_id)
    return item


$distractors
'''

T0_VISIBLE = """from app.$module import AccessDenied, $Entity, $Store, $read_fn

ATTACK_PARAM = "requester_id"
ATTACK_VALUE = "$attacker"


def _store():
    store = $Store()
    store.add($Entity($id1, "$owner", "$body1"))
    return store


def test_visible_owner_can_read():
    assert $read_fn(_store(), "$owner", $id1).body == "$body1"


def test_visible_missing_raises_key_error():
    try:
        $read_fn(_store(), "$owner", 999)
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_visible_rejects_basic_attack():
    try:
        $read_fn(_store(), ATTACK_VALUE, $id1)
    except AccessDenied:
        return
    raise AssertionError("expected AccessDenied")
"""

T0_HIDDEN = """from app.$module import AccessDenied, $Entity, $Store, $read_fn


def _store():
    store = $Store()
    store.add($Entity($id1, "$owner", "$body1"))
    store.add($Entity($id2, "$other", "$body2"))
    return store


def _denied(fn):
    try:
        fn()
    except AccessDenied:
        return True
    except Exception:
        return False
    return False


def test_func_owner_reads_own():
    assert $read_fn(_store(), "$other", $id2).body == "$body2"


def test_func_missing_is_key_error():
    try:
        $read_fn(_store(), "$other", 4242)
    except KeyError:
        return
    raise AssertionError("expected KeyError for missing item")


def test_sec_cross_user_read_denied():
    assert _denied(lambda: $read_fn(_store(), "$other", $id1))


def test_sec_unknown_user_denied():
    assert _denied(lambda: $read_fn(_store(), "$stranger", $id2))


def test_sec_empty_identity_denied():
    assert _denied(lambda: $read_fn(_store(), "", $id1))
"""

T1_MODULE = '''"""Profile management service (synthetic TraceForge task)."""

from dataclasses import dataclass


class AccessDenied(Exception):
    """Raised when a caller is not permitted to perform an action."""


@dataclass
class User:
    user_id: str
    role: str  # "member" or "admin"


@dataclass
class Profile:
    user_id: str
    display_name: str


def $update_fn(users: dict[str, User], profiles: dict[str, Profile], actor_id: str, target_id: str, display_name: str) -> Profile:
    profile = profiles[target_id]
    profile.display_name = display_name
    return profile


$distractors
'''

T1_VISIBLE = """from app.$module import AccessDenied, Profile, User, $update_fn

ATTACK_PARAM = "actor_id"
ATTACK_VALUE = "$attacker"


def _world():
    users = {"$owner": User("$owner", "member"), "$attacker": User("$attacker", "member")}
    profiles = {"$owner": Profile("$owner", "Old"), "$attacker": Profile("$attacker", "Other")}
    return users, profiles


def test_visible_self_update():
    users, profiles = _world()
    assert $update_fn(users, profiles, "$owner", "$owner", "New").display_name == "New"


def test_visible_rejects_basic_attack():
    users, profiles = _world()
    try:
        $update_fn(users, profiles, ATTACK_VALUE, "$owner", "pwned")
    except AccessDenied:
        return
    raise AssertionError("expected AccessDenied")
"""

T1_HIDDEN = """from app.$module import AccessDenied, Profile, User, $update_fn


def _world():
    users = {
        "$owner": User("$owner", "member"),
        "$other": User("$other", "member"),
        "$admin": User("$admin", "admin"),
    }
    profiles = {uid: Profile(uid, uid.upper()) for uid in users}
    return users, profiles


def _denied(fn):
    try:
        fn()
    except AccessDenied:
        return True
    except Exception:
        return False
    return False


def test_func_self_update():
    users, profiles = _world()
    assert $update_fn(users, profiles, "$other", "$other", "Me").display_name == "Me"


def test_func_admin_updates_other():
    users, profiles = _world()
    assert $update_fn(users, profiles, "$admin", "$owner", "ByAdmin").display_name == "ByAdmin"


def test_sec_member_cannot_update_other():
    users, profiles = _world()
    assert _denied(lambda: $update_fn(users, profiles, "$other", "$owner", "x"))
    assert profiles["$owner"].display_name == "$owner".upper()


def test_sec_unknown_actor_denied():
    users, profiles = _world()
    assert _denied(lambda: $update_fn(users, profiles, "$stranger", "$owner", "x"))


def test_sec_unknown_actor_cannot_self_update_missing_user():
    users, profiles = _world()
    profiles["$stranger"] = Profile("$stranger", "S")
    assert _denied(lambda: $update_fn(users, profiles, "$stranger", "$stranger", "x"))
"""

T2_MODULE = '''"""Record deletion endpoint logic (synthetic TraceForge task)."""

from dataclasses import dataclass


class AccessDenied(Exception):
    """Raised when a caller is not permitted to perform an action."""


@dataclass
class User:
    user_id: str
    role: str  # "member" or "admin"


@dataclass
class Record:
    record_id: int
    owner_id: str


def $delete_fn(records: dict[int, Record], users: dict[str, User], request: dict) -> None:
    """request = {"actor_id": str, "record_id": int, "is_admin": bool (client supplied)}"""
    record = records.get(request["record_id"])
    if record is None:
        raise KeyError(request["record_id"])
    if record.owner_id != request["actor_id"] and not request.get("is_admin", False):
        raise AccessDenied("not permitted")
    del records[request["record_id"]]


$distractors
'''

T2_VISIBLE = """from app.$module import AccessDenied, Record, User, $delete_fn

ATTACK_PARAM = "request"
ATTACK_VALUE = {"actor_id": "$attacker", "record_id": $id1, "is_admin": True}


def _world():
    users = {"$owner": User("$owner", "member"), "$attacker": User("$attacker", "member")}
    records = {$id1: Record($id1, "$owner")}
    return records, users


def test_visible_owner_deletes():
    records, users = _world()
    $delete_fn(records, users, {"actor_id": "$owner", "record_id": $id1})
    assert $id1 not in records


def test_visible_rejects_basic_attack():
    records, users = _world()
    try:
        $delete_fn(records, users, dict(ATTACK_VALUE))
    except AccessDenied:
        assert $id1 in records
        return
    raise AssertionError("expected AccessDenied")
"""

T2_HIDDEN = """from app.$module import AccessDenied, Record, User, $delete_fn


def _world():
    users = {
        "$owner": User("$owner", "member"),
        "$other": User("$other", "member"),
        "$admin": User("$admin", "admin"),
    }
    records = {$id1: Record($id1, "$owner"), $id2: Record($id2, "$other")}
    return records, users


def _denied(fn):
    try:
        fn()
    except AccessDenied:
        return True
    except Exception:
        return False
    return False


def test_func_owner_delete():
    records, users = _world()
    $delete_fn(records, users, {"actor_id": "$other", "record_id": $id2})
    assert $id2 not in records


def test_func_real_admin_delete():
    records, users = _world()
    $delete_fn(records, users, {"actor_id": "$admin", "record_id": $id1})
    assert $id1 not in records


def test_func_missing_record():
    records, users = _world()
    try:
        $delete_fn(records, users, {"actor_id": "$owner", "record_id": 777})
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_sec_client_supplied_admin_flag_ignored():
    records, users = _world()
    req = {"actor_id": "$other", "record_id": $id1, "is_admin": True}
    assert _denied(lambda: $delete_fn(records, users, req))
    assert $id1 in records


def test_sec_unknown_actor_with_flag():
    records, users = _world()
    req = {"actor_id": "$stranger", "record_id": $id2, "is_admin": True}
    assert _denied(lambda: $delete_fn(records, users, req))
"""


class AccessControlFamily:
    family_id = "access_control"
    short = "authz"
    category = "access_control_remediation"
    title = "Broken access control remediation"
    templates: tuple[str, ...] = ("t0", "t1", "t2")
    allowed_imports: tuple[str, ...] = ("dataclasses", "typing", "collections", "enum")

    def render(self, template_id: str, seed: int) -> RenderedTask:
        rng = rng_for(self.family_id, template_id, seed)
        owner, other, attacker, admin, stranger = rng.sample(USERS, 5)
        id1, id2 = rng.sample(range(10, 500), 2)
        distractors = pick_distractors(rng)
        values: dict[str, object] = dict(
            owner=owner,
            other=other,
            attacker=attacker,
            admin=admin,
            stranger=stranger,
            id1=id1,
            id2=id2,
            distractors=distractors,
        )
        if template_id == "t0":
            entity, short = rng.choice(RESOURCES)
            read_fn = rng.choice([f"read_{short}", f"get_{short}", f"fetch_{short}"])
            values.update(
                Entity=entity,
                short=short,
                Store=f"{entity}Store",
                read_fn=read_fn,
                body1=rng.choice(["quarterly plan", "salary data", "draft memo"]),
                body2=rng.choice(["meeting notes", "holiday list", "budget"]),
            )
            module, target = f"{short}_service", read_fn
            mod_t, vis_t, hid_t, diff = T0_MODULE, T0_VISIBLE, T0_HIDDEN, Difficulty.EASY
        elif template_id == "t1":
            update_fn = rng.choice(["update_profile", "rename_user", "set_display_name"])
            values.update(update_fn=update_fn)
            module, target = "profile_service", update_fn
            mod_t, vis_t, hid_t, diff = T1_MODULE, T1_VISIBLE, T1_HIDDEN, Difficulty.MEDIUM
        elif template_id == "t2":
            delete_fn = rng.choice(["delete_record", "remove_record", "handle_delete"])
            values.update(delete_fn=delete_fn)
            module, target = "record_api", delete_fn
            mod_t, vis_t, hid_t, diff = T2_MODULE, T2_VISIBLE, T2_HIDDEN, Difficulty.HARD
        else:
            raise ValueError(f"unknown template {template_id}")
        values["module"] = module
        path = f"app/{module}.py"
        objective = (
            f"The function `{target}` in `{path}` has broken authorization. Fix the access-"
            "control logic so callers can only perform actions they are entitled to (owners, or "
            "server-side administrators), without breaking legitimate use. Raise `AccessDenied` "
            "for unauthorized callers."
        )
        files = {
            "TASK.md": task_markdown(
                "Access-control remediation",
                objective,
                path,
                [
                    "Never trust authorization claims supplied by the client.",
                    "Missing resources should keep raising KeyError.",
                ],
            ),
            "app/__init__.py": "",
            path: render(mod_t, **values),
            "tests/test_visible.py": render(vis_t, **values),
        }
        return RenderedTask(
            family_id=self.family_id,
            template_id=template_id,
            seed=seed,
            difficulty=diff,
            module_name=module,
            target_function=target,
            workspace_files=files,
            hidden_tests=render(hid_t, **values),
            allowed_imports=self.allowed_imports,
            params={k: v for k, v in values.items() if k != "distractors"},
        )
