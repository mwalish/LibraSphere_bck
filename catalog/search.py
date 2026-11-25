"""Search adapter. Views call `apply_search(qs, q)`; swap the backend via settings.SEARCH_BACKEND
(later: a Meilisearch backend) without touching any view code."""
import re

from django.conf import settings
from django.db.models import Q


def _basic(qs, q):
    for term in re.findall(r"[\w\-]+", q):
        qs = qs.filter(Q(title__icontains=term) | Q(search_text__icontains=term))
    return qs


def _mysql_fulltext(qs, q):
    terms = re.findall(r"\w+", q)
    if not terms:
        return qs
    boolean = " ".join(f"+{t}*" for t in terms)
    return qs.extra(where=["MATCH(catalog_work.search_text) AGAINST (%s IN BOOLEAN MODE)"], params=[boolean])


BACKENDS = {"basic": _basic, "mysql_fulltext": _mysql_fulltext}


def apply_search(qs, q):
    q = (q or "").strip()
    if not q:
        return qs
    return BACKENDS.get(settings.SEARCH_BACKEND, _basic)(qs, q)
