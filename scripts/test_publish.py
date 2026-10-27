import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from publish import build, load, valid_catalog


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        source = Path(__file__).resolve().parents[1] / "data"
        for path in source.glob("*.json"):
            (self.root / "data" / path.name).write_bytes(path.read_bytes())
        self.today = dt.date(2026, 10, 28)

    def change(self, name, transform):
        path = self.root / "data" / name
        obj = load(path)
        transform(obj)
        path.write_text(json.dumps(obj))

    def test_valid_package_and_checksum(self):
        import hashlib
        package, metadata = build(self.root, self.today)
        self.assertEqual(package["sha256"], hashlib.sha256(package["payload"].encode()).hexdigest())
        self.assertEqual(170, metadata["flightCount"])
        self.assertIsNone(metadata["timetableVerifiedAt"])

    def test_same_package_is_idempotent(self):
        previous, _ = build(self.root, self.today)
        self.assertEqual(previous, build(self.root, self.today, previous)[0])

    def test_changes_need_version_bump(self):
        previous, _ = build(self.root, self.today)
        self.change("aliases.json", lambda obj: obj.update({"STBY": "SBY"}))
        with self.assertRaisesRegex(ValueError, "new version"):
            build(self.root, self.today, previous)

    def test_downgrade_fails(self):
        previous, _ = build(self.root, self.today)
        previous["version"] += 1
        with self.assertRaisesRegex(ValueError, "rollback"):
            build(self.root, self.today, previous)

    def test_new_version_can_publish_reviewed_change(self):
        previous, _ = build(self.root, self.today)
        self.change("aliases.json", lambda obj: obj.update({"STBY": "SBY"}))
        self.change("release.json", lambda obj: obj.update(version=3))
        self.assertEqual(3, build(self.root, self.today, previous)[0]["version"])

    def test_no_silent_expiry_renewal(self):
        previous, _ = build(self.root, self.today)
        self.change("release.json", lambda obj: obj.update(version=3, validUntil="2027-05-01"))
        with self.assertRaisesRegex(ValueError, "review"):
            build(self.root, self.today, previous)

    def test_bad_time_and_timezone_are_rejected(self):
        catalog = load(self.root / "data/catalog.json")
        bad = copy.deepcopy(catalog)
        bad["flights"]["001"]["std"] = "25:99"
        with self.assertRaises(ValueError):
            valid_catalog(bad)
        bad = copy.deepcopy(catalog)
        bad["airports"]["DOH"]["tz"] = "Unknown/Zone"
        with self.assertRaises(Exception):
            valid_catalog(bad)

    def test_expired_and_future_packages_fail(self):
        for date in (dt.date(2026, 10, 1), dt.date(2027, 5, 1)):
            with self.assertRaisesRegex(ValueError, "expired"):
                build(self.root, date)

    def test_alias_cannot_create_pairing_or_unknown_status(self):
        for value in ("PAIRING", "EMPTY", "UNKNOWN", "ASMT"):
            self.change("aliases.json", lambda obj: obj.update(NEW=value))
            with self.assertRaisesRegex(ValueError, "alias"):
                build(self.root, self.today)

    def test_review_claim_requires_date(self):
        self.change("provenance.json", lambda obj: obj.update(status="reviewed-reference"))
        with self.assertRaisesRegex(ValueError, "review date"):
            build(self.root, self.today)


if __name__ == "__main__":
    unittest.main()
