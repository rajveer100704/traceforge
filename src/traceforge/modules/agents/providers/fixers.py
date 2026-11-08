"""Deterministic source transforms used by the scripted demo policies.

These operate ONLY on source text the agent observed through ``read_file``. They never touch
oracle material. They exist so the full lifecycle can run without a GPU or API key; they are a
deterministic demo, not a learned model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Edit:
    old: str
    new: str


def _sqli(source: str) -> Edit | None:
    # t0: f-string equality lookup
    m = re.search(
        r"""( *)query = f"(SELECT [^"]*?) = '\{(\w+)\}'"\n( *)return conn\.execute\(query\)""",
        source,
    )
    if m:
        ind, sel, var, ind2 = m.groups()
        new = f'{ind}query = "{sel} = ?"\n{ind2}return conn.execute(query, ({var},))'
        return Edit(m.group(0), new)
    # t1: LIKE concatenation
    m = re.search(
        r"""( *)sql = "(SELECT [^"]*? LIKE) '%" \+ (\w+) \+ "%'([^"]*)"\n( *)return (.*?)conn\.execute\(sql\)""",
        source,
    )
    if m:
        ind, sel, var, tail, ind2, prefix = m.groups()
        new = (
            f'{ind}sql = "{sel} ?{tail}"\n'
            f'{ind2}return {prefix}conn.execute(sql, ("%" + {var} + "%",))'
        )
        return Edit(m.group(0), new)
    # t2: str.format with two values
    m = re.search(
        r"""( *)sql = "(SELECT [^"]*?) = '\{\}' AND (\w+) = '\{\}'([^"]*)"\.format\((\w+), (\w+)\)\n( *)return conn\.execute\(sql\)""",  # noqa: E501
        source,
    )
    if m:
        ind, sel, col2, tail, a, b, ind2 = m.groups()
        new = f'{ind}sql = "{sel} = ? AND {col2} = ?{tail}"\n{ind2}return conn.execute(sql, ({a}, {b}))'
        return Edit(m.group(0), new)
    return None


def _authz(source: str) -> Edit | None:
    # t0: ownership check missing before return
    m = re.search(r"( *)if item is None:\n( *)raise KeyError\(item_id\)\n( *)return item\n", source)
    if m and "requester_id" in source:
        ind = m.group(3)
        new = (
            m.group(0).rsplit(f"{ind}return item\n", 1)[0]
            + f"{ind}if item.owner_id != requester_id:\n"
            + f'{ind}    raise AccessDenied("not the owner")\n'
            + f"{ind}return item\n"
        )
        return Edit(m.group(0), new)
    # t1: update without actor authorisation
    m = re.search(r"( *)profile = profiles\[target_id\]\n", source)
    if m:
        ind = m.group(1)
        new = (
            f"{ind}actor = users.get(actor_id)\n"
            f'{ind}if actor is None or (actor_id != target_id and actor.role != "admin"):\n'
            f'{ind}    raise AccessDenied("not permitted")\n'
            f"{m.group(0)}"
        )
        return Edit(m.group(0), new)
    # t2: trusts client-supplied is_admin flag
    m = re.search(
        r"""( *)if record\.owner_id != request\["actor_id"\] and not request\.get\("is_admin", False\):\n""",
        source,
    )
    if m:
        ind = m.group(1)
        new = (
            f'{ind}actor = users.get(request["actor_id"])\n'
            f'{ind}is_admin = actor is not None and actor.role == "admin"\n'
            f"{ind}if actor is None or (record.owner_id != actor.user_id and not is_admin):\n"
        )
        return Edit(m.group(0), new)
    return None


SAFE_VALIDATE_BODY = '''    """Return url if every resolved destination is a public address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedDestination("scheme not allowed")
    host = parts.hostname
    if not host:
        raise BlockedDestination("missing host")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(a) for a in resolver(host)]
        except ValueError:
            raise BlockedDestination("unresolvable host") from None
    if not addresses:
        raise BlockedDestination("unresolvable host")
    for address in addresses:
        mapped = getattr(address, "ipv4_mapped", None)
        candidate = mapped if mapped is not None else address
        if not candidate.is_global:
            raise BlockedDestination("destination not allowed")
    return url
'''


def _ssrf(source: str) -> list[Edit] | None:
    # t2: redirect target not re-validated
    m_fetch = re.search(r"current = (\w+)\(url, resolver\)", source)
    m_redirect = re.search(
        r"""current = urljoin\(current, response\.headers\["location"\]\)""", source
    )
    if m_fetch and m_redirect:
        fn = m_fetch.group(1)
        new = f'current = {fn}(urljoin(current, response.headers["location"]), resolver)'
        return [Edit(m_redirect.group(0), new)]
    # t0/t1: replace the validation body with resolver-based public-address validation
    m = re.search(
        r"(def (\w+)\(url: str, resolver\) -> str:\n)((?: {4}.*\n|\n)+?)(?=\S|\Z)", source
    )
    if m is None:
        return None
    body = m.group(3).rstrip("\n") + "\n"
    edits = [Edit(m.group(1) + body, m.group(1) + SAFE_VALIDATE_BODY)]
    if "import ipaddress" not in source:
        anchor = "from dataclasses import dataclass, field\n"
        edits.insert(0, Edit(anchor, "import ipaddress\n" + anchor))
    return edits


def reference_edits(source: str) -> list[Edit]:
    """Return the edit list that remediates a recognised synthetic task, or [] if unknown."""
    if "sqlite3" in source:
        edit = _sqli(source)
        return [edit] if edit else []
    if "class AccessDenied" in source:
        edit = _authz(source)
        return [edit] if edit else []
    if "class BlockedDestination" in source:
        return _ssrf(source) or []
    return []


def blocking_statement(source: str) -> str:
    if "class AccessDenied" in source:
        return 'raise AccessDenied("blocked")'
    if "class BlockedDestination" in source:
        return 'raise BlockedDestination("blocked")'
    return "return []"


def guard_edit(source: str, function: str, condition: str) -> Edit | None:
    """Insert ``if <condition>: <block>`` as the first statement of ``function``."""
    m = re.search(rf"(def {re.escape(function)}\(.*\).*:\n)", source)
    if m is None:
        return None
    # Skip a one-line docstring if present.
    rest = source[m.end() :]
    doc = re.match(r'( {4}""".*"""\n)', rest)
    header = m.group(1) + (doc.group(1) if doc else "")
    block = blocking_statement(source)
    new = header + f"    if {condition}:\n        {block}\n"
    return Edit(header, new)
