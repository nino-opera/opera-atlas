"""Tests for scripts/validate.py. Standard library only.

Run from the repository folder:  python -m unittest discover -s tests
Each test writes a small valid repository into a temporary folder, breaks one
thing and checks that the validator reports it at the right level.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import validate  # noqa: E402


def L(text):
    return {"ka": text + " ka", "en": text, "it": text + " it"}


def fixture():
    return {
        "data.json": {
            "updated": "2026-10-21",
            "cities": {"tbs": L("Tbilisi")},
            "houses": {"tbs": {"n": "Tbilisi Opera", "c": "tbs", "w": "Tbilisi_Opera"}},
            "events": [{"id": "ev-1", "s": "2026-10-01", "e": "2026-10-05", "h": "tbs", "ty": "run",
                        "t": "Daisi", "c": "", "u": "https://opera.ge/", "n": L("Note")}],
            "news": [{"d": "2026-05", "h": "tbs", "u": "https://opera.ge/n", "t": L("News")}],
            "reviews": [
                {"id": "r-1", "d": "2026-10", "h": "tbs", "w": "Daisi", "src": "Paper",
                 "u": "https://example.com/r", "t": L("Review")},
                {"id": "r-2", "d": "2017", "h": None, "place": "Bergen", "nino": True, "w": "Carmen",
                 "src": "Paper", "u": "https://example.com/r2", "t": L("Review")},
            ],
            "nino": {
                "bio": [{"h": L("Head"), "p": L("Para")}],
                "roles": L("Roles"),
                "timeline": [{"y": "≈2003", "t": L("Debut")},
                             {"y": "2010", "t": L("Award"), "l": [["Link", "https://example.com/l"]]}],
                "awards": [L("Award")],
                "upcoming": [{"t": L("Next"), "l": [["Link", "https://example.com/u"]]}],
                "links": [["Operabase", "https://operabase.com/x"]],
                "videos": [{"q": "Nino Carmen", "t": L("Carmen")}],
                "w": "Nino_Surguladze",
                "img": "images/nino/portrait.jpg",
                "gallery": [{"src": "images/nino/a.jpg", "c": L("Caption")},
                            {"src": "images/nino/b.jpg", "alt": L("Alt")}],
                "rev": {"title": L("Title"), "note": L("Note"), "cards": [
                    {"img": "images/card.jpg", "cap": L("Cap"),
                     "q": [{"o": "Orig", "ol": "it", "t": L("Quote"), "a": "Author", "s": "Paper"}]}]},
            },
            "tbs": {
                "name": L("Name"), "addr": L("Addr"), "phone": "+995", "hours": L("Hours"),
                "links": [["opera.ge", "https://www.opera.ge/geo"]], "intro": L("Intro"),
                "season": [{"id": 305, "g": "opera", "t": L("Daisi"), "c": L("Paliashvili"),
                            "d": ["2026-09-19"], "time": "19:00"},
                           {"id": 307, "g": "ballet", "t": L("Giselle"), "c": None,
                            "d": ["2026-09-25"], "time": "19:00 (27/09 — 15:00)"}],
                "feat": [{"id": 307, "g": "ballet", "t": L("Giselle"), "sub": L("Sub"),
                          "facts": [[L("Conductor"), L("Name")]], "cast": [[L("Role"), L("Name")]],
                          "syn": None, "u": "https://opera.ge/307", "note": L("Note")}],
                "opera": [["Paliashvili", L("Zakaria Paliashvili"), [L("Daisi")]]],
                "ballet": [[L("Chabukiani"), [L("Laurencia")]]],
                "news": [{"d": "2026-10-08", "t": L("News"), "u": "https://opera.ge/news"}],
                "src": "https://www.opera.ge/geo",
            },
        },
        "feed.json": {"updated": "2026-10-21", "items": [
            {"title": "B", "link": "https://example.com/b", "date": "2026-10-21", "source": "X", "nino": False},
            {"title": "A", "link": "https://example.com/a", "date": "2026-10-20", "source": "X", "nino": True},
        ]},
        "singers.json": {"updated": "2026-10-21", "singers": [
            {"id": "maria-callas", "name": L("Maria Callas"), "sort": "Callas", "aliases": ["Kallas"],
             "voice": "soprano", "era": "historic", "born": 1923, "died": 1977, "country": L("Greece"),
             "blurb": L("Blurb"),
             "img": {"src": "https://upload.wikimedia.org/x.jpg", "w": 500, "h": 500, "pos": "50% 20%",
                     "author": "Someone", "lic": "Public domain", "lic_u": "https://commons.wikimedia.org/x",
                     "file": "https://commons.wikimedia.org/x", "sm": "https://upload.wikimedia.org/s.jpg",
                     "smw": 250, "smh": 250}},
            {"id": "nino-surguladze", "to": "nino", "name": L("Nino Surguladze"), "sort": "Surguladze",
             "aliases": [], "voice": "mezzo", "era": "contemporary", "born": None, "died": None,
             "country": L("Georgia"), "blurb": L("Blurb"),
             "img": {"src": "images/nino/portrait.jpg", "w": 560, "h": 700, "pos": "50% 20%"}},
        ]},
        "singers/maria-callas.json": {
            "id": "maria-callas", "name": L("Maria Callas"), "vtype": L("Soprano"), "life": L("1923-1977"),
            "bio": {"ka": ["p1", "p2"], "en": ["p1", "p2"], "it": ["p1", "p2"]},
            "roles": [{"n": "Norma", "c": "Bellini"}], "tl": [{"y": "1923", "t": L("Born")}],
            "notes": [L("Note")], "yt": "Callas Casta diva",
            "src": [{"n": "Britannica", "u": "https://www.britannica.com/x"}],
        },
    }


IMAGES = ["images/nino/portrait.jpg", "images/nino/a.jpg", "images/nino/b.jpg", "images/card.jpg"]


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = fixture()

    def tearDown(self):
        self.tmp.cleanup()

    def run_validator(self, raw=None, images=IMAGES):
        raw = raw or {}
        for rel, content in self.files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            text = raw.get(rel)
            if text is None:
                text = json.dumps(content, ensure_ascii=False, indent=1)
            path.write_text(text, encoding="utf-8")
        for rel in images:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xd8\xff")
        return validate.validate(self.root)

    def assertReported(self, items, where, text):
        hits = [f"{w}: {m}" for w, m in items if where in w and text in m]
        self.assertTrue(hits, f"expected {where!r} / {text!r} in:\n" + "\n".join(f"{w}: {m}" for w, m in items))

    # ---- baseline and accepted exceptions ---------------------------------
    def test_valid_fixture_is_clean(self):
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])

    def test_accepted_exceptions_are_not_errors(self):
        # The fixture already uses every accepted shape: empty composer, review with
        # h=null and place, gallery item with alt, timeline without links, free-form
        # year, season c=null, feat syn=null, singer redirected with "to" and no profile.
        r = self.run_validator()
        self.assertTrue(r.ok(strict=True))

    # ---- syntax ------------------------------------------------------------
    def test_invalid_json_syntax(self):
        r = self.run_validator(raw={"feed.json": '{"updated": "2026-10-21", "items": [}'})
        self.assertReported(r.errors, "feed.json", "invalid JSON")
        self.assertReported(r.errors, "feed.json", "line 1")

    def test_duplicate_key(self):
        r = self.run_validator(raw={"feed.json": '{"updated": "2026-10-21", "updated": "2026-10-22", "items": []}'})
        self.assertReported(r.errors, "feed.json", 'duplicate key "updated"')

    def test_nan_is_rejected(self):
        r = self.run_validator(raw={"feed.json": '{"updated": "2026-10-21", "items": [], "x": NaN}'})
        self.assertReported(r.errors, "feed.json", "NaN is not valid JSON")

    def test_missing_file(self):
        del self.files["feed.json"]
        r = self.run_validator()
        self.assertReported(r.errors, "feed.json", "file not found")

    def test_bom_is_warning(self):
        text = "﻿" + json.dumps(self.files["feed.json"])
        r = self.run_validator(raw={"feed.json": text})
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "feed.json", "BOM")

    # ---- required fields and types ----------------------------------------
    def test_missing_required_field(self):
        del self.files["data.json"]["events"][0]["u"]
        r = self.run_validator()
        self.assertReported(r.errors, "$.events[0]#ev-1", 'missing required field "u"')

    def test_unknown_field_is_warning(self):
        self.files["data.json"]["events"][0]["nmae"] = "typo"
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "$.events[0]", 'unknown field "nmae"')

    def test_duplicate_event_id(self):
        events = self.files["data.json"]["events"]
        events.append(dict(events[0]))
        r = self.run_validator()
        self.assertReported(r.errors, "$.events[1]#ev-1", "duplicate event id")

    def test_bad_enum(self):
        self.files["data.json"]["events"][0]["ty"] = "premiere"
        self.files["singers.json"]["singers"][0]["voice"] = "alto"
        r = self.run_validator()
        self.assertReported(r.errors, ".ty", 'unknown value "premiere"')
        self.assertReported(r.errors, ".voice", 'unknown value "alto"')

    def test_dates(self):
        ev = self.files["data.json"]["events"][0]
        ev["s"], ev["e"] = "2026-10-05", "2026-10-01"
        self.files["data.json"]["news"][0]["d"] = "2026-13"
        self.files["data.json"]["tbs"]["season"][0]["d"] = ["2026-02-30"]
        r = self.run_validator()
        self.assertReported(r.errors, "#ev-1", "is before start date")
        self.assertReported(r.errors, "$.news[0].d", "not a real calendar date")
        self.assertReported(r.errors, "#305.d[0]", "not a real calendar date")

    def test_bad_url(self):
        self.files["data.json"]["events"][0]["u"] = "opera.ge/page"
        r = self.run_validator()
        self.assertReported(r.errors, ".u", "is not an http(s) URL")

    # ---- languages ---------------------------------------------------------
    def test_missing_language(self):
        del self.files["data.json"]["events"][0]["n"]["ka"]
        r = self.run_validator()
        self.assertReported(r.errors, "#ev-1.n", "missing language(s): ka")

    def test_empty_language_text(self):
        self.files["data.json"]["cities"]["tbs"]["it"] = "  "
        r = self.run_validator()
        self.assertReported(r.errors, "$.cities.tbs.it", "empty text")

    def test_paragraph_count_mismatch_is_warning(self):
        self.files["singers/maria-callas.json"]["bio"]["ka"] = ["p1"]
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "$.bio", "different paragraph counts")

    def test_null_language_text_where_not_allowed(self):
        self.files["data.json"]["tbs"]["feat"][0]["note"] = None
        r = self.run_validator()
        self.assertReported(r.errors, ".note", "got null")

    # ---- cities, houses, reviews -----------------------------------------
    def test_unknown_house_and_city(self):
        self.files["data.json"]["events"][0]["h"] = "met"
        self.files["data.json"]["houses"]["tbs"]["c"] = "ny"
        r = self.run_validator()
        self.assertReported(r.errors, "#ev-1.h", 'unknown house "met"')
        self.assertReported(r.errors, "$.houses.tbs.c", 'unknown city "ny"')

    def test_unused_house_is_warning(self):
        self.files["data.json"]["houses"]["met"] = {"n": "Met", "c": "tbs", "w": "Met"}
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "$.houses.met", "not used")

    def test_review_without_house_needs_place(self):
        del self.files["data.json"]["reviews"][1]["place"]
        r = self.run_validator()
        self.assertReported(r.errors, "#r-2", 'needs a "place"')

    # ---- images --------------------------------------------------------------
    def test_missing_image(self):
        r = self.run_validator(images=IMAGES[1:])
        self.assertReported(r.errors, "$.nino.img", 'image "images/nino/portrait.jpg" not found')

    def test_image_case_mismatch(self):
        r = self.run_validator(images=["images/nino/Portrait.JPG"] + IMAGES[1:])
        self.assertReported(r.errors, "$.nino.img", "letter case differs")

    def test_image_path_outside_repo(self):
        self.files["data.json"]["nino"]["img"] = "../secret.jpg"
        r = self.run_validator()
        self.assertReported(r.errors, "$.nino.img", "relative path inside the repository")

    def test_unused_image_is_warning(self):
        r = self.run_validator(images=IMAGES + ["images/old.jpg"])
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "images/ old.jpg", "not used")

    # ---- singers -------------------------------------------------------------
    def test_singer_without_profile(self):
        del self.files["singers/maria-callas.json"]
        r = self.run_validator()
        self.assertReported(r.errors, "#maria-callas", "singers/maria-callas.json not found")

    def test_profile_id_must_match_file(self):
        self.files["singers/maria-callas.json"]["id"] = "callas"
        r = self.run_validator()
        self.assertReported(r.errors, "singers/maria-callas.json $.id", "does not match the file name")

    def test_unknown_redirect_view(self):
        self.files["singers.json"]["singers"][1]["to"] = "ninoo"
        r = self.run_validator()
        self.assertReported(r.errors, "#nino-surguladze.to", 'unknown value "ninoo"')

    def test_singer_without_photo_is_warning(self):
        del self.files["singers.json"]["singers"][0]["img"]
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "#maria-callas", "no photo")

    def test_photo_alt_text_must_be_trilingual(self):
        img = self.files["singers.json"]["singers"][0]["img"]
        img["alt"] = L("Portrait")
        self.assertEqual(self.run_validator().errors, [])
        del img["alt"]["ka"]
        r = self.run_validator()
        self.assertReported(r.errors, "#maria-callas.img.alt", "missing language(s): ka")

    def test_unlisted_profile_is_warning(self):
        self.files["singers/extra.json"] = dict(self.files["singers/maria-callas.json"], id="extra")
        r = self.run_validator()
        self.assertReported(r.warnings, "singers/extra.json", "not listed in singers.json")

    # ---- feed ----------------------------------------------------------------
    def test_feed_order_and_duplicates_are_warnings(self):
        items = self.files["feed.json"]["items"]
        items.reverse()
        items.append(dict(items[0]))
        r = self.run_validator()
        self.assertEqual(r.errors, [])
        self.assertReported(r.warnings, "feed.json", "not sorted newest first")
        self.assertReported(r.warnings, "feed.json", "duplicate link")

    # ---- command line ----------------------------------------------------
    def test_exit_codes(self):
        self.files["data.json"]["houses"]["met"] = {"n": "Met", "c": "tbs", "w": "Met"}
        self.run_validator()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(validate.main(["--root", str(self.root)]), 0)
            self.assertEqual(validate.main(["--root", str(self.root), "--strict"]), 1)
        self.assertIn("WARNINGS (1)", out.getvalue())

    def test_validator_does_not_modify_files(self):
        self.run_validator()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        validate.validate(self.root)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class RepositoryDataTest(unittest.TestCase):
    def test_repository_has_no_errors(self):
        r = validate.validate(REPO)
        self.assertEqual(r.errors, [], "\n".join(f"{w}: {m}" for w, m in r.errors))


if __name__ == "__main__":
    unittest.main()
