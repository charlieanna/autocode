"""Independent SQLite behavior checks for FilteredRelation expression joins."""
import json
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True
workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace))
import django
assert Path(django.__file__).resolve().is_relative_to(workspace)

app = types.ModuleType("arena_probe")
app.__file__ = __file__
app.__path__ = []
sys.modules[app.__name__] = app
from django.conf import settings
settings.configure(
    INSTALLED_APPS=["arena_probe"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    USE_TZ=False,
    SECRET_KEY="arena-local-probe",
)
django.setup()
from django.db import connection, models
from django.db.models import Exists, F, FilteredRelation, OuterRef, Q
from django.db.models.functions import Coalesce


class Author(models.Model):
    name = models.CharField(max_length=80)

    class Meta:
        app_label = "arena_probe"


class Editor(models.Model):
    name = models.CharField(max_length=80)

    class Meta:
        app_label = "arena_probe"


class Book(models.Model):
    title = models.CharField(max_length=80)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    editor = models.ForeignKey(Editor, null=True, on_delete=models.CASCADE)

    class Meta:
        app_label = "arena_probe"


with connection.schema_editor() as schema:
    for model in (Author, Editor, Book):
        schema.create_model(model)

authors = [Author.objects.create(name=name) for name in ("Ada Lovelace", "Grace Hopper", "Alan Turing")]
editors = [Editor.objects.create(name=name) for name in ("Ada", "Grace", "NoMatch")]
books = [
    Book.objects.create(title=title, author=authors[a], editor=None if e is None else editors[e])
    for title, a, e in (
        ("Computing by Ada Lovelace", 0, 0),
        ("Naval computing", 1, 1),
        ("Alan Turing", 2, None),
        ("Ada Lovelace", 1, 2),
        ("Letters", 0, None),
    )
]
checks = []


def check(name, fn):
    try:
        fn()
    except Exception as error:
        checks.append({"name": name, "ok": False, "detail": type(error).__name__ + ": " + str(error)})
    else:
        checks.append({"name": name, "ok": True})


def equal(actual, expected):
    assert actual == expected, (actual, expected)


def ids(query):
    return list(query.order_by("pk").values_list("pk", flat=True))


def cross_join():
    query = Book.objects.annotate(
        matching_author=FilteredRelation("author", condition=Q(author__name__icontains=F("editor__name")))
    ).filter(matching_author__isnull=False)
    equal(ids(query), [books[0].pk, books[1].pk])


def chained_alias():
    query = Book.objects.annotate(
        matching_author=FilteredRelation("author", condition=Q(author__name__icontains=F("editor__name"))),
        repeated_author=FilteredRelation("author", condition=Q(author__name=F("matching_author__name"))),
    ).filter(repeated_author__isnull=False)
    equal(ids(query), [books[0].pk, books[1].pk])


def self_reference():
    query = Book.objects.annotate(
        named_author=FilteredRelation("author", condition=Q(title__icontains=F("author__name")))
    ).filter(named_author__isnull=False)
    equal(ids(query), [books[0].pk, books[2].pk])


def coalesce_join():
    query = Book.objects.annotate(
        fallback_author=FilteredRelation("author", condition=Q(author__name=Coalesce(F("editor__name"), F("title"))))
    ).filter(fallback_author__isnull=False)
    equal(ids(query), [books[2].pk])


def nested_alias_clone():
    inner = Book.objects.annotate(
        matching_author=FilteredRelation("author", condition=Q(author__name__icontains=F("editor__name")))
    ).filter(matching_author__isnull=False, author_id=OuterRef("pk"))
    query = Author.objects.filter(Exists(inner))
    equal(ids(query), [authors[0].pk, authors[1].pk])


def unused_annotation():
    ordinary = Book.objects.all()
    annotated = ordinary.annotate(
        unused_author=FilteredRelation("author", condition=Q(author__name__icontains=F("editor__name")))
    )
    equal(ids(annotated), ids(ordinary))
    equal(annotated.count(), len(books))


def ordinary_preservation():
    query = Book.objects.annotate(
        ada=FilteredRelation("author", condition=Q(author__name="Ada Lovelace"))
    ).filter(ada__isnull=False)
    equal(ids(query), [books[0].pk, books[4].pk])
    equal(ids(Book.objects.filter(author__name="Grace Hopper")), [books[1].pk, books[3].pk])


def independent_multijoins():
    query = Author.objects.annotate(
        computing=FilteredRelation("books", condition=Q(books__title__icontains="computing")),
        letters=FilteredRelation("books", condition=Q(books__title="Letters")),
    ).filter(computing__isnull=False, letters__isnull=False)
    equal(ids(query), [authors[0].pk])
    # Further composition must clone the query without destroying either
    # condition or the already-resolved aliases on the original query.
    equal(ids(query.filter(name__startswith="Grace")), [])
    equal(ids(query), [authors[0].pk])


check("cross_join_rhs", cross_join)
check("chained_aliases", chained_alias)
check("self_reference", self_reference)
check("coalesce_join", coalesce_join)
check("nested_alias_clone", nested_alias_clone)
check("unused_annotation", unused_annotation)
check("ordinary_preservation", ordinary_preservation)
check("independent_multijoins", independent_multijoins)
print(json.dumps({"checks": checks}))
