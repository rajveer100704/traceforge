"""Family A: SQL injection remediation (offline, in-memory SQLite only)."""

from __future__ import annotations

from traceforge.contracts.tasks import Difficulty
from traceforge.modules.tasks.families.base import (
    RenderedTask,
    pick_distractors,
    render,
    rng_for,
    task_markdown,
)

ENTITIES = [
    ("member", "members"),
    ("customer", "customers"),
    ("employee", "staff"),
    ("account", "accounts"),
    ("tenant", "tenants"),
]
KEY_COLS = ["username", "login", "handle", "email"]
VAL_COLS = ["display_name", "department", "tier", "region"]
PEOPLE = ["alice", "bob", "carol", "dmitri", "erin", "farah", "gwen", "hiro", "ines", "jamal"]
QUOTED = ["o'neil", "d'arcy", "o'hara", "l'amour"]
WORDS = ["gold", "silver", "north", "south", "ops", "sales", "red", "blue"]
PRODUCT_SETS = [
    ("apple", ["apple juice", "applesauce", "banana bread", "kid's apple snack", "grape soda"]),
    ("tea", ["green tea", "black tea", "coffee beans", "chef's tea blend", "cocoa"]),
    ("lamp", ["desk lamp", "floor lamp", "office chair", "kid's lamp shade", "monitor"]),
]

T0_MODULE = '''"""$title_entity directory backed by SQLite (synthetic TraceForge task)."""

import sqlite3


def $init_fn(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS $table "
        "(id INTEGER PRIMARY KEY, $key_col TEXT NOT NULL, $val_col TEXT NOT NULL)"
    )


def $add_fn(conn: sqlite3.Connection, $key_col: str, $val_col: str) -> None:
    conn.execute("INSERT INTO $table ($key_col, $val_col) VALUES (?, ?)", ($key_col, $val_col))


def $find_fn(conn: sqlite3.Connection, $key_col: str) -> list[tuple]:
    query = f"SELECT id, $key_col, $val_col FROM $table WHERE $key_col = '{$key_col}'"
    return conn.execute(query).fetchall()


$distractors
'''

T0_VISIBLE = """import sqlite3

from app.$module import $add_fn, $find_fn, $init_fn

ATTACK_PARAM = "$key_col"
ATTACK_VALUE = "' OR '1'='1"


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn)
    $add_fn(conn, "$u1", "$v1")
    $add_fn(conn, "$u2", "$v2")
    return conn


def test_visible_finds_existing():
    rows = $find_fn(_conn(), "$u1")
    assert len(rows) == 1
    assert rows[0][1] == "$u1" and rows[0][2] == "$v1"


def test_visible_missing_returns_empty():
    assert $find_fn(_conn(), "$missing") == []


def test_visible_rejects_basic_attack():
    assert $find_fn(_conn(), ATTACK_VALUE) == []
"""

T0_HIDDEN = """import sqlite3

from app.$module import $add_fn, $find_fn, $init_fn

ROWS = [("$u1", "$v1"), ("$u2", "$v2"), ("$u3", "$v3"), ("$quoted", "$v1")]


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn)
    for key, value in ROWS:
        $add_fn(conn, key, value)
    return conn


def test_func_exact_match():
    rows = $find_fn(_conn(), "$u2")
    assert [(r[1], r[2]) for r in rows] == [("$u2", "$v2")]


def test_func_missing():
    assert $find_fn(_conn(), "$missing") == []


def test_func_quote_in_value():
    rows = $find_fn(_conn(), "$quoted")
    assert len(rows) == 1 and rows[0][1] == "$quoted"


def test_func_no_wildcards():
    assert $find_fn(_conn(), "%") == []


def test_sec_tautology():
    assert $find_fn(_conn(), "' OR '1'='1") == []


def test_sec_comment_bypass():
    assert $find_fn(_conn(), "$u1' OR 1=1 --") == []


def test_sec_union():
    assert $find_fn(_conn(), "x' UNION SELECT 1, name, sql FROM sqlite_master --") == []


def test_sec_stacked_query():
    conn = _conn()
    assert $find_fn(conn, "x'; DROP TABLE $table; --") == []
    assert conn.execute("SELECT COUNT(*) FROM $table").fetchone()[0] == len(ROWS)
"""

T1_MODULE = '''"""Catalogue search for $table (synthetic TraceForge task)."""

import sqlite3


def $init_fn(conn: sqlite3.Connection, names: list[str]) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS $table (id INTEGER PRIMARY KEY, $name_col TEXT)")
    conn.executemany("INSERT INTO $table ($name_col) VALUES (?)", [(n,) for n in names])


def $search_fn(conn: sqlite3.Connection, term: str) -> list[str]:
    sql = "SELECT $name_col FROM $table WHERE $name_col LIKE '%" + term + "%' ORDER BY id"
    return [row[0] for row in conn.execute(sql).fetchall()]


$distractors
'''

T1_VISIBLE = """import sqlite3

from app.$module import $init_fn, $search_fn

ATTACK_PARAM = "term"
ATTACK_VALUE = "' OR 1=1 --"
NAMES = $names


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn, NAMES)
    return conn


def test_visible_search_matches():
    assert $search_fn(_conn(), "$needle") == $expected


def test_visible_no_match():
    assert $search_fn(_conn(), "zzzz") == []


def test_visible_rejects_basic_attack():
    assert $search_fn(_conn(), ATTACK_VALUE) == []
"""

T1_HIDDEN = """import sqlite3

from app.$module import $init_fn, $search_fn

NAMES = $names


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn, NAMES)
    return conn


def test_func_substring_order():
    assert $search_fn(_conn(), "$needle") == $expected


def test_func_apostrophe_term():
    assert $search_fn(_conn(), "$apos_term") == [$apos_name]


def test_func_empty_result():
    assert $search_fn(_conn(), "qqqq") == []


def test_sec_or_true():
    assert $search_fn(_conn(), "x' OR 'a'='a") == []


def test_sec_union_schema_leak():
    assert $search_fn(_conn(), "%' UNION SELECT sql FROM sqlite_master --") == []


def test_sec_comment():
    assert $search_fn(_conn(), "zz' OR 1=1 --") == []
"""

T2_MODULE = '''"""Order ledger queries (synthetic TraceForge task)."""

import sqlite3


def $init_fn(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS $table "
        "(id INTEGER PRIMARY KEY, $owner_col TEXT, status TEXT, $amount_col INTEGER)"
    )


def $add_fn(conn: sqlite3.Connection, $owner_col: str, status: str, $amount_col: int) -> None:
    conn.execute(
        "INSERT INTO $table ($owner_col, status, $amount_col) VALUES (?, ?, ?)",
        ($owner_col, status, $amount_col),
    )


def $list_fn(conn: sqlite3.Connection, $owner_col: str, status: str) -> list[tuple]:
    sql = "SELECT id, $amount_col FROM $table WHERE $owner_col = '{}' AND status = '{}' ORDER BY id".format($owner_col, status)
    return conn.execute(sql).fetchall()


$distractors
'''

T2_VISIBLE = """import sqlite3

from app.$module import $add_fn, $init_fn, $list_fn

ATTACK_PARAM = "$owner_col"
ATTACK_VALUE = "x' OR '1'='1"


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn)
    $add_fn(conn, "$u1", "open", 10)
    $add_fn(conn, "$u1", "closed", 20)
    $add_fn(conn, "$u2", "open", 30)
    return conn


def test_visible_lists_owner_status():
    assert $list_fn(_conn(), "$u1", "open") == [(1, 10)]


def test_visible_rejects_basic_attack():
    assert $list_fn(_conn(), ATTACK_VALUE, "open") == []
"""

T2_HIDDEN = """import sqlite3

from app.$module import $add_fn, $init_fn, $list_fn


def _conn():
    conn = sqlite3.connect(":memory:")
    $init_fn(conn)
    $add_fn(conn, "$u1", "open", 10)
    $add_fn(conn, "$u1", "closed", 20)
    $add_fn(conn, "$u2", "open", 30)
    $add_fn(conn, "$quoted", "open", 40)
    return conn


def test_func_owner_status():
    assert $list_fn(_conn(), "$u1", "closed") == [(2, 20)]


def test_func_quoted_owner():
    assert $list_fn(_conn(), "$quoted", "open") == [(4, 40)]


def test_func_no_rows():
    assert $list_fn(_conn(), "$u2", "closed") == []


def test_sec_status_injection():
    assert $list_fn(_conn(), "$u1", "open' OR '1'='1") == []


def test_sec_owner_comment():
    assert $list_fn(_conn(), "$u1' --", "nonexistent") == []


def test_sec_owner_tautology():
    assert $list_fn(_conn(), "x' OR '1'='1' --", "open") == []
"""


class SqlInjectionFamily:
    family_id = "sql_injection"
    short = "sqli"
    category = "sql_injection_remediation"
    title = "SQL injection remediation"
    templates: tuple[str, ...] = ("t0", "t1", "t2")
    allowed_imports: tuple[str, ...] = ("sqlite3", "typing", "dataclasses", "re", "collections")

    def render(self, template_id: str, seed: int) -> RenderedTask:
        rng = rng_for(self.family_id, template_id, seed)
        entity, table = rng.choice(ENTITIES)
        people = rng.sample(PEOPLE, 4)
        words = rng.sample(WORDS, 3)
        distractors = pick_distractors(rng)
        quoted = rng.choice(QUOTED)
        if template_id == "t0":
            key_col, val_col = rng.choice(KEY_COLS), rng.choice(VAL_COLS)
            find_fn = rng.choice(
                [f"find_{entity}", f"lookup_{entity}", f"get_{entity}_by_{key_col}"]
            )
            values = dict(
                title_entity=entity.capitalize(),
                table=table,
                key_col=key_col,
                val_col=val_col,
                init_fn=rng.choice(["init_schema", "create_tables", "ensure_schema"]),
                add_fn=f"add_{entity}",
                find_fn=find_fn,
                u1=people[0],
                u2=people[1],
                u3=people[2],
                missing=people[3],
                v1=words[0],
                v2=words[1],
                v3=words[2],
                quoted=quoted,
                distractors=distractors,
            )
            module, target = f"{entity}_directory", find_fn
            mod_t, vis_t, hid_t, diff = T0_MODULE, T0_VISIBLE, T0_HIDDEN, Difficulty.EASY
        elif template_id == "t1":
            needle, names = rng.choice(PRODUCT_SETS)
            names = list(names)
            apos_name = next(n for n in names if "'" in n)
            search_fn = rng.choice(["search_catalog", "find_products", "lookup_items"])
            values = dict(
                table=rng.choice(["products", "items", "catalog"]),
                name_col=rng.choice(["name", "title", "label"]),
                init_fn=rng.choice(["load_catalog", "seed_catalog"]),
                search_fn=search_fn,
                names=repr(names),
                needle=needle,
                expected=repr([n for n in names if needle in n]),
                apos_term=apos_name.split(" ")[0],
                apos_name=repr(apos_name),
                distractors=distractors,
            )
            module, target = "catalog_search", search_fn
            mod_t, vis_t, hid_t, diff = T1_MODULE, T1_VISIBLE, T1_HIDDEN, Difficulty.MEDIUM
        elif template_id == "t2":
            owner_col = rng.choice(["customer", "owner", "client"])
            list_fn = rng.choice(["list_orders", "orders_for", "fetch_ledger"])
            values = dict(
                table=rng.choice(["orders", "ledger", "invoices"]),
                owner_col=owner_col,
                amount_col=rng.choice(["amount", "total", "cents"]),
                init_fn=rng.choice(["init_ledger", "create_ledger"]),
                add_fn=rng.choice(["record_order", "add_entry"]),
                list_fn=list_fn,
                u1=people[0],
                u2=people[1],
                quoted=quoted,
                distractors=distractors,
            )
            module, target = "order_ledger", list_fn
            mod_t, vis_t, hid_t, diff = T2_MODULE, T2_VISIBLE, T2_HIDDEN, Difficulty.HARD
        else:
            raise ValueError(f"unknown template {template_id}")
        values["module"] = module
        path = f"app/{module}.py"
        objective = (
            f"The function `{target}` in `{path}` builds SQL by string interpolation and is "
            "vulnerable to SQL injection. Fix it (for example with parameterised queries) "
            "while preserving its legitimate behaviour."
        )
        files = {
            "TASK.md": task_markdown(
                "SQL injection remediation",
                objective,
                path,
                [
                    "Only in-memory SQLite is used; no network services exist.",
                    "Values containing apostrophes are legitimate and must keep working.",
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
