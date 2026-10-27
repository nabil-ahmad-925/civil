"""The real thing: sql/paradedb/001_nodes_bm25.sql and ParadeDbKeywordSearch against a running ParadeDB.

Skipped unless PARADEDB_TEST_URL is set, e.g.
    docker compose -f paradedb/docker-compose.yml up -d
    PARADEDB_TEST_URL=postgresql://postgres:test@127.0.0.1:55432/nodes_search pytest tests/test_paradedb_integration.py
It works in a throwaway schema (paradedb_test) that it drops at the end, so it does not touch a real `nodes` table.
"""

import os
from pathlib import Path

import pytest

from app.models import Node
from app.repository.memory import MemoryRepository
from app.repository.paradedb import ParadeDbKeywordSearch

URL = os.environ.get("PARADEDB_TEST_URL", "")
psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not URL, reason="set PARADEDB_TEST_URL to a throwaway ParadeDB database to run these")

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "paradedb" / "001_nodes_bm25.sql"
SCHEMA = "paradedb_test"
NODES = [
    ("00000000-0000-0000-0000-000000000001", "PPM", "1-1.8 Content and Development of Electronic Files", "The Master Topographic File contains mapping of existing conditions."),
    ("00000000-0000-0000-0000-000000000002", "PPM", "2-2.8 Erosion Control", "The Master Topographic file (bb file) is maintained by Surveys. Erosion control standards apply."),
    ("00000000-0000-0000-0000-000000000003", "HDM", "Topic 101 Design Speed", "Design speed standards for freeways and local facilities. The standard design speed is selected by the district."),
    ("00000000-0000-0000-0000-000000000004", "HDM", "Topic 102 Culverts", "Culvert design follows the drainage manual. Cleaning of culverts is a maintenance duty."),
    ("00000000-0000-0000-0000-000000000005", "HDM", "Topic 103 Shoulders", "Paved shoulder width on freeways. Shoulder shoulder shoulder."),
]


def connect():
    return psycopg.connect(URL, autocommit=True, options=f"-c search_path={SCHEMA},public")


@pytest.fixture(scope="module")
def search():
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"drop schema if exists {SCHEMA} cascade")
        admin.execute(f"create schema {SCHEMA}")
    with connect() as conn:
        conn.execute(SQL_FILE.read_text())
        for i, manual, title, text in NODES:
            conn.execute("insert into nodes (id, document_id, manual, title, text) values (%s, %s, %s, %s, %s)", (i, i, manual, title, text))
    yield ParadeDbKeywordSearch(URL, connect=connect)
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"drop schema if exists {SCHEMA} cascade")


def ids(hits):
    return [i[-1] for i, _ in hits]  # the last digit of each id is enough to read


def test_a_stemmed_word_finds_the_other_forms(search):
    assert set(ids(search.match("standards", 5))) == {"2", "3"}  # "standard" and "standards"


def test_rare_words_and_several_words(search):
    assert ids(search.match("topographic standards", 5))[0] == "2"  # has both
    assert search.match("pizza", 5) == []


def test_the_manual_filter_and_the_limit(search):
    assert set(ids(search.match("design", 5, "HDM"))) <= {"3", "4"}
    assert len(search.match("shoulder culvert design standards", 2)) == 2


def test_a_word_in_the_heading_counts_more(search):
    [(first, score)] = search.match("erosion", 5)
    assert first.endswith("2") and score > 0


def test_the_score_is_averaged_over_the_words_of_the_question(search):
    [(_, one)] = search.match("culvert", 1)
    [(_, with_unknown)] = search.match("culvert zzzunknown", 1)
    assert with_unknown == pytest.approx(one / 2)


@pytest.mark.parametrize("query", ["standards", "topographic standards", "culvert cleaning", "shoulder width", "design speed standards", "erosion"])
def test_the_in_memory_stand_in_orders_sections_like_the_real_index(search, query):
    memory = MemoryRepository("paradedb")
    memory.insert_nodes([Node(id=i, document_id=i, manual=m, section=i, title=t, page_start=1, page_end=1, ordinal=n, text=x) for n, (i, m, t, x) in enumerate(NODES)])
    assert ids(memory.match_nodes_text(query, 5)) == ids(search.match(query, 5))
