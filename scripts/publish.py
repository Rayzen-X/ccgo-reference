#!/usr/bin/env python3
"""Validate reviewed inputs and build a schema-1 Android reference package. No scraping."""
import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

STATUSES = {"OFF", "REST", "ABLV", "NOSW", "SLVE", "SICK", "DOFF", "AOFF", "TRAIN", "FTG", "SCDR", "GRM", "SBY"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_catalog(catalog):
    require(set(catalog) == {"airports", "flights"}, "unexpected catalog fields")
    airports, flights = catalog["airports"], catalog["flights"]
    require("DOH" in airports and flights, "missing DOH or flights")
    for code, airport in airports.items():
        require(re.fullmatch(r"[A-Z]{3}", code), "invalid airport code")
        require(set(airport) == {"city", "cityEn", "tz"}, "unexpected airport fields")
        require(all(isinstance(x, str) and x for x in airport.values()), "invalid airport text")
        ZoneInfo(airport["tz"])
    for number, flight in flights.items():
        require(re.fullmatch(r"\d{3,4}", number) and int(number) > 0, "invalid flight number")
        require(set(flight) == {"from", "to", "ret", "std", "sta", "arriveNextDay", "redEye", "weekdays"}, "unexpected flight fields")
        require(flight["from"] in airports and flight["to"] in airports and flight["from"] != flight["to"], "invalid route")
        require(re.fullmatch(r"\d{3,4}", flight["ret"]) and int(flight["ret"]) > 0, "invalid return number")
        for key in ("std", "sta"):
            require(re.fullmatch(r"\d{2}:\d{2}", flight[key]), "invalid time")
            dt.time.fromisoformat(flight[key])
        require(type(flight["arriveNextDay"]) is bool and type(flight["redEye"]) is bool, "invalid flight flags")
        require(re.fullmatch(r"[1-7]{1,7}", flight["weekdays"]) and len(set(flight["weekdays"])) == len(flight["weekdays"]), "invalid weekdays")


def build(root, today, previous=None):
    catalog = load(root / "data/catalog.json")
    aliases = load(root / "data/aliases.json")
    release = load(root / "data/release.json")
    provenance = load(root / "data/provenance.json")
    valid_catalog(catalog)
    require(isinstance(aliases, dict) and len(aliases) <= 100, "invalid aliases")
    require(all(re.fullmatch(r"[A-Z]{2,12}", key) and value in STATUSES for key, value in aliases.items()), "invalid alias target")
    require(set(release) == {"version", "validFrom", "validUntil"}, "unexpected release fields")
    require(type(release["version"]) is int and 1 < release["version"] <= 2147483647, "invalid version")
    start, end = (dt.date.fromisoformat(release[key]) for key in ("validFrom", "validUntil"))
    require(start <= today <= end, "package is future-dated or expired")
    require(end - today <= dt.timedelta(days=366), "validity exceeds one year")
    require(provenance["status"] in {"bootstrap-reference", "reviewed-reference"} and provenance["description"], "missing provenance")
    require(provenance["referenceLinks"] and all(url.startswith("https://") for url in provenance["referenceLinks"]), "missing source links")
    verified = provenance["timetableVerifiedAt"]
    if verified is not None:
        require(dt.date.fromisoformat(verified) <= today, "review date is in the future")
    require(provenance["status"] != "reviewed-reference" or verified is not None, "reviewed data needs a review date")
    compact = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    payload = compact({"catalog": compact(catalog), "statusAliases": aliases})
    require(len(payload.encode("utf-8")) <= 2_000_000, "package too large")
    package = {"schema": 1, **release, "payload": payload, "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
    if previous:
        require(package["version"] >= previous["version"], "version rollback")
        require(package["version"] > previous["version"] or package == previous, "changed content or dates need a new version")
        # Changing the expiry alone must never pretend that old schedules were reviewed.
        if end > dt.date.fromisoformat(previous["validUntil"]):
            require(verified is not None and 0 <= (today - dt.date.fromisoformat(verified)).days <= 31,
                    "extending validity requires a recent timetable review")
    return package, {**provenance, "version": package["version"], "validFrom": release["validFrom"],
                     "validUntil": release["validUntil"], "sha256": package["sha256"],
                     "airportCount": len(catalog["airports"]), "flightCount": len(catalog["flights"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    today = dt.datetime.now(ZoneInfo("Asia/Qatar")).date()
    package, metadata = build(args.root, today, load(args.previous) if args.previous else None)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in (("reference.json", package), ("provenance.json", metadata)):
        (args.output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "README.md").write_text(
        "# 丛人去哪了在线参考资料\n\n版本：" + str(package["version"]) + "\n\n" + metadata["description"]
        + "\n\n这是参考资料，不提供个人班表或实时航班保证。维护源码：../tree/main\n", encoding="utf-8")
    print(f"Validated v{package['version']}: {metadata['airportCount']} airports, {metadata['flightCount']} flights; expires {package['validUntil']}")


if __name__ == "__main__":
    main()
