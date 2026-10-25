#!/usr/bin/env python3
"""Data validator for Opera Atlas. Standard library only.

Checks the JSON files the site reads (data.json, feed.json, singers.json and
singers/*.json): JSON syntax, required fields, the three-language texts
(ka / en / it), the links between events, houses and cities, singer profiles,
and that every local image exists with the exact file-name case.

Errors are problems that break the site or leave the data inconsistent.
Warnings are things worth a look. Shapes the current data uses on purpose are
accepted: reviews without a house (they carry "place"), gallery items with
"alt" instead of a caption, events without a composer, timeline entries
without links, free-form years such as "≈2003". The script only reads files;
it never changes or fixes anything.

Usage: python scripts/validate.py [--root DIR] [--strict]
Exit code: 1 if there are errors (or any warnings with --strict), else 0.
"""
import argparse
import codecs
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LANGS = ("ka", "en", "it")
EXTRA_LANGS = ("ru",)  # announced in README; tolerated, not required
EVENT_TYPES = {"new", "world", "revival", "run", "cpremiere"}
VOICES = {"soprano", "mezzo", "contralto", "tenor", "baritone", "bass"}
ERAS = {"historic", "contemporary"}
GENRES = {"opera", "ballet"}
SOURCE_KINDS = {"official", "third-party"}
VIEWS = {"ov", "cal", "pre", "rev", "st", "tbs", "nino"}  # VIEWS in index.html
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg"}
FEED_MAX = 80  # MAX_ITEMS in scripts/update.py

FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PARTIAL_DATE_RE = re.compile(r"^\d{4}(-\d{2}){0,2}$")
URL_RE = re.compile(r"^https?://[^\s/]+\S*$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def kind(v):
    return {dict: "object", list: "list", str: "string", bool: "boolean",
            type(None): "null"}.get(type(v), "number")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.counts = Counter()

    def ok(self, strict=False):
        return not self.errors and not (strict and self.warnings)


class Checker:
    def __init__(self, root, report):
        self.root = Path(root)
        self.r = report
        self.file = ""
        self.images = {}  # local image path -> where it is first used

    # ---- reporting -------------------------------------------------------
    def error(self, path, msg):
        self.r.errors.append((f"{self.file} {path}".strip(), msg))

    def warn(self, path, msg):
        self.r.warnings.append((f"{self.file} {path}".strip(), msg))

    # ---- loading ---------------------------------------------------------
    def load(self, rel, required=True):
        self.file = rel
        path = self.root / rel
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            if required:
                self.error("", "file not found")
            return None
        except OSError as exc:
            self.error("", f"cannot read file: {exc}")
            return None
        if raw.startswith(codecs.BOM_UTF8):
            self.warn("", "file starts with a UTF-8 BOM")
            raw = raw[len(codecs.BOM_UTF8):]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.error("", f"not valid UTF-8 (byte {exc.start})")
            return None
        dups = []

        def pairs(items):
            obj = {}
            for k, v in items:
                if k in obj:
                    dups.append(k)
                obj[k] = v
            return obj

        def bad_constant(name):
            # Python accepts NaN/Infinity, the browser's JSON.parse does not.
            raise ValueError(f"{name} is not valid JSON")

        try:
            data = json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
        except json.JSONDecodeError as exc:
            self.error("", f"invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})")
            return None
        except ValueError as exc:
            self.error("", f"invalid JSON: {exc}")
            return None
        for k in sorted(set(dups)):
            self.error("", f'duplicate key "{k}" (the later value silently wins)')
        self.r.counts["files"] += 1
        return data

    # ---- generic checks (each returns the value when valid, else None) ----
    def get(self, o, key, path, check, *args, **kw):
        if isinstance(o, dict) and key in o:
            return check(o[key], f"{path}.{key}", *args, **kw)
        return None

    def obj(self, v, path, required=(), optional=()):
        if not isinstance(v, dict):
            self.error(path, f"expected an object, got {kind(v)}")
            return None
        for k in required:
            if k not in v:
                self.error(path, f'missing required field "{k}"')
        known = set(required) | set(optional)
        for k in v:
            if k not in known:
                self.warn(path, f'unknown field "{k}"')
        return v

    def arr(self, v, path, nonempty=False):
        if not isinstance(v, list):
            self.error(path, f"expected a list, got {kind(v)}")
            return []
        if nonempty and not v:
            self.warn(path, "list is empty")
        return v

    def text(self, v, path, allow_empty=False):
        if not isinstance(v, str):
            self.error(path, f"expected a string, got {kind(v)}")
            return None
        if not allow_empty and not v.strip():
            self.error(path, "empty text")
            return None
        if v != v.strip():
            self.warn(path, "leading or trailing whitespace")
        return v

    def integer(self, v, path, nullable=False, positive=False):
        if v is None and nullable:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            self.error(path, f"expected an integer, got {kind(v)}")
            return None
        if positive and v <= 0:
            self.error(path, "must be greater than zero")
            return None
        return v

    def boolean(self, v, path):
        if not isinstance(v, bool):
            self.error(path, f"expected true/false, got {kind(v)}")
            return None
        return v

    def enum(self, v, path, allowed, soft=False):
        if v not in allowed:
            msg = f'unknown value {json.dumps(v, ensure_ascii=False)} (allowed: {", ".join(sorted(allowed))})'
            (self.warn if soft else self.error)(path, msg)
            return None
        return v

    def ref(self, v, path, table, what, used):
        if not isinstance(v, str) or v not in table:
            self.error(path, f"unknown {what} {json.dumps(v, ensure_ascii=False)}")
            return None
        used.add(v)
        return v

    def date(self, v, path, partial=False, allow_empty=False):
        if allow_empty and v == "":
            self.warn(path, "date is empty")
            return None
        if not isinstance(v, str):
            self.error(path, f"expected a date string, got {kind(v)}")
            return None
        pattern = PARTIAL_DATE_RE if partial else FULL_DATE_RE
        want = "YYYY, YYYY-MM or YYYY-MM-DD" if partial else "YYYY-MM-DD"
        if not pattern.match(v):
            self.error(path, f'"{v}" is not a {want} date')
            return None
        parts = [int(x) for x in v.split("-")] + [1, 1]
        try:
            date(parts[0], parts[1], parts[2])
        except ValueError:
            self.error(path, f'"{v}" is not a real calendar date')
            return None
        return v

    def url(self, v, path):
        if self.text(v, path) is None:
            return None
        if not URL_RE.match(v):
            self.error(path, f'"{v[:80]}" is not an http(s) URL')
            return None
        if v.startswith("http://"):
            self.warn(path, "plain http:// link (https is preferred)")
        return v

    def link(self, v, path):
        """A [label, url] pair, as used in links lists."""
        if not isinstance(v, list) or len(v) != 2:
            self.error(path, "expected a [label, url] pair")
            return None
        self.text(v[0], f"{path}[0]")
        self.url(v[1], f"{path}[1]")
        return v

    def links(self, v, path):
        for i, x in enumerate(self.arr(v, path)):
            self.link(x, f"{path}[{i}]")

    def lang(self, v, path, paragraphs=False, nullable=False):
        """A text in every site language: {"ka": ..., "en": ..., "it": ...}."""
        if v is None and nullable:
            return None
        self.r.counts["language texts"] += 1
        if not isinstance(v, dict):
            self.error(path, f"expected a {{ka, en, it}} object, got {kind(v)}")
            return None
        for k in v:
            if k not in LANGS and k not in EXTRA_LANGS:
                self.error(path, f'unknown language key "{k}"')
        missing = [k for k in LANGS if k not in v]
        if missing:
            self.error(path, "missing language(s): " + ", ".join(missing))
        sizes = {}
        for k in LANGS + EXTRA_LANGS:
            if k not in v:
                continue
            x, lp = v[k], f"{path}.{k}"
            if paragraphs:
                if not isinstance(x, list) or not x:
                    self.error(lp, "expected a non-empty list of paragraphs")
                    continue
                for i, para in enumerate(x):
                    self.text(para, f"{lp}[{i}]")
                sizes[k] = len(x)
            else:
                self.text(x, lp)
        if len(set(sizes.values())) > 1:
            self.warn(path, "languages have different paragraph counts: "
                      + ", ".join(f"{k}={n}" for k, n in sizes.items()))
        return v

    def image(self, v, path):
        if self.text(v, path) is None:
            return None
        if re.match(r"^[a-z]+:", v):
            return self.url(v, path)
        parts = v.split("/")
        if v.startswith("/") or "\\" in v or ".." in parts or "" in parts:
            self.error(path, f'"{v}" must be a relative path inside the repository')
            return None
        self.images.setdefault(v, f"{self.file} {path}")
        found = exact_path(self.root, parts)
        if found is True:
            return v
        if found is None:
            self.error(path, f'image "{v}" not found')
        else:
            self.error(path, f'image "{v}" exists only as "{found}" (letter case differs: works on '
                             'Windows, breaks on GitHub Pages)')
        return None


def exact_path(root, parts):
    """True if root/parts exists with this exact case, the real spelling if
    only the case differs, None if it does not exist."""
    cur, real = Path(root), []
    for part in parts:
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        if part in names:
            match = part
        else:
            folded = [n for n in names if n.lower() == part.lower()]
            if not folded:
                return None
            match = folded[0]
        real.append(match)
        cur = cur / match
    if not cur.is_file():
        return None
    return True if real == parts else "/".join(real)


def unique(c, seen, value, path, what, soft=False):
    if value is None:
        return
    if value in seen:
        (c.warn if soft else c.error)(path, f"duplicate {what} {json.dumps(value, ensure_ascii=False)}"
                                      f" (first at {seen[value]})")
    else:
        seen[value] = path


def tag(base, i, item, key="id"):
    """Path of a list item, with its id when it has one: $.events[3]#met-mac."""
    p = f"{base}[{i}]"
    if isinstance(item, dict) and isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool):
        p += f"#{item[key]}"
    return p


# ---- data.json -------------------------------------------------------------

def check_data(c, d):
    if not c.obj(d, "$", required=("updated", "cities", "houses", "events", "news", "reviews", "nino", "tbs")):
        return
    c.get(d, "updated", "$", c.date)

    cities = d.get("cities") if isinstance(d.get("cities"), dict) else {}
    if "cities" in d and not isinstance(d["cities"], dict):
        c.error("$.cities", "expected an object keyed by city code")
    for k, v in cities.items():
        c.lang(v, f"$.cities.{k}")
    c.r.counts["cities"] = len(cities)

    houses = d.get("houses") if isinstance(d.get("houses"), dict) else {}
    if "houses" in d and not isinstance(d["houses"], dict):
        c.error("$.houses", "expected an object keyed by house code")
    used_cities, used_houses = set(), set()
    for k, h in houses.items():
        hp = f"$.houses.{k}"
        if c.obj(h, hp, required=("n", "c", "w")):
            c.get(h, "n", hp, c.text)
            c.get(h, "w", hp, c.text)
            c.get(h, "c", hp, c.ref, cities, "city", used_cities)
    c.r.counts["houses"] = len(houses)

    seen = {}
    for i, e in enumerate(c.arr(d.get("events"), "$.events", nonempty=True)):
        ep = tag("$.events", i, e)
        c.r.counts["events"] += 1
        if not c.obj(e, ep, required=("id", "s", "e", "h", "ty", "t", "c", "u", "n"), optional=("src",)):
            continue
        unique(c, seen, c.get(e, "id", ep, c.text), ep, "event id")
        start = c.get(e, "s", ep, c.date)
        end = c.get(e, "e", ep, c.date)
        if start and end and end < start:
            c.error(ep, f'end date "e" ({end}) is before start date "s" ({start})')
        c.get(e, "h", ep, c.ref, houses, "house", used_houses)
        c.get(e, "ty", ep, c.enum, EVENT_TYPES)
        c.get(e, "t", ep, c.text)
        c.get(e, "c", ep, c.text, allow_empty=True)  # ballets and some new works have no composer
        c.get(e, "u", ep, c.url)
        c.get(e, "n", ep, c.lang)
        if "src" in e and c.obj(e["src"], ep + ".src", required=("n", "v", "d")):
            c.get(e["src"], "n", ep + ".src", c.text)
            c.get(e["src"], "v", ep + ".src", c.enum, SOURCE_KINDS, soft=True)
            c.get(e["src"], "d", ep + ".src", c.date)

    for i, n in enumerate(c.arr(d.get("news"), "$.news")):
        np = f"$.news[{i}]"
        c.r.counts["news"] += 1
        if c.obj(n, np, required=("d", "h", "u", "t")):
            c.get(n, "d", np, c.date, partial=True)
            c.get(n, "h", np, c.ref, houses, "house", used_houses)
            c.get(n, "u", np, c.url)
            c.get(n, "t", np, c.lang)

    seen = {}
    for i, r in enumerate(c.arr(d.get("reviews"), "$.reviews")):
        rp = tag("$.reviews", i, r)
        c.r.counts["reviews"] += 1
        if not c.obj(r, rp, required=("id", "d", "w", "src", "u", "t"), optional=("h", "place", "nino")):
            continue
        unique(c, seen, c.get(r, "id", rp, c.text), rp, "review id")
        c.get(r, "d", rp, c.date, partial=True)
        if r.get("h") is not None:
            c.get(r, "h", rp, c.ref, houses, "house", used_houses)
        elif "place" not in r:
            c.error(rp, 'a review without a house ("h") needs a "place"')
        c.get(r, "place", rp, c.text)
        c.get(r, "nino", rp, c.boolean)
        for k in ("w", "src"):
            c.get(r, k, rp, c.text)
        c.get(r, "u", rp, c.url)
        c.get(r, "t", rp, c.lang)

    for k in sorted(set(cities) - used_cities):
        c.warn(f"$.cities.{k}", "city is not used by any house")
    for k in sorted(set(houses) - used_houses):
        c.warn(f"$.houses.{k}", "house is not used by any event, news item or review")

    check_nino(c, d.get("nino"))
    check_tbs(c, d.get("tbs"))


def check_nino(c, n):
    p = "$.nino"
    if not c.obj(n, p, required=("bio", "roles", "timeline", "awards", "upcoming", "links", "videos",
                                 "w", "img", "gallery", "rev")):
        return
    for i, b in enumerate(c.arr(n.get("bio"), p + ".bio", nonempty=True)):
        bp = f"{p}.bio[{i}]"
        if c.obj(b, bp, required=("h", "p")):
            c.get(b, "h", bp, c.lang)
            c.get(b, "p", bp, c.lang)
    c.get(n, "roles", p, c.lang)
    for i, t in enumerate(c.arr(n.get("timeline"), p + ".timeline")):
        tp = f"{p}.timeline[{i}]"
        if c.obj(t, tp, required=("y", "t"), optional=("l",)):
            c.get(t, "y", tp, c.text)  # free form on purpose: "2026", "≈1999–2003"
            c.get(t, "t", tp, c.lang)
            c.get(t, "l", tp, c.links)
    for i, a in enumerate(c.arr(n.get("awards"), p + ".awards")):
        c.lang(a, f"{p}.awards[{i}]")
    for i, u in enumerate(c.arr(n.get("upcoming"), p + ".upcoming")):
        up = f"{p}.upcoming[{i}]"
        if c.obj(u, up, required=("t",), optional=("l",)):
            c.get(u, "t", up, c.lang)
            c.get(u, "l", up, c.links)
    c.get(n, "links", p, c.links)
    for i, v in enumerate(c.arr(n.get("videos"), p + ".videos")):
        vp = f"{p}.videos[{i}]"
        if c.obj(v, vp, required=("q", "t")):
            c.get(v, "q", vp, c.text)
            c.get(v, "t", vp, c.lang)
    c.get(n, "w", p, c.text)
    c.get(n, "img", p, c.image)
    for i, g in enumerate(c.arr(n.get("gallery"), p + ".gallery")):
        gp = f"{p}.gallery[{i}]"
        c.r.counts["gallery photos"] += 1
        if c.obj(g, gp, required=("src",), optional=("c", "alt")):
            c.get(g, "src", gp, c.image)
            if "c" not in g and "alt" not in g:
                c.warn(gp, 'photo has neither a caption "c" nor an "alt" text')
            c.get(g, "c", gp, c.lang)
            c.get(g, "alt", gp, c.lang)
    rp = p + ".rev"
    rev = n.get("rev")
    if c.obj(rev, rp, required=("title", "note", "cards")):
        c.get(rev, "title", rp, c.lang)
        c.get(rev, "note", rp, c.lang)
        for i, card in enumerate(c.arr(rev.get("cards"), rp + ".cards")):
            cp = f"{rp}.cards[{i}]"
            if not c.obj(card, cp, required=("img", "cap", "q")):
                continue
            c.get(card, "img", cp, c.image)
            c.get(card, "cap", cp, c.lang)
            for j, q in enumerate(c.arr(card.get("q"), cp + ".q")):
                qp = f"{cp}.q[{j}]"
                if c.obj(q, qp, required=("o", "t", "s"), optional=("ol", "a")):
                    c.get(q, "o", qp, c.text)
                    c.get(q, "t", qp, c.lang)
                    c.get(q, "s", qp, c.text)
                    c.get(q, "a", qp, c.text)
                    c.get(q, "ol", qp, c.text)


def check_tbs(c, t):
    p = "$.tbs"
    if not c.obj(t, p, required=("name", "addr", "phone", "hours", "links", "intro", "season", "feat",
                                 "opera", "ballet", "news", "src")):
        return
    for k in ("name", "addr", "hours", "intro"):
        c.get(t, k, p, c.lang)
    c.get(t, "phone", p, c.text)
    c.get(t, "links", p, c.links)
    c.get(t, "src", p, c.url)

    season_ids = {}
    for i, s in enumerate(c.arr(t.get("season"), p + ".season")):
        sp = tag(p + ".season", i, s)
        if not c.obj(s, sp, required=("id", "g", "t", "d", "time"), optional=("c",)):
            continue
        unique(c, season_ids, c.get(s, "id", sp, c.integer, positive=True), sp, "playbill id", soft=True)
        c.get(s, "g", sp, c.enum, GENRES)
        c.get(s, "t", sp, c.lang)
        c.get(s, "c", sp, c.lang, nullable=True)  # null for ballets without a composer line
        days = c.get(s, "d", sp, c.arr, nonempty=True) or []
        for j, day in enumerate(days):
            c.date(day, f"{sp}.d[{j}]")
        c.get(s, "time", sp, c.text)  # free form: "19:00 (27/09 — 15:00)"

    for i, f in enumerate(c.arr(t.get("feat"), p + ".feat")):
        fp = tag(p + ".feat", i, f)
        if not c.obj(f, fp, required=("id", "g", "t", "sub", "facts", "cast", "syn", "u", "note"),
                     optional=("tk",)):
            continue
        fid = c.get(f, "id", fp, c.integer, positive=True)
        if fid is not None and fid not in season_ids:
            c.warn(fp, f"featured production {fid} is not in the season list")
        c.get(f, "g", fp, c.enum, GENRES)
        for k in ("t", "sub", "note"):
            c.get(f, k, fp, c.lang)
        c.get(f, "syn", fp, c.lang, nullable=True)  # the page skips the synopsis when null
        for k in ("facts", "cast"):
            for j, pair in enumerate(c.get(f, k, fp, c.arr) or []):
                pp = f"{fp}.{k}[{j}]"
                if isinstance(pair, list) and len(pair) == 2:
                    c.lang(pair[0], pp + "[0]")
                    c.lang(pair[1], pp + "[1]")
                else:
                    c.error(pp, "expected a [label, value] pair of language texts")
        c.get(f, "u", fp, c.url)
        if f.get("tk") is not None:
            c.get(f, "tk", fp, c.url)

    # opera: [composer key, composer name, [works]]; ballet: [author, [works]]
    for k, size in (("opera", 3), ("ballet", 2)):
        for i, row in enumerate(c.arr(t.get(k), f"{p}.{k}")):
            rp = f"{p}.{k}[{i}]"
            if not isinstance(row, list) or len(row) != size:
                c.error(rp, f"expected a list of {size} items")
                continue
            if size == 3:
                c.text(row[0], rp + "[0]")
            c.lang(row[-2], f"{rp}[{size - 2}]")
            for j, w in enumerate(c.arr(row[-1], f"{rp}[{size - 1}]", nonempty=True)):
                c.lang(w, f"{rp}[{size - 1}][{j}]")

    for i, n in enumerate(c.arr(t.get("news"), p + ".news")):
        np = f"{p}.news[{i}]"
        if c.obj(n, np, required=("d", "t", "u")):
            c.get(n, "d", np, c.date, partial=True)
            c.get(n, "t", np, c.lang)
            c.get(n, "u", np, c.url)


# ---- feed.json -------------------------------------------------------------

def check_feed(c, f):
    if not c.obj(f, "$", required=("updated", "items")):
        return
    c.get(f, "updated", "$", c.date)
    items = c.get(f, "items", "$", c.arr) or []
    c.r.counts["feed items"] = len(items)
    if len(items) > FEED_MAX:
        c.warn("$.items", f"{len(items)} items; scripts/update.py keeps at most {FEED_MAX}")
    links, prev = {}, None
    for i, it in enumerate(items):
        ip = f"$.items[{i}]"
        if not c.obj(it, ip, required=("title", "link", "date", "source", "nino")):
            continue
        c.get(it, "title", ip, c.text)
        unique(c, links, c.get(it, "link", ip, c.url), ip, "link", soft=True)
        d = c.get(it, "date", ip, c.date, allow_empty=True)
        c.get(it, "source", ip, c.text)
        c.get(it, "nino", ip, c.boolean)
        if d:
            if prev and d > prev:
                c.warn(ip, f"items are not sorted newest first ({d} after {prev})")
            prev = d


# ---- singers.json and singers/*.json ---------------------------------------

def check_singers(c, s, profiles):
    c.file = "singers.json"
    listed = {}
    if s is not None and c.obj(s, "$", required=("updated", "singers")):
        c.get(s, "updated", "$", c.date)
        seen = {}
        for i, e in enumerate(c.get(s, "singers", "$", c.arr, nonempty=True) or []):
            ep = tag("$.singers", i, e)
            c.r.counts["singers"] += 1
            if not c.obj(e, ep, required=("id", "name", "sort", "aliases", "voice", "era", "born", "died",
                                          "country", "blurb"), optional=("img", "to")):
                continue
            sid = c.get(e, "id", ep, c.text)
            if sid is not None and not SLUG_RE.match(sid):
                c.error(ep + ".id", f'"{sid}" is not a lowercase-hyphen slug (it is used as a file name)')
                sid = None
            unique(c, seen, sid, ep, "singer id")
            for k in ("name", "country", "blurb"):
                c.get(e, k, ep, c.lang)
            c.get(e, "sort", ep, c.text)
            for j, a in enumerate(c.get(e, "aliases", ep, c.arr) or []):
                c.text(a, f"{ep}.aliases[{j}]")
            c.get(e, "voice", ep, c.enum, VOICES)
            c.get(e, "era", ep, c.enum, ERAS)
            born = c.get(e, "born", ep, c.integer, nullable=True)
            died = c.get(e, "died", ep, c.integer, nullable=True)
            if born and died and died < born:
                c.error(ep, f"died ({died}) is before born ({born})")
            if "img" in e:
                check_singer_img(c, e["img"], ep + ".img")
            else:
                c.warn(ep, "no photo; the card shows a monogram")
            to = c.get(e, "to", ep, c.enum, VIEWS)
            if sid is not None:
                listed[sid] = (ep, e, "to" in e)
                if "to" in e and sid in profiles:
                    c.warn(ep, f'has "to": "{to}", so singers/{sid}.json is never opened')

    for sid, (ep, e, redirected) in listed.items():
        if not redirected and sid not in profiles:
            c.file = "singers.json"
            c.error(ep, f"profile file singers/{sid}.json not found")
    for stem, prof in profiles.items():
        c.file = f"singers/{stem}.json"
        if s is not None and stem not in listed:
            c.warn("$", "profile is not listed in singers.json")
        if prof is not None:
            check_profile(c, prof, stem, listed.get(stem, (None, None))[1])


def check_singer_img(c, img, p):
    if not c.obj(img, p, required=("src", "w", "h", "pos"),
                 optional=("author", "lic", "lic_u", "file", "sm", "smw", "smh", "alt")):
        return
    remote = isinstance(img.get("src"), str) and img["src"].startswith(("http://", "https://"))
    c.get(img, "src", p, c.image)
    for k in ("w", "h"):
        c.get(img, k, p, c.integer, positive=True)
    c.get(img, "pos", p, c.text)
    c.get(img, "alt", p, c.lang)
    if remote and not ("author" in img and "lic" in img):
        c.warn(p, 'remote photo without "author" and "lic" credits')
    for k in ("author", "lic"):
        c.get(img, k, p, c.text)
    for k in ("lic_u", "file"):
        c.get(img, k, p, c.url)
    if "sm" in img:
        c.get(img, "sm", p, c.image)
        for k in ("smw", "smh"):
            if k not in img:
                c.warn(p, f'"sm" thumbnail without "{k}"')
            c.get(img, k, p, c.integer, positive=True)


def check_profile(c, d, stem, entry):
    c.r.counts["singer profiles"] += 1
    if not c.obj(d, "$", required=("id", "name", "vtype", "life", "bio", "roles", "tl", "src"),
                 optional=("notes", "yt")):
        return
    pid = c.get(d, "id", "$", c.text)
    if pid is not None and pid != stem:
        c.error("$.id", f'id "{pid}" does not match the file name "{stem}"')
    name = c.get(d, "name", "$", c.lang)
    if name and isinstance(entry, dict) and isinstance(entry.get("name"), dict):
        for k in LANGS:
            if k in name and k in entry["name"] and name[k] != entry["name"][k]:
                c.warn(f"$.name.{k}", f'"{name[k]}" differs from singers.json "{entry["name"][k]}"')
    for k in ("vtype", "life"):
        c.get(d, k, "$", c.lang)
    c.get(d, "bio", "$", c.lang, paragraphs=True)
    for i, r in enumerate(c.get(d, "roles", "$", c.arr, nonempty=True) or []):
        rp = f"$.roles[{i}]"
        if c.obj(r, rp, required=("n", "c")):
            c.get(r, "n", rp, c.text)
            c.get(r, "c", rp, c.text, allow_empty=True)
    for i, t in enumerate(c.get(d, "tl", "$", c.arr, nonempty=True) or []):
        tp = f"$.tl[{i}]"
        if c.obj(t, tp, required=("y", "t")):
            c.get(t, "y", tp, c.text)
            c.get(t, "t", tp, c.lang)
    for i, note in enumerate(c.get(d, "notes", "$", c.arr) or []):
        c.lang(note, f"$.notes[{i}]")
    c.get(d, "yt", "$", c.text)
    for i, s in enumerate(c.get(d, "src", "$", c.arr, nonempty=True) or []):
        sp = f"$.src[{i}]"
        if c.obj(s, sp, required=("n", "u")):
            c.get(s, "n", sp, c.text)
            c.get(s, "u", sp, c.url)


# ---- images ----------------------------------------------------------------

def check_unused_images(c):
    c.file = "images/"
    base = c.root / "images"
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXT:
            c.r.counts["image files"] += 1
            rel = path.relative_to(c.root).as_posix()
            if rel not in c.images:
                c.warn(rel[len("images/"):], "image file is not used by any JSON file")


# ---- entry points ----------------------------------------------------------

def validate(root=ROOT):
    report = Report()
    c = Checker(root, report)
    data = c.load("data.json")
    if data is not None:
        check_data(c, data)
    feed = c.load("feed.json")
    if feed is not None:
        check_feed(c, feed)
    profiles = {}
    folder = c.root / "singers"
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            profiles[path.stem] = c.load(f"singers/{path.name}")
    singers = c.load("singers.json")
    check_singers(c, singers, profiles)
    check_unused_images(c)
    report.counts["local images referenced"] = len(c.images)
    return report


def print_report(report, root, out=None):
    out = out or sys.stdout
    print(f"Opera Atlas data check: {root}", file=out)
    for title, items in (("ERRORS", report.errors), ("WARNINGS", report.warnings)):
        print(f"\n{title} ({len(items)})", file=out)
        for where, msg in items:
            print(f"  {where}: {msg}", file=out)
    print("\nChecked: " + ", ".join(f"{k} {n}" for k, n in report.counts.items()), file=out)
    print(f"Result: {len(report.errors)} error(s), {len(report.warnings)} warning(s)", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate the Opera Atlas JSON data.")
    ap.add_argument("--root", type=Path, default=ROOT, help="repository folder (default: this repository)")
    ap.add_argument("--strict", action="store_true", help="also fail when there are warnings")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    report = validate(args.root)
    print_report(report, args.root)
    return 0 if report.ok(args.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
