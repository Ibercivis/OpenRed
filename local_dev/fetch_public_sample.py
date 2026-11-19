#!/usr/bin/env python3
"""
Build the snapshot of public measurements that ``manage.py seed_local`` plants.

Why this exists at all
----------------------
A measurement in OpenRed is a number, a place and a moment: a gamma dose rate at
a coordinate at a time, or a night-sky brightness at a coordinate at a time. All
three parts of both are published as open data, so the half of a development
dataset that ought to be realistic can be rebuilt from a public source instead of
extracted from production.

The other half -- who measured it, with which device, in which project -- is
invented by ``seed_local``. In OpenRed the personal data lives in ``auth_user``,
``account_emailaddress``, ``measures_emaillog`` and the ``owner``/``user``/
``created_by`` columns that point at a user. None of it is copied, so there is
nothing to anonymise.

Two sources, both checked by hand before being chosen
----------------------------------------------------
**Safecast** (https://api.safecast.org) for gamma radiation. Volunteer-collected
CPM readings with coordinates and timestamps. Licence: **CC0 1.0** -- "Safecast
data is published under a CC0 designation ... the data is free and open for
anyone to use under any circumstance" (https://safecast.org/data/). No API key:
verified by request. Attribution is not required and is given anyway.

**Globe at Night** (https://globeatnight.org) for light pollution. Volunteer
naked-eye and SQM night-sky-brightness observations. Licence: **CC BY 4.0** --
"Globe at Night data is made available under a Creative Commons Attribution 4.0
International License" (https://globeatnight.org/maps-data/). Attribution *is*
required, so the snapshot carries it and so does LOCAL-DEV.md. No key needed.

EURDEP and the CSN's REA network were the institutional alternatives. Neither is
used: EURDEP's data is behind a registration for authorised users, and the CSN
publishes its station readings as a web map rather than as a downloadable open
dataset. Safecast is the one of the three that is both open and fetchable, and
unlike the other two it is citizen-collected, which is what OpenRed is.

What is never fetched
---------------------
Both sources name, or can name, the person or the instrument behind a reading:

* Safecast returns ``user_id``, and ``https://api.safecast.org/en-US/users/<id>``
  shows that contributor's name. It also returns ``device_id``, which is one
  physical instrument and therefore its owner, and ``location_name``, free text
  that a contributor typed. The measurement's own ``id`` is a back-reference to a
  page that names the submitter, so **even the record id is dropped** and a
  salted-free one-way ``ref`` is stored instead, only so repeated runs can be
  deduplicated.
* Globe at Night returns ``SQMSerial`` -- a photometer serial number, which is one
  instrument and so one owner -- and ``SkyComment`` and ``LocationComment``, two
  free-text fields that volunteers fill in and which in practice describe where
  they were standing.

``KEEP`` below is an allow-list per source: a field that is not in it is dropped
here, at fetch time, and never reaches this repository or any database. ``PERSONAL``
lists the fields known to carry or resolve to a person, and ``strip()`` asserts
that none of them survived -- which is what makes this a check rather than a hope.
``strip()`` also refuses any kept value that looks like free text, because a
free-text field is where a name turns up when nobody is looking for it.

Usage
-----
    python3 local_dev/fetch_public_sample.py                 # rewrite the snapshot
    python3 local_dev/fetch_public_sample.py --pages 20      # more Safecast records
    python3 local_dev/fetch_public_sample.py --gan-year 2024

The output, ``local_dev/public_sample.json``, is committed. ``seed_local`` reads
it and **never touches the network**.
"""

import argparse
import collections
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

SAFECAST_API = "https://api.safecast.org/measurements.json"
GAN_DOCUMENTS = {
    # Globe at Night publishes one CSV per calendar year, at a stable
    # /documents/<n>/GaN<year>.csv path linked from globeatnight.org/maps-data/.
    2025: "https://globeatnight.org/documents/1190/GaN2025.csv",
    2024: "https://globeatnight.org/documents/926/GaN2024.csv",
    2023: "https://globeatnight.org/documents/661/GaN2023.csv",
}

# The API ignores limit values above this. Checked: limit=25, 100 and 1000 all
# return 25 rows, and `page` is what actually advances.
SAFECAST_PAGE_SIZE = 25

# ---------------------------------------------------------------------------
# Allow-lists. Add a field only after reading what it actually contains.
# ---------------------------------------------------------------------------

KEEP = {
    "safecast": (
        "value",             # the reading
        "unit",              # 'cpm' for everything Iberian
        "latitude",
        "longitude",
        "height",            # metres, often null
        "devicetype_id",     # a MODEL name ("Pointcast V1"), not an instrument
        "captured_at",
    ),
    "globeatnight": (
        "Latitude",
        "Longitude",
        "Elevation(m)",
        "UTDate",
        "UTTime",
        "LimitingMag",       # naked-eye limiting magnitude
        "SQMReading",         # mag/arcsec^2, where the volunteer had a meter
        "CloudCover",
        "Constellation",
        "Country",           # country/region, never finer than that
    ),
}

# Fields known to name a person, to resolve to one, or to be free text a person
# wrote. Never keep one of these. The assertion in strip() fails loudly if one is
# ever added to KEEP by accident.
PERSONAL = {
    "safecast": (
        "user_id",                  # resolves to a named contributor page
        "device_id",                # one physical instrument -> its owner
        "id",                       # back-reference to a page naming the submitter
        "original_id",
        "measurement_import_id",
        "location_name",            # free text typed by a contributor
        "sensor_id",
        "station_id",
        "channel_id",
    ),
    "globeatnight": (
        "ID",
        "SQMSerial",                # photometer serial -> its owner
        "SkyComment",               # free text
        "LocationComment",          # free text, in practice "my back garden at..."
        "LocalDate",                # local clock + place is more identifying than UT
        "LocalTime",
        "ObsType",
    ),
}

# A kept value longer than this, with a space in it, is prose rather than a
# measurement, and prose is where a name hides. Country names and device model
# names are short; "Mojave Trails National Monument, rural" is not.
MAX_TEXT_LEN = 40


def strip(record, source):
    """Reduce a record to KEEP[source], dropping everything else."""
    allowed = KEEP[source]
    personal = set(PERSONAL[source])

    # The allow-list and the deny-list must not overlap. This is the check that
    # makes the whole file trustworthy, so it runs on every record.
    overlap = set(allowed) & personal
    assert not overlap, f"personal field is in KEEP[{source!r}]: {sorted(overlap)}"

    kept = {}
    for name in allowed:
        value = record.get(name)
        if value is None or value == "":
            continue
        if isinstance(value, str) and len(value) > MAX_TEXT_LEN and " " in value:
            raise AssertionError(
                f"kept field {name!r} in {source} looks like free text "
                f"({value[:60]!r}); check what it contains before keeping it"
            )
        kept[name] = value

    assert not set(kept) & personal, f"personal field survived: {set(kept) & personal}"
    return kept


def ref_of(raw_id):
    """
    A short one-way reference for a source record.

    Not the source's own id: that id is a URL to a page that names whoever
    submitted the reading. This is only here so a later run of this script can
    tell whether it has already seen a record.
    """
    return hashlib.sha256(f"openred-local-dev:{raw_id}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Safecast
# ---------------------------------------------------------------------------

# Iberian regions. The frontend's map opens centred on Spain at zoom 5.5
# (src/components/MapComponent.tsx), so a sample outside Iberia would load onto
# an empty map. Safecast's Iberian coverage is real but old -- volunteers drove
# these routes between 2015 and 2025 -- which is why seed_local shifts the dates
# by default. (latitude, longitude, radius in metres)
SAFECAST_REGIONS = {
    "madrid": (40.42, -3.70, 250_000),
    "zaragoza-ebro": (41.65, -0.89, 250_000),
    "barcelona": (41.39, 2.17, 200_000),
    "andalusia": (37.39, -5.98, 250_000),
    "galicia": (42.88, -8.54, 200_000),
    "valencia": (39.47, -0.38, 200_000),
    "canarias": (28.29, -16.63, 200_000),
    "pyrenees": (42.65, 0.60, 150_000),
}


def http_json(url, attempts=3):
    """GET JSON, retrying. The Safecast API returns a 500 on some queries."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
                return json.load(response)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            if attempt == attempts - 1:
                print(f"  giving up on {url[:100]}... ({exc})", file=sys.stderr)
                return []
            time.sleep(2 * (attempt + 1))
    return []


def fetch_safecast(pages):
    """
    Walk Safecast page by page per region, keeping one record per coordinate cell.

    One record per ~1 km cell, because a parked logger emits a reading every few
    seconds from the same spot and a map of those is a single pin.
    """
    out, seen_cells, seen_refs = [], set(), set()
    for region, (lat, lon, distance) in SAFECAST_REGIONS.items():
        kept_here = 0
        for page in range(1, pages + 1):
            params = {
                "limit": SAFECAST_PAGE_SIZE,
                "page": page,
                "latitude": lat,
                "longitude": lon,
                "distance": distance,
            }
            rows = http_json(f"{SAFECAST_API}?{urllib.parse.urlencode(params)}")
            if not rows:
                break
            for row in rows:
                if row.get("latitude") is None or row.get("longitude") is None:
                    continue
                if not row.get("captured_at") or not isinstance(row.get("value"), (int, float)):
                    continue
                if row.get("unit") != "cpm":
                    # Iberian Safecast data is all cpm; anything else is a stray
                    # unit string (the API also emits things like "211").
                    continue
                if row["value"] <= 0:
                    continue
                cell = (round(row["latitude"], 2), round(row["longitude"], 2))
                if cell in seen_cells:
                    continue
                ref = ref_of(row.get("id"))
                if ref in seen_refs:
                    continue
                record = strip(row, "safecast")
                record["ref"] = ref
                record["region"] = region
                seen_cells.add(cell)
                seen_refs.add(ref)
                out.append(record)
                kept_here += 1
        print(f"  safecast/{region}: kept {kept_here} distinct locations")
    out.sort(key=lambda r: r["captured_at"])
    return out


# ---------------------------------------------------------------------------
# Globe at Night
# ---------------------------------------------------------------------------

# Globe at Night is volunteer-entered and not all of it is physically possible.
# Three checks, all of them earned by looking at what the file actually contains:
#
#   * `SQMReading` of 1.0 and 3.0 appear in the 2025 Spanish rows. Night-sky
#     brightness lives between about 16 (inner city) and 22 (pristine) mag per
#     square arcsecond; 1.0 is brighter than daylight. Seeding those unchecked
#     produced a "brightest sky" of 135,074 lux, which is above direct sunlight.
#     A reading outside the plausible band is dropped -- the row is kept, and the
#     seeder falls back to its naked-eye limiting magnitude.
#   * One row has `Latitude` 0.0, in the Gulf of Guinea, filed under Spain.
#     Coordinates are required to be inside the Iberian/Canary box.
#   * `LimitingMag` 0 through 7 are all kept: 0 is a real Globe at Night answer
#     ("no stars visible"), not a missing value.
SQM_PLAUSIBLE = (14.0, 23.0)
# Iberian peninsula, Balearics and Canaries, generously.
IBERIA_BOX = {"lat": (27.0, 44.5), "lon": (-19.0, 5.0)}


def plausible_sqm(text):
    """The SQM reading if it is a possible sky brightness, else None."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    low, high = SQM_PLAUSIBLE
    return value if low <= value <= high else None


def inside_iberia(lat, lon):
    return (
        IBERIA_BOX["lat"][0] <= lat <= IBERIA_BOX["lat"][1]
        and IBERIA_BOX["lon"][0] <= lon <= IBERIA_BOX["lon"][1]
    )


def fetch_globeatnight(year, country_prefix="Spain"):
    """
    Download one Globe at Night yearly CSV and keep the Iberian rows.

    Globe at Night publishes a calendar year at a time and the current year's file
    appears after it ends, so this data is up to a year old however recent the
    file is. seed_local's date shift covers that too.
    """
    url = GAN_DOCUMENTS.get(year)
    if not url:
        raise SystemExit(
            f"no Globe at Night document URL known for {year}. "
            f"Known: {sorted(GAN_DOCUMENTS)}. Find the year's link on "
            f"https://globeatnight.org/maps-data/ and add it to GAN_DOCUMENTS."
        )
    with urllib.request.urlopen(url, timeout=180) as response:  # noqa: S310
        raw = response.read().decode("utf-8-sig", errors="replace")

    out, seen_cells = [], set()
    dropped = collections.Counter()
    for row in csv.DictReader(io.StringIO(raw)):
        if not (row.get("Country") or "").startswith(country_prefix):
            continue
        if not row.get("Latitude") or not row.get("Longitude") or not row.get("UTDate"):
            continue
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except ValueError:
            continue
        if not inside_iberia(lat, lon):
            dropped["coordinates outside Iberia"] += 1
            continue
        if row.get("SQMReading") and plausible_sqm(row["SQMReading"]) is None:
            # Keep the observation, drop the impossible meter reading: the
            # naked-eye limiting magnitude in the same row is still usable.
            dropped["implausible SQM reading"] += 1
            row = dict(row, SQMReading="")
        cell = (round(lat, 2), round(lon, 2), row["UTDate"])
        if cell in seen_cells:
            continue
        record = strip(row, "globeatnight")
        record["ref"] = ref_of(f"gan{year}-{row.get('ID')}")
        seen_cells.add(cell)
        out.append(record)
    out.sort(key=lambda r: (r["UTDate"], r.get("UTTime", "")))
    for reason, number in sorted(dropped.items()):
        print(f"  globeatnight/{year}: dropped {number} rows -- {reason}")
    print(f"  globeatnight/{year}: kept {len(out)} {country_prefix} observations")
    return out


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--pages", type=int, default=12,
        help=f"Safecast pages per region, {SAFECAST_PAGE_SIZE} records each. Default 12.",
    )
    parser.add_argument(
        "--gan-year", type=int, default=2025,
        help="Globe at Night calendar year to download. Default 2025.",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_sample.json"),
    )
    options = parser.parse_args()

    print("fetching radiation records from Safecast (CC0) ...")
    radiation = fetch_safecast(options.pages)
    print("fetching light-pollution records from Globe at Night (CC BY 4.0) ...")
    light = fetch_globeatnight(options.gan_year)

    if not radiation:
        raise SystemExit("no radiation records fetched; refusing to write an empty snapshot")

    snapshot = {
        "built_by": "local_dev/fetch_public_sample.py",
        "retrieved": date.today().isoformat(),
        "note": (
            "Nothing in this file names a person. The fields that identify a "
            "contributor or an instrument (Safecast user_id, device_id and record "
            "id; Globe at Night SQMSerial, SkyComment and LocationComment) are "
            "dropped at fetch time by an allow-list and an assertion, so they "
            "never reach this repository. The people, devices, projects and "
            "missions in the local dataset are invented by seed_local."
        ),
        "sources": {
            "radiation": {
                "name": "Safecast",
                "api": SAFECAST_API,
                "licence": "CC0 1.0 (public domain) -- https://safecast.org/data/",
                "attribution": "Not required under CC0. Data collected by Safecast volunteers.",
                "api_key": "none needed (verified by request)",
                "regions": {k: {"centre": [v[0], v[1]], "radius_m": v[2]}
                            for k, v in SAFECAST_REGIONS.items()},
                "fields_kept": list(KEEP["safecast"]) + ["ref", "region"],
                "records": radiation,
            },
            "light_pollution": {
                "name": "Globe at Night",
                "file": GAN_DOCUMENTS[options.gan_year],
                "year": options.gan_year,
                "licence": "CC BY 4.0 -- https://globeatnight.org/maps-data/",
                "attribution": (
                    "Globe at Night, https://globeatnight.org -- data made available "
                    "under CC BY 4.0. Files generated by Dennis L. Ward, "
                    "(c) Copyright 2007 University Corporation for Atmospheric Research."
                ),
                "api_key": "none needed (plain CSV download, verified by request)",
                "fields_kept": list(KEEP["globeatnight"]) + ["ref"],
                "records": light,
            },
        },
    }

    with open(options.out, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")
    print(f"wrote {options.out}: {len(radiation)} radiation, {len(light)} light-pollution records")


if __name__ == "__main__":
    main()
