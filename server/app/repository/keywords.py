"""What the keyword search looks for in a question. Every repository uses it, so they agree.

A question is mostly filler ("I want to know the article where ..."). Those words say nothing about the topic, and in a
manual they are rare, so BM25 would weigh them heavily: they are dropped before the search.
"""

import re

# What Postgres's English text search configuration drops by itself, which the full-text search relies on and BM25 does not.
ENGLISH_STOP_WORDS = frozenset(
    "i me my myself we our ours ourselves you your yours yourself yourselves he him his himself she her hers herself it its "
    "itself they them their theirs themselves what which who whom this that these those am is are was were be been being "
    "have has had having do does did doing a an the and but if or because as until while of at by for with about against "
    "between into through during before after above below to from up down in out on off over under again further then once "
    "here there when where why how all any both each few more most other some such no nor not only own same so than too "
    "very s t can will just don should now".split()
)
# Words that only ask ("I want to know the article where ..."): they say nothing about the topic.
ASKING_WORDS = frozenset(
    "want know tell show give get got need look find please explain describe description article info information "
    "question answer section chapter page topic mention mentioned refer referred locate say said".split()
)
STOP_WORDS = ENGLISH_STOP_WORDS | ASKING_WORDS
TERM_RE = re.compile(r"[a-z0-9]+")


def keyword_terms(query: str) -> list[str]:
    """The words of `query` worth searching for, in order and without repeats. Empty when the question is all filler."""
    found: dict[str, None] = {}
    for word in TERM_RE.findall(query.lower()):
        if len(word) > 1 and word not in STOP_WORDS:
            found.setdefault(word)
    return list(found)
