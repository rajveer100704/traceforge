"""Frozen task content for historical task version 1.0.0 (verifier 1.0.0).

Hidden SQL-injection oracle templates exactly as shipped in TraceForge v0.2.0 (commit 39a842a),
kept so rollouts recorded against task version 1.0.0 can be re-verified against their original
hidden tests. Do not edit: ``FIXTURE_HASHES_1_0_0`` pins the fixture hash of every 1.0.0 task as
computed by the original code, and the registry refuses to register a reconstructed 1.0.0
bundle whose hash differs.
"""

from __future__ import annotations

SQL_T0_HIDDEN_1_0_0 = 'import sqlite3\n\nfrom app.$module import $add_fn, $find_fn, $init_fn\n\nROWS = [("$u1", "$v1"), ("$u2", "$v2"), ("$u3", "$v3"), ("$quoted", "$v1")]\n\n\ndef _conn():\n    conn = sqlite3.connect(":memory:")\n    $init_fn(conn)\n    for key, value in ROWS:\n        $add_fn(conn, key, value)\n    return conn\n\n\ndef test_func_exact_match():\n    rows = $find_fn(_conn(), "$u2")\n    assert [(r[1], r[2]) for r in rows] == [("$u2", "$v2")]\n\n\ndef test_func_missing():\n    assert $find_fn(_conn(), "$missing") == []\n\n\ndef test_func_quote_in_value():\n    rows = $find_fn(_conn(), "$quoted")\n    assert len(rows) == 1 and rows[0][1] == "$quoted"\n\n\ndef test_func_no_wildcards():\n    assert $find_fn(_conn(), "%") == []\n\n\ndef test_sec_tautology():\n    assert $find_fn(_conn(), "\' OR \'1\'=\'1") == []\n\n\ndef test_sec_comment_bypass():\n    assert $find_fn(_conn(), "$u1\' OR 1=1 --") == []\n\n\ndef test_sec_union():\n    assert $find_fn(_conn(), "x\' UNION SELECT 1, name, sql FROM sqlite_master --") == []\n\n\ndef test_sec_stacked_query():\n    conn = _conn()\n    assert $find_fn(conn, "x\'; DROP TABLE $table; --") == []\n    assert conn.execute("SELECT COUNT(*) FROM $table").fetchone()[0] == len(ROWS)\n'

SQL_T1_HIDDEN_1_0_0 = 'import sqlite3\n\nfrom app.$module import $init_fn, $search_fn\n\nNAMES = $names\n\n\ndef _conn():\n    conn = sqlite3.connect(":memory:")\n    $init_fn(conn, NAMES)\n    return conn\n\n\ndef test_func_substring_order():\n    assert $search_fn(_conn(), "$needle") == $expected\n\n\ndef test_func_apostrophe_term():\n    assert $search_fn(_conn(), "$apos_term") == [$apos_name]\n\n\ndef test_func_empty_result():\n    assert $search_fn(_conn(), "qqqq") == []\n\n\ndef test_sec_or_true():\n    assert $search_fn(_conn(), "x\' OR \'a\'=\'a") == []\n\n\ndef test_sec_union_schema_leak():\n    assert $search_fn(_conn(), "%\' UNION SELECT sql FROM sqlite_master --") == []\n\n\ndef test_sec_comment():\n    assert $search_fn(_conn(), "zz\' OR 1=1 --") == []\n'

SQL_T2_HIDDEN_1_0_0 = 'import sqlite3\n\nfrom app.$module import $add_fn, $init_fn, $list_fn\n\n\ndef _conn():\n    conn = sqlite3.connect(":memory:")\n    $init_fn(conn)\n    $add_fn(conn, "$u1", "open", 10)\n    $add_fn(conn, "$u1", "closed", 20)\n    $add_fn(conn, "$u2", "open", 30)\n    $add_fn(conn, "$quoted", "open", 40)\n    return conn\n\n\ndef test_func_owner_status():\n    assert $list_fn(_conn(), "$u1", "closed") == [(2, 20)]\n\n\ndef test_func_quoted_owner():\n    assert $list_fn(_conn(), "$quoted", "open") == [(4, 40)]\n\n\ndef test_func_no_rows():\n    assert $list_fn(_conn(), "$u2", "closed") == []\n\n\ndef test_sec_status_injection():\n    assert $list_fn(_conn(), "$u1", "open\' OR \'1\'=\'1") == []\n\n\ndef test_sec_owner_comment():\n    assert $list_fn(_conn(), "$u1\' --", "nonexistent") == []\n\n\ndef test_sec_owner_tautology():\n    assert $list_fn(_conn(), "x\' OR \'1\'=\'1\' --", "open") == []\n'

FIXTURE_HASHES_1_0_0: dict[str, str] = {
    "authz-t0-s000": "a2f26c2eca3c628931ff37a3191ba23c1b90d76071648258ca4e5ca72b894729",
    "authz-t0-s001": "08beb38cd5815934fc75e3e70e852163555151d96d05f7b438a420b513a6c3e2",
    "authz-t0-s002": "9c7257b220eb3daf6381d41b46d15da360b30bbe6561a26b8ebee0a27bbd1cb4",
    "authz-t0-s003": "83c23d2d2c92f441f342fefdfd5ea7b8748e1ebcab3ab93f61749551ffd8ceb2",
    "authz-t0-s004": "cccb7dfb09fdc1f0ac0630769d2d0ecec7509ade7bee3abd8640c65c1a4cdf9d",
    "authz-t1-s000": "e4d0a84739267ca77549d5a35ec32fd41bbf426c6d66e83e88434f5d687684f3",
    "authz-t1-s001": "276992b16d1d275fb52fbd3fd928d9e3b44b1d75ae0c7ce4471502d80274034d",
    "authz-t1-s002": "1563d9e5ca212891e716b6aeca1319079ccba44a25a7e646d1a2fb69ea80289e",
    "authz-t1-s003": "289a7786b0af9ebf4dcf123aad750972f59e04a0171cfd8d4f5d62110b9ddb19",
    "authz-t1-s004": "cdf2a2765109acba4693136ca2291ca94bcec857b630d41bbd66285ce7b5372a",
    "authz-t2-s000": "0c1b7454220fcb49a35276f02542290a10e895b0075f7cf6f4afeebda769ab29",
    "authz-t2-s001": "2b751fef350307a31381007a35c7b9a5e465e8255da8c8ba04b9464f75c0b399",
    "authz-t2-s002": "6b149d46a73a9bd3abbc58470f4436609e8729bfac54526076f48b8583d0f61f",
    "sqli-t0-s000": "924c104f506992ad07b03bb6bd2a3cd86ddc37ad177d9ede572f90b5f88b72c6",
    "sqli-t0-s001": "249c334ac5bb927560c604e9d76a81c19a80cf476a8914cba3021e0e6463b1ea",
    "sqli-t0-s002": "5df10967cf783cf632dbf86b0da8d5c8c8b2173c06d272e8bda5e62aec6503c5",
    "sqli-t0-s003": "645a8417e3455641bc3bcacbfc2134af79e5782bdc5f0e47480da0af4747d6b7",
    "sqli-t0-s004": "947f45ad448b6e8cfe6cff1684c719e2beef27a715f95c6107680978e2d2192a",
    "sqli-t1-s000": "d2ecb702919afbba3cf165b41e1ad3316c61070412b61f183fd597955081427c",
    "sqli-t1-s001": "35ce68e82345bb99cc1c862af160291db0c2ec7adf74672807b4f86a4190842f",
    "sqli-t1-s002": "fb40c396a866b0d479b653e1cf22870d1d05ed61025aa7cdf94a621d46a4214f",
    "sqli-t1-s003": "34ea026fe1d2b279d079f1e5094e454d73cadd347555f344f6472afbf060077e",
    "sqli-t1-s004": "c685f0f3d8b5ccb964fb8b5741346fee50ba2613bd6daf760e15858511164b37",
    "sqli-t2-s000": "73331253c8530b08c2b607b6ace11a275cc03a109628a06fe65817f7a66fcfec",
    "sqli-t2-s001": "da7219029c296a36c74ee451090ae1ac9b2ba0e4ba1c52565201f5a93e467855",
    "sqli-t2-s002": "0751cf36abc56bda8d543a4765e67a454efc9a43717e85b06a40fd64690c2ca7",
    "ssrf-t0-s000": "3ad130bfc85a5384320e36e9833dcee8f24350d5a32b23d84981f4a235abebf1",
    "ssrf-t0-s001": "356cc4fa982fbe9fa897c1b20f8cc4b61622e71a293cfd4bbede72333825747c",
    "ssrf-t0-s002": "7c9c97c9908b08c9cf0f5c10db3b215833620b4d880bddb38e326fc8892d2d62",
    "ssrf-t0-s003": "b149f09e72b47247a9ee33286e8d8a6c304f7b09b386dcda44f7c315145c6809",
    "ssrf-t0-s004": "e93ff92817e9c8a10a63e641edeb88efece2deed697f6e4d11bea218498a5f0f",
    "ssrf-t1-s000": "0449189b3db330b08cba56283241248b38c58c179778f607c7c76acf4a7ceb7b",
    "ssrf-t1-s001": "233addba87c485d9525640a1454177750b26732734ca256d99388c902efc5291",
    "ssrf-t1-s002": "1b4d7a78e48b944c88b957659ddb8e41114bd030f5391231b672b9af823c067d",
    "ssrf-t1-s003": "b474926ebfa59b1164d4ce6f0158ae206869a401d2b9a95900d6e836cf423541",
    "ssrf-t1-s004": "53de5dd73b3156e38e7473d4f90598b5b91bf26a376cff950b4f6e0fab4508af",
    "ssrf-t2-s000": "0687b18cd5b5e7c4fdb0a4cb00d229d1d72a655c477e1bcb1dfdef062fa80989",
    "ssrf-t2-s001": "49c452f2949dbfd9a75aca94cbdfc5cd8847b45b39c54796b79f93f8c3bef3d7",
    "ssrf-t2-s002": "d923cc6ae9c05a22de374f9fc58cf2bbc3c9f2633be562bade53c9f8b5e3c506",
}
