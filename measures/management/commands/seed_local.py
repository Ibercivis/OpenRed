"""
Seed a small local dataset for development.

**Nothing is copied from production, and nothing needs to be.** The dataset has
two halves, and only one of them is real:

* **The measurements are real public records.** Gamma readings come from
  Safecast (CC0) and night-sky-brightness observations from Globe at Night
  (CC BY 4.0), both via the committed snapshot ``local_dev/public_sample.json``.
  Real values, at the coordinates in Spain where they were really taken.
  ``local_dev/fetch_public_sample.py`` built that snapshot behind an allow-list,
  so the fields naming a contributor or an instrument never reached this
  repository.

* **Everybody and everything else is invented.** The accounts, their addresses
  (all on the reserved ``.invalid`` domain, so none can receive mail), the
  devices and their serial numbers, the projects, missions, campaigns and
  stations are made up here.

That split is the point: in OpenRed the personal data lives in ``auth_user``,
``account_emailaddress``, ``measures_emaillog`` and the ``owner`` / ``user`` /
``created_by`` columns that point at a user. The measurements, tracks, spectra and
weather rows carry nothing about anybody. So the half of a development dataset
that should be realistic is exactly the half that can be rebuilt from a public
source, and the half that would have carried personal data is the half that is
cheapest to invent. There is nothing to anonymise because nothing is copied.
This matches ADR-0009 in citsci-federation: "Everything in it is fixtures".

What is real and what is not, field by field, is in LOCAL-DEV.md. The short
version: values, coordinates and (unless shifted) timestamps are real;
device serials, spectra, weather and the grouping of points into tracks are not.

Usage:

    python manage.py seed_local
    python manage.py seed_local --measurements 200
    python manage.py seed_local --dates real
    python manage.py seed_local --reset
"""
# pylint: disable=too-many-locals,too-many-statements,too-many-instance-attributes

import json
import math
import os
import random
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from devices.models import Device, DeviceModel
from measures.models import (
    LightPollutionMeasurement,
    RadiationMeasurement,
    Spectrum,
    Station,
    Track,
    WeatherCache,
)
from missions.models import Campaign, Mission, Project

SNAPSHOT = os.path.join(settings.BASE_DIR, "local_dev", "public_sample.json")

# RFC 2606 reserves `.invalid`, so none of these addresses can ever resolve.
EMAIL_DOMAIN = "openred.invalid"

PASSWORD = "devpass12345"

# Invented people: handle, first, last
SUPERUSER = ("admin", "Local", "Admin")
STEWARD = ("rosa", "Rosa", "Villanueva")
VOLUNTEERS = [
    ("ada", "Ada", "Monegros"),
    ("bruno", "Bruno", "Estrada"),
    ("chiara", "Chiara", "Luce"),
    ("dani", "Dani", "Aliaga"),
]

# Safecast's bGeigie Nano uses an LND 7317 pancake tube, for which Safecast's own
# conversion is 334 CPM per uSv/h. The snapshot's readings are all CPM, so this is
# what turns a real count rate into the dose rate OpenRed stores and colours maps
# by. It is a real, documented instrument constant, not a guess -- but it is the
# constant for *that* tube, so a reading taken with another detector would convert
# differently. Good enough for a development dataset; stated here so nobody mistakes
# the dose rates for calibrated ones.
CPM_PER_USV_H = 334.0

# Night-sky brightness -> illuminance.
#   Luminance:    L [cd/m2] = 1.08e5 * 10^(-0.4 * m)       (m in mag/arcsec^2)
#   Illuminance:  E [lux]   = pi * L                        (uniform hemisphere)
# Standard photometric identities, so the lux values follow from the real
# measured sky brightness rather than being invented.
SKY_LUMINANCE_COEFF = 1.08e5

# Naked-eye limiting magnitude -> sky brightness, Schaefer's relation inverted:
#   m = 21.58 - 5 * log10(10^((8.89 - nelm)/5) - 1)
# Used only for the Globe at Night rows that have no SQM meter reading, so that
# every observation gets a brightness. Approximate by nature; flagged per row.
NELM_REFERENCE = 21.58
NELM_ZEROPOINT = 8.89


def quantize_coord(value):
    """Both coordinate columns are DecimalField(decimal_places=8)."""
    return Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def parse_safecast_time(text):
    """'2014-08-01T16:51:49.000Z' -> aware datetime."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(dt_timezone.utc)


def sky_brightness_from_nelm(nelm):
    """Approximate mag/arcsec^2 from a naked-eye limiting magnitude."""
    inner = 10 ** ((NELM_ZEROPOINT - nelm) / 5.0) - 1.0
    if inner <= 0:
        return NELM_REFERENCE
    return NELM_REFERENCE - 5.0 * math.log10(inner)


def lux_from_sky_brightness(mag_per_arcsec2):
    """mag/arcsec^2 -> lux, via luminance."""
    luminance = SKY_LUMINANCE_COEFF * (10 ** (-0.4 * mag_per_arcsec2))
    return math.pi * luminance


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def synthetic_spectrum(channel_count, total_counts, rng):
    """
    A plausible gamma spectrum: Compton-like continuum plus a K-40 photopeak.

    **Invented, not measured.** Neither Safecast nor Globe at Night publishes
    channel data, and a spectrum carries nothing about anybody, so this is the
    cheap half to make up. The shape is physically sensible -- a falling continuum
    with a Gaussian line at 1460 keV, which is what a CsI(Tl) detector sees from
    natural potassium -- so the spectrum screens have something recognisable to
    draw, but no conclusion should be drawn from these numbers.
    """
    # E(keV) = a0 + a1*ch (+ a2*ch^2); a 1024-channel detector to ~3 MeV.
    a0, a1, a2 = 0.0, 3.0, 0.0
    k40_channel = (1460.0 - a0) / a1
    sigma = 18.0

    counts = []
    for channel in range(channel_count):
        energy = a0 + a1 * channel
        continuum = math.exp(-energy / 420.0) if energy > 0 else 0.0
        peak = 0.22 * math.exp(-((channel - k40_channel) ** 2) / (2 * sigma ** 2))
        weight = continuum + peak
        counts.append(weight)

    total_weight = sum(counts) or 1.0
    scaled = []
    for weight in counts:
        expected = total_counts * weight / total_weight
        # Poisson-ish jitter so the spectrum is not suspiciously smooth.
        scaled.append(max(0, int(round(rng.gauss(expected, math.sqrt(max(expected, 0.0001)))))))
    return a0, a1, a2, scaled


class Command(BaseCommand):
    """Create a small, entirely local development dataset."""

    help = (
        "Seed a small local dataset: invented accounts, devices, projects and "
        "stations plus real public radiation (Safecast, CC0) and light-pollution "
        "(Globe at Night, CC BY 4.0) measurements. Copies nothing from production."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--measurements",
            type=int,
            default=240,
            help="Radiation measurements to plant across the tracks. Default 240.",
        )
        parser.add_argument(
            "--dates",
            choices=["shifted", "real"],
            default="shifted",
            help=(
                "Both public sources publish with a lag -- Safecast's Iberian "
                "coverage is 2014-2025 and Globe at Night publishes a calendar "
                "year at a time -- so with real dates every screen that counts "
                "'this month' shows zero. 'shifted' (the default) slides the whole "
                "set forward as a block, keeping the real intervals, so the newest "
                "record lands today. 'real' keeps the sources' own dates. The "
                "station time series is always recent: see --help output of "
                "LOCAL-DEV.md for why."
            ),
        )
        parser.add_argument(
            "--password",
            default=PASSWORD,
            help=f"Password for every seeded account. Default {PASSWORD!r}.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20261111,
            help="Random seed, so two runs produce the same invented data.",
        )
        parser.add_argument(
            "--no-spectra",
            action="store_true",
            help="Skip the synthesised gamma spectra (faster).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete what a previous run created, then exit.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Proceed even though the database holds accounts this command did "
                "not create. Without it, seeding refuses to touch such a database."
            ),
        )

    # -- guards ------------------------------------------------------------

    def check_safe_to_run(self, force):
        """
        Refuse to run anywhere that might not be a development database.

        Belt and braces, because the thing this command must never do is write to
        production. `create_test_project.py`-style commands in these repositories
        have no such guard; this one does.
        """
        if not settings.DEBUG:
            raise CommandError(
                "DEBUG is False. seed_local only runs against a development "
                "database. If this really is a development environment, set "
                "DEBUG=True."
            )

        seeded_domain = f"@{EMAIL_DOMAIN}"
        foreign = User.objects.exclude(email__iendswith=seeded_domain).count()
        if foreign and not force:
            raise CommandError(
                f"this database holds {foreign} account(s) that seed_local did not "
                f"create (their addresses are not on {EMAIL_DOMAIN}). Refusing to "
                f"touch it. Use --force if you are certain this is a throwaway "
                f"database."
            )

    # -- reset -------------------------------------------------------------

    def do_reset(self):
        """Remove what a previous run created."""
        accounts = User.objects.filter(email__iendswith=f"@{EMAIL_DOMAIN}")
        handles = list(accounts.values_list("username", flat=True))

        # Measurements first: their FKs to user/device are SET_NULL, so deleting
        # the accounts would orphan them rather than remove them.
        counts = {
            "radiation measurements": RadiationMeasurement.objects.all().delete()[0],
            "light-pollution measurements": LightPollutionMeasurement.objects.all().delete()[0],
            "spectra": Spectrum.objects.all().delete()[0],
            "tracks": Track.objects.all().delete()[0],
            "stations": Station.objects.all().delete()[0],
            "campaigns": Campaign.objects.all().delete()[0],
            "missions": Mission.objects.all().delete()[0],
            "projects": Project.objects.all().delete()[0],
            "devices": Device.objects.all().delete()[0],
            "device models": DeviceModel.objects.all().delete()[0],
            "weather cache rows": WeatherCache.objects.all().delete()[0],
            "accounts": accounts.delete()[0],
        }
        for label, number in counts.items():
            self.stdout.write(f"  removed {number} {label}")
        self.stdout.write(self.style.SUCCESS(f"reset done (accounts: {', '.join(handles) or 'none'})"))
        self.stdout.write(
            "media/ is left alone; it is gitignored, delete it by hand if you want it empty."
        )

    # -- people ------------------------------------------------------------

    def make_person(self, handle, first, last, password, superuser=False, staff=False):
        email = f"{handle}@{EMAIL_DOMAIN}"
        user, _ = User.objects.get_or_create(
            username=handle,
            defaults={"email": email, "first_name": first, "last_name": last},
        )
        user.email = email
        user.first_name, user.last_name = first, last
        user.is_superuser = superuser
        user.is_staff = superuser or staff
        user.set_password(password)
        user.save()

        # allauth's ACCOUNT_EMAIL_VERIFICATION is 'mandatory' in settings.py, as in
        # production. Give each account a confirmed EmailAddress so it can sign in
        # at once instead of waiting for a mail that goes to the console.
        try:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.update_or_create(
                user=user,
                email=email,
                defaults={"verified": True, "primary": True},
            )
        except ImportError:  # pragma: no cover - allauth is installed
            pass

        # The API authenticates with a DRF token (REST_AUTH TOKEN_MODEL).
        try:
            from rest_framework.authtoken.models import Token

            Token.objects.get_or_create(user=user)
        except ImportError:  # pragma: no cover
            pass
        return user

    # -- main --------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            self.check_safe_to_run(options["force"])
            self.do_reset()
            return

        self.check_safe_to_run(options["force"])

        if not os.path.exists(SNAPSHOT):
            raise CommandError(
                f"snapshot not found at {SNAPSHOT}. Build it with:\n"
                f"    python3 local_dev/fetch_public_sample.py"
            )
        with open(SNAPSHOT, encoding="utf-8") as handle:
            snapshot = json.load(handle)

        rng = random.Random(options["seed"])
        password = options["password"]
        shift_dates = options["dates"] == "shifted"

        radiation_records = snapshot["sources"]["radiation"]["records"]
        light_records = snapshot["sources"]["light_pollution"]["records"]
        if not radiation_records:
            raise CommandError("the snapshot holds no radiation records")

        self.stdout.write(
            f"snapshot retrieved {snapshot.get('retrieved')}: "
            f"{len(radiation_records)} radiation, {len(light_records)} light-pollution records"
        )

        # --- the date shift ------------------------------------------------
        # Slide the whole set forward as a block so the newest record is today,
        # keeping every real interval between records. Applied to the measurement
        # timestamps that came from the sources, not to the station series, which
        # is generated relative to now in the first place.
        now = timezone.now()
        newest = max(parse_safecast_time(r["captured_at"]) for r in radiation_records)
        radiation_offset = (now - newest) if shift_dates else timedelta(0)

        newest_light = max(
            datetime.strptime(f"{r['UTDate']} {r.get('UTTime', '00:00')}", "%Y-%m-%d %H:%M")
            .replace(tzinfo=dt_timezone.utc)
            for r in light_records
        ) if light_records else now
        light_offset = (now - newest_light) if shift_dates else timedelta(0)

        if shift_dates:
            self.stdout.write(
                f"dates shifted forward by {radiation_offset.days} days (radiation) "
                f"and {light_offset.days} days (light pollution); "
                f"use --dates real to keep the sources' own dates"
            )

        # --- the site --------------------------------------------------------
        Site.objects.update_or_create(
            id=settings.SITE_ID,
            defaults={"domain": "localhost:8020", "name": "OpenRed (local)"},
        )

        # --- people ----------------------------------------------------------
        admin = self.make_person(*SUPERUSER, password=password, superuser=True)
        rosa = self.make_person(*STEWARD, password=password, staff=True)
        volunteers = [self.make_person(*v, password=password) for v in VOLUNTEERS]
        self.stdout.write(f"  people: {2 + len(volunteers)} invented accounts")

        # --- device models ---------------------------------------------------
        # Real product names from real manufacturers: public facts about hardware,
        # nothing about anybody. The serial numbers below are invented.
        models_spec = [
            ("bGeigie Nano", "Safecast", "1.5", "Geiger-Muller (LND 7317)", 1000.0, True),
            ("RadiaCode 103", "Scan-Electronics", "1.0", "CsI(Tl) scintillator", 1000.0, True),
            ("GammaStation M5", "Ibercivis", "0.3", "Geiger-Muller (J305)", 500.0, False),
            ("SQM-LE", "Unihedron", "2.0", "Sky Quality Meter (TSL237)", 0.0, True),
        ]
        device_models = {}
        for name, maker, version, tech, ceiling, validated in models_spec:
            obj, _ = DeviceModel.objects.update_or_create(
                name=name,
                manufacturer=maker,
                defaults={
                    "version": version,
                    "technology": tech,
                    "max_radiation_range": ceiling,
                    "validatedByOpenRed": validated,
                    "description": f"Invented local-development record for the {name}.",
                },
            )
            device_models[name] = obj

        # --- devices ---------------------------------------------------------
        # Serial numbers are invented and marked LOCAL- so nobody mistakes one for
        # a real instrument. A serial ties a measurement to a device and a device
        # to an owner, which is exactly why no real serial is used here.
        devices_spec = [
            ("LOCAL-BGN-0001", "bGeigie Nano", volunteers[0]),
            ("LOCAL-BGN-0002", "bGeigie Nano", volunteers[1]),
            ("LOCAL-RC103-0001", "RadiaCode 103", volunteers[2]),
            ("LOCAL-RC103-0002", "RadiaCode 103", volunteers[3]),
            ("LOCAL-SQM-0001", "SQM-LE", volunteers[0]),
            ("LOCAL-M5-ZGZ-01", "GammaStation M5", rosa),
            ("LOCAL-M5-MAD-01", "GammaStation M5", rosa),
        ]
        devices = {}
        for serial, model_name, owner in devices_spec:
            obj, _ = Device.objects.update_or_create(
                serial_number=serial,
                defaults={
                    "device_model": device_models[model_name],
                    "owner": owner,
                    "is_active": True,
                    "calibration_date": (now - timedelta(days=rng.randint(30, 300))).date(),
                    "purchase_date": (now - timedelta(days=rng.randint(400, 900))).date(),
                },
            )
            devices[serial] = obj

        # The two station devices post through the headless ingest endpoint, which
        # authenticates with a per-device token. Issue one for each so that path can
        # be exercised locally; the plaintext is printed once, as in production.
        station_tokens = {}
        for serial in ("LOCAL-M5-ZGZ-01", "LOCAL-M5-MAD-01"):
            station_tokens[serial] = devices[serial].issue_ingest_token()

        self.stdout.write(
            f"  devices: {len(devices)} instances of {len(device_models)} models"
        )

        # --- projects, missions, campaigns -----------------------------------
        radiation_project, _ = Project.objects.update_or_create(
            name="Radiacion gamma de fondo en Espana",
            defaults={
                "description": (
                    "Invented local-development project. Its measurements are real "
                    "Safecast records (CC0) taken in Spain."
                ),
                "project_type": "radiation",
                "is_active": True,
                "is_public": True,
                "created_by": rosa,
            },
        )
        light_project, _ = Project.objects.update_or_create(
            name="Contaminacion luminica del cielo nocturno",
            defaults={
                "description": (
                    "Invented local-development project. Its measurements are real "
                    "Globe at Night observations (CC BY 4.0) taken in Spain."
                ),
                "project_type": "light_pollution",
                "is_active": True,
                "is_public": True,
                "created_by": rosa,
            },
        )
        # A third project with nothing in it, so the empty states are reachable.
        empty_project, _ = Project.objects.update_or_create(
            name="Radiacion en balsas de riego (sin datos)",
            defaults={
                "description": "Invented, deliberately empty: this is the empty state.",
                "project_type": "radiation",
                "is_active": True,
                "is_public": False,
                "created_by": rosa,
            },
        )

        missions, campaigns = {}, {}
        mission_spec = [
            ("Rutas urbanas", radiation_project, 180),
            ("Rutas rurales y de montana", radiation_project, 120),
            ("Cielo nocturno de invierno", light_project, 200),
        ]
        for name, project, days in mission_spec:
            obj, _ = Mission.objects.update_or_create(
                name=name,
                project=project,
                defaults={
                    "description": f"Invented mission for local development ({name}).",
                    "start_date": (now - timedelta(days=days)).date(),
                    "end_date": (now + timedelta(days=90)).date(),
                    "created_by": rosa,
                },
            )
            missions[name] = obj

        campaign_spec = [
            ("Campana abierta urbana", "Rutas urbanas", None),
            # One password-protected campaign, so the protected upload path is
            # reachable locally. The password is a development constant and is
            # printed below. NOTE: OpenRed stores this column in plain text.
            ("Campana protegida urbana", "Rutas urbanas", "campana-local-2026"),
            ("Campana de montana", "Rutas rurales y de montana", None),
            ("Campana de invierno", "Cielo nocturno de invierno", None),
        ]
        for name, mission_name, campaign_password in campaign_spec:
            obj, _ = Campaign.objects.update_or_create(
                name=name,
                mission=missions[mission_name],
                defaults={
                    "description": f"Invented campaign for local development ({name}).",
                    "start_date": missions[mission_name].start_date,
                    "end_date": missions[mission_name].end_date,
                    "created_by": rosa,
                    "password": campaign_password,
                },
            )
            obj.participants.set(volunteers)
            campaigns[name] = obj

        self.stdout.write(
            f"  structure: 3 projects, {len(missions)} missions, {len(campaigns)} campaigns"
        )

        # --- stations --------------------------------------------------------
        # Two fixed stations, placed on real measured coordinates from the
        # snapshot's Zaragoza and Madrid regions.
        def first_in_region(region):
            for record in radiation_records:
                if record.get("region") == region:
                    return record
            return radiation_records[0]

        station_spec = [
            ("Estacion Zaragoza (Ebro)", "LOCAL-M5-ZGZ-01", "zaragoza-ebro", 600, "online"),
            ("Estacion Madrid (centro)", "LOCAL-M5-MAD-01", "madrid", 600, "offline"),
        ]
        stations = {}
        for name, serial, region, interval, status in station_spec:
            anchor = first_in_region(region)
            obj, _ = Station.objects.update_or_create(
                device=devices[serial],
                defaults={
                    "user": rosa,
                    "project": radiation_project,
                    "name": name,
                    "latitude": quantize_coord(anchor["latitude"]),
                    "longitude": quantize_coord(anchor["longitude"]),
                    "altitude": 200.0,
                    "expected_interval_seconds": interval,
                    "status": status,
                    "is_active": True,
                },
            )
            stations[name] = obj

        # --- radiation: tracks of real moving records ------------------------
        # Cut the snapshot into tracks the way the readings themselves fall.
        #
        # Sorted by their real capture time, consecutive snapshot records sit a
        # median of about 1 km apart: the fetch script kept one reading per ~1 km
        # cell, so what survives of each volunteer's drive is that drive's shape,
        # in order. Splitting wherever the step exceeds TRACK_GAP_M or
        # TRACK_GAP_SECONDS therefore recovers real routes, and a track's geometry,
        # distance and speed are all real rather than arranged here.
        #
        # Grouping by region instead -- the obvious first idea -- produced single
        # "tracks" 1,900 km long, because one region holds drives from different
        # years, and every distance and speed derived from them was nonsense.
        #
        # What is still invented: which account and which device a track belongs
        # to, and which mission and campaign it was filed under. The snapshot
        # carries no contributor or device id, so no real person is behind any of
        # these routes and none of them can be traced back to one.
        TRACK_GAP_M = 30_000
        TRACK_GAP_SECONDS = 6 * 3600
        MIN_TRACK_POINTS = 5
        # Dropping Safecast's device_id (it identifies one instrument and so its
        # owner) means two people driving different roads in the same area at the
        # same time arrive here interleaved, and a distance-and-time split alone
        # merges them into one zig-zagging "track" whose average speed came out at
        # 231 m/s -- 830 km/h. So also split whenever the step between two
        # consecutive points implies a speed no vehicle reaches: that separates
        # the two drives again without needing to know who made either.
        MAX_PLAUSIBLE_SPEED_MS = 45.0  # 162 km/h

        wanted = min(options["measurements"], len(radiation_records))
        in_time_order = sorted(radiation_records, key=lambda r: r["captured_at"])

        runs, current = [], []
        for record in in_time_order:
            if current:
                previous = current[-1]
                metres = haversine_m(
                    previous["latitude"], previous["longitude"],
                    record["latitude"], record["longitude"],
                )
                seconds = (
                    parse_safecast_time(record["captured_at"])
                    - parse_safecast_time(previous["captured_at"])
                ).total_seconds()
                implied_speed = metres / seconds if seconds > 0 else float("inf")
                if (
                    metres > TRACK_GAP_M
                    or seconds > TRACK_GAP_SECONDS
                    or implied_speed > MAX_PLAUSIBLE_SPEED_MS
                ):
                    runs.append(current)
                    current = []
            current.append(record)
        if current:
            runs.append(current)

        # A run long enough to be a route becomes a Track. Everything else is a
        # loose point, and is planted as one: `capture_mode='movement'` with no
        # track is what the model itself calls "punto suelto a agrupar en track".
        #
        # Keeping them matters for more than completeness. The snapshot's whole
        # upper value range -- 88 readings above 200 CPM, up to 1026 CPM, three
        # times background -- sits in these fragments, because they come from
        # fixed loggers that report from one spot rather than from a drive. Drop
        # them and every dose rate in the database falls in a 0.05-0.18 uSv/h
        # band, which leaves the map's colour scale and the histogram with
        # nothing to show.
        route_runs = [run for run in runs if len(run) >= MIN_TRACK_POINTS]
        loose_records = [r for run in runs if len(run) < MIN_TRACK_POINTS for r in run]

        # Longest routes first, so a small --measurements budget buys whole
        # routes rather than fragments of many.
        route_runs.sort(key=len, reverse=True)

        chosen_runs, budget = [], wanted
        for run in route_runs:
            if budget < MIN_TRACK_POINTS:
                break
            chosen_runs.append(run[:budget])
            budget -= len(chosen_runs[-1])
        # Whatever the budget has left goes to loose points, spread evenly through
        # them so all regions and the whole value range are represented.
        if budget > 0 and loose_records:
            stride = max(1, len(loose_records) // budget)
            loose_records = loose_records[::stride][:budget]
        else:
            loose_records = []

        track_owner_cycle = [
            (volunteers[0], "LOCAL-BGN-0001"),
            (volunteers[1], "LOCAL-BGN-0002"),
            (volunteers[2], "LOCAL-RC103-0001"),
            (volunteers[3], "LOCAL-RC103-0002"),
        ]

        made_tracks, made_radiation = [], 0
        for index, records in enumerate(chosen_runs):
            region = records[0].get("region", "unknown")
            if len(records) < 2:
                continue
            owner, serial = track_owner_cycle[index % len(track_owner_cycle)]
            mission = missions["Rutas urbanas"] if index % 2 == 0 else missions["Rutas rurales y de montana"]
            campaign = (
                campaigns["Campana abierta urbana"] if index % 2 == 0
                else campaigns["Campana de montana"]
            )
            records = sorted(records, key=lambda r: r["captured_at"])

            # Track.name is a read-only property derived from the uploaded
            # filename, so the human-readable label goes in `description`.
            track = Track.objects.create(
                project=radiation_project,
                mission=mission,
                campaign=campaign,
                device=devices[serial],
                created_by=owner,
                track_type="radiation",
                file_type="csv",
                # No file: the real flow uploads an RCTRK and a worker parses it.
                # These rows are planted straight into the database, so there is
                # nothing to attach and status is already its end state.
                status="completed",
                description=(
                    f"Ruta {region} {records[0]['captured_at'][:10]} (local) - "
                    f"{len(records)} real Safecast (CC0) readings, in the order and "
                    f"at the times they were taken. The account, device, mission "
                    f"and campaign are invented."
                ),
                synced=True,
                required_gps_accuracy_meters=25.0,
            )

            dose_rates, previous, distance_total = [], None, 0.0
            first_time = last_time = None
            for order, record in enumerate(records):
                captured = parse_safecast_time(record["captured_at"]) + radiation_offset
                cpm = float(record["value"])
                dose_rate = cpm / CPM_PER_USV_H
                dose_rates.append(dose_rate)

                speed = None
                if previous is not None:
                    metres = haversine_m(
                        previous[0], previous[1], record["latitude"], record["longitude"]
                    )
                    seconds = max(1.0, (captured - previous[2]).total_seconds())
                    distance_total += metres
                    speed = round(metres / seconds, 3)

                measurement = RadiationMeasurement(
                    device=devices[serial],
                    user=owner,
                    project=radiation_project,
                    campaign=campaign,
                    track=track,
                    dateTime=captured,
                    timestamp=int(captured.timestamp()),
                    latitude=quantize_coord(record["latitude"]),
                    longitude=quantize_coord(record["longitude"]),
                    altitude=record.get("height"),
                    accuracy=round(rng.uniform(3.0, 18.0), 1),
                    data_quality=rng.choice(["excellent", "good", "good", "fair"]),
                    notes="",
                    radiation_unit="μSv/h",
                    cpm=int(cpm),
                    cpm_rel_error=round(1.0 / math.sqrt(max(cpm, 1.0)), 4),
                    dose_rate=round(dose_rate, 5),
                    dose_rate_rel_error=round(1.0 / math.sqrt(max(cpm, 1.0)), 4),
                    speed=speed,
                    detector_type="gamma",
                    calibration_factor=CPM_PER_USV_H,
                    background_subtracted=False,
                    capture_mode="movement",
                    received_at=captured,
                    raw_data={
                        "source": "Safecast",
                        "licence": "CC0 1.0",
                        "source_ref": record["ref"],
                        "source_unit": record["unit"],
                        "source_value": record["value"],
                        "note": (
                            "Real public reading. Converted to dose rate with "
                            f"{CPM_PER_USV_H} CPM per uSv/h (LND 7317). The device, "
                            "owner, project and track are invented."
                        ),
                    },
                )
                # .save() per row, not bulk_create: BaseMeasurement.save() is what
                # fills the PostGIS `location` point from lat/lng, and bulk_create
                # does not call it.
                measurement.save()
                made_radiation += 1

                previous = (record["latitude"], record["longitude"], captured)
                first_time = first_time or captured
                last_time = captured

            mean = sum(dose_rates) / len(dose_rates)
            variance = sum((d - mean) ** 2 for d in dose_rates) / len(dose_rates)
            span = max(1.0, (last_time - first_time).total_seconds())
            Track.objects.filter(pk=track.pk).update(
                start_time=first_time,
                end_time=last_time,
                total_measurements=len(records),
                total_distance=round(distance_total, 2),
                average_speed=round(distance_total / span, 3),
                min_dose_rate=round(min(dose_rates), 5),
                max_dose_rate=round(max(dose_rates), 5),
                avg_dose_rate=round(mean, 5),
                std_dose_rate=round(math.sqrt(variance), 5),
                synced_at=last_time,
            )
            made_tracks.append(track)

        # --- radiation: loose points, no track -------------------------------
        loose_made = 0
        for index, record in enumerate(loose_records):
            captured = parse_safecast_time(record["captured_at"]) + radiation_offset
            owner, serial = track_owner_cycle[index % len(track_owner_cycle)]
            cpm = float(record["value"])
            dose_rate = cpm / CPM_PER_USV_H
            RadiationMeasurement(
                device=devices[serial],
                user=owner,
                project=radiation_project,
                # No track and no campaign: an ungrouped reading, which is the
                # state the grouping job looks for.
                track=None,
                dateTime=captured,
                timestamp=int(captured.timestamp()),
                latitude=quantize_coord(record["latitude"]),
                longitude=quantize_coord(record["longitude"]),
                altitude=record.get("height"),
                accuracy=round(rng.uniform(3.0, 25.0), 1),
                data_quality=rng.choice(["excellent", "good", "good", "fair"]),
                radiation_unit="μSv/h",
                cpm=int(cpm),
                cpm_rel_error=round(1.0 / math.sqrt(max(cpm, 1.0)), 4),
                dose_rate=round(dose_rate, 5),
                dose_rate_rel_error=round(1.0 / math.sqrt(max(cpm, 1.0)), 4),
                detector_type="gamma",
                calibration_factor=CPM_PER_USV_H,
                capture_mode="movement",
                received_at=captured,
                raw_data={
                    "source": "Safecast",
                    "licence": "CC0 1.0",
                    "source_ref": record["ref"],
                    "source_unit": record["unit"],
                    "source_value": record["value"],
                    "note": (
                        "Real public reading, not part of any route: a single "
                        "point, ungrouped. The device, owner and project are "
                        "invented."
                    ),
                },
            ).save()
            loose_made += 1

        self.stdout.write(
            f"  radiation: {made_radiation} measurements in {len(made_tracks)} tracks, "
            f"plus {loose_made} loose ungrouped points"
        )

        # --- radiation: station time series ----------------------------------
        # Real measured values, replayed onto a recent regular schedule at the
        # station's fixed coordinate. The timing is synthesised on purpose: a
        # station is the one thing in OpenRed that must have *recent* readings,
        # or the watchdog, the "last 24h" chart and the activity figures are all
        # empty whatever the date shift does.
        # Each station replays the readings really taken in *its own* region, so
        # its series sits in the band that place actually measures instead of
        # being clipped to an arbitrary ceiling.
        station_regions = {
            "Estacion Zaragoza (Ebro)": "zaragoza-ebro",
            "Estacion Madrid (centro)": "madrid",
        }
        station_made = 0
        for station_index, (name, station) in enumerate(sorted(stations.items())):
            region = station_regions.get(name)
            series_values = [
                float(r["value"]) for r in radiation_records if r.get("region") == region
            ] or [float(r["value"]) for r in radiation_records]
            readings = 96 if station.status == "online" else 48
            # The offline station stopped reporting three days ago; that is what
            # makes it offline, and gives the watchdog something to find.
            ends_at = now if station.status == "online" else now - timedelta(days=3)
            interval = timedelta(seconds=station.expected_interval_seconds)
            last_at = None
            for step in range(readings):
                stamp = ends_at - interval * (readings - 1 - step)
                cpm = series_values[(station_index * 17 + step) % len(series_values)]
                dose_rate = cpm / CPM_PER_USV_H
                RadiationMeasurement(
                    device=station.device,
                    user=station.user,
                    project=station.project,
                    station=station,
                    dateTime=stamp,
                    timestamp=int(stamp.timestamp()),
                    latitude=station.latitude,
                    longitude=station.longitude,
                    altitude=station.altitude,
                    accuracy=1.0,
                    data_quality="good",
                    radiation_unit="μSv/h",
                    cpm=int(cpm),
                    cpm_rel_error=round(1.0 / math.sqrt(max(cpm, 1.0)), 4),
                    dose_rate=round(dose_rate, 5),
                    speed=0.0,
                    detector_type="gamma",
                    calibration_factor=CPM_PER_USV_H,
                    capture_mode="static",
                    received_at=stamp,
                    raw_data={
                        "source": "Safecast",
                        "licence": "CC0 1.0",
                        "note": (
                            "Real measured value, replayed onto a synthesised recent "
                            "schedule at this invented station's coordinate."
                        ),
                    },
                ).save()
                station_made += 1
                last_at = stamp
            Station.objects.filter(pk=station.pk).update(last_measurement_at=last_at)

        self.stdout.write(
            f"  stations: {len(stations)}, with {station_made} static readings between them"
        )

        # --- spectra ---------------------------------------------------------
        spectra_made = 0
        if not options["no_spectra"]:
            for track in made_tracks:
                points = list(
                    RadiationMeasurement.objects.filter(track=track).order_by("dateTime")
                )
                if len(points) < 4:
                    continue
                # Two spectra per track, each integrated over half of it.
                half = len(points) // 2
                segments = [points[:half], points[half:]]
                for index, segment in enumerate(segments):
                    if not segment:
                        continue
                    started, ended = segment[0].dateTime, segment[-1].dateTime
                    duration = max(60, int((ended - started).total_seconds()))
                    mean_cpm = sum(p.cpm or 0 for p in segment) / len(segment)
                    total_counts = max(500, int(mean_cpm * duration / 60.0))
                    a0, a1, a2, counts = synthetic_spectrum(1024, total_counts, rng)
                    Spectrum.objects.create(
                        track=track,
                        name=f"{track.name} - espectro {index + 1}",
                        index=index,
                        started_at=started,
                        ended_at=ended,
                        duration_sec=duration,
                        a0=a0,
                        a1=a1,
                        a2=a2,
                        channel_count=1024,
                        counts=counts,
                        start_lat=float(segment[0].latitude),
                        start_lon=float(segment[0].longitude),
                        start_alt=segment[0].altitude,
                        end_lat=float(segment[-1].latitude),
                        end_lon=float(segment[-1].longitude),
                    )
                    spectra_made += 1
            self.stdout.write(f"  spectra: {spectra_made} synthesised (1024 channels each)")

        # --- light pollution --------------------------------------------------
        light_made = 0
        light_campaign = campaigns["Campana de invierno"]
        sqm_device = devices["LOCAL-SQM-0001"]
        for index, record in enumerate(light_records):
            stamp = datetime.strptime(
                f"{record['UTDate']} {record.get('UTTime', '00:00')}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=dt_timezone.utc) + light_offset

            sqm = record.get("SQMReading")
            if sqm:
                brightness = float(sqm)
                brightness_source = "SQM meter reading (mag/arcsec^2), as published"
            else:
                brightness = sky_brightness_from_nelm(float(record["LimitingMag"]))
                brightness_source = (
                    "derived from the published naked-eye limiting magnitude via "
                    "Schaefer's relation; approximate"
                )
            lux = lux_from_sky_brightness(brightness)

            owner = volunteers[index % len(volunteers)]
            measurement = LightPollutionMeasurement(
                device=sqm_device,
                user=owner,
                project=light_project,
                campaign=light_campaign,
                dateTime=stamp,
                timestamp=int(stamp.timestamp()),
                latitude=quantize_coord(record["Latitude"]),
                longitude=quantize_coord(record["Longitude"]),
                altitude=float(record["Elevation(m)"]) if record.get("Elevation(m)") else None,
                accuracy=round(rng.uniform(5.0, 30.0), 1),
                data_quality="good" if record.get("CloudCover") == "clear" else "fair",
                lux=round(lux, 6),
                # CCT and the CIE coordinates are instrument channel data that
                # Globe at Night does not publish. Left null rather than invented,
                # so nothing here looks measured when it is not.
                cct=None,
                legacy_data={
                    "source": "Globe at Night",
                    "licence": "CC BY 4.0",
                    "attribution": "Globe at Night, https://globeatnight.org",
                    "source_ref": record["ref"],
                    "sky_brightness_mag_arcsec2": round(brightness, 3),
                    "sky_brightness_source": brightness_source,
                    "limiting_magnitude": record.get("LimitingMag"),
                    "cloud_cover": record.get("CloudCover"),
                    "constellation": record.get("Constellation"),
                },
            )
            measurement.save()
            light_made += 1

        self.stdout.write(f"  light pollution: {light_made} measurements")

        # --- weather ----------------------------------------------------------
        # A handful of synthesised cache rows attached to real measurement hours,
        # so the weather panel has something to show without the weather worker
        # calling an external API. The rest stay unfetched, which is what the
        # `fetch_pending_weather` job exists to pick up.
        import h3

        weather_made = 0
        for measurement in RadiationMeasurement.objects.order_by("?")[:20]:
            hour = measurement.dateTime.replace(minute=0, second=0, microsecond=0)
            # The table is unique on (h3_cell, timestamp_hour) and the production
            # code buckets by H3 resolution 8 (~5.5 km), so the cell has to be
            # computed the same way here or two nearby points collide on an empty
            # h3_cell and the insert fails.
            cell = h3.latlng_to_cell(
                float(measurement.latitude), float(measurement.longitude), 8
            )
            cache, created = WeatherCache.objects.get_or_create(
                h3_cell=cell,
                timestamp_hour=hour,
                defaults={
                    "latitude": Decimal(str(round(float(measurement.latitude), 6))),
                    "longitude": Decimal(str(round(float(measurement.longitude), 6))),
                    "temperature": round(rng.uniform(2.0, 31.0), 1),
                    "humidity": round(rng.uniform(25.0, 95.0), 1),
                    "pressure": round(rng.uniform(985.0, 1030.0), 1),
                    "wind_speed": round(rng.uniform(0.0, 38.0), 1),
                    "wind_direction": round(rng.uniform(0.0, 359.0), 1),
                    "cloud_cover": round(rng.uniform(0.0, 100.0), 1),
                    "rain_sum": round(rng.choice([0.0, 0.0, 0.0, 0.4, 2.1]), 1),
                    "fetched": True,
                    "weather_data": {
                        "note": "Synthesised for local development. Not a real forecast."
                    },
                },
            )
            if created:
                weather_made += 1
            RadiationMeasurement.objects.filter(pk=measurement.pk).update(weather_cache=cache)
        self.stdout.write(f"  weather: {weather_made} synthesised cache rows")

        # --- created_at ------------------------------------------------------
        # `created_at` is auto_now_add on every one of these models, so each row was
        # stamped with the moment this command ran. Left alone, every screen that
        # orders or groups by created_at -- the activity figures, the monthly report,
        # "recent contributions" -- would show the whole history as having happened
        # in the same second. Set it to the measurement's own time instead.
        updated = 0
        for model, field in (
            (RadiationMeasurement, "dateTime"),
            (LightPollutionMeasurement, "dateTime"),
            (Spectrum, "started_at"),
        ):
            with transaction.atomic():
                table = model._meta.db_table
                from django.db import connection

                with connection.cursor() as cursor:
                    cursor.execute(
                        f'UPDATE "{table}" SET created_at = "{field}" '
                        f'WHERE created_at <> "{field}"'
                    )
                    updated += cursor.rowcount
        # Tracks have no single measurement time; use the track's own start.
        with transaction.atomic():
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE "{Track._meta.db_table}" SET created_at = start_time '
                    f'WHERE start_time IS NOT NULL'
                )
                updated += cursor.rowcount
        self.stdout.write(f"  created_at: {updated} rows back-dated to their own timestamps")

        # --- summary ----------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("seeded. Nothing was copied from production."))
        self.stdout.write("")
        self.stdout.write(f"  API      http://localhost:8020/api/docs/")
        self.stdout.write(f"  admin    http://localhost:8020/admin/")
        self.stdout.write(f"  RQ       http://localhost:8020/django-rq/")
        self.stdout.write("")
        self.stdout.write(f"  accounts, all with password {password!r}:")
        self.stdout.write(f"    {admin.email:28s} superuser, for /admin/")
        self.stdout.write(f"    {rosa.email:28s} staff; owns the projects and the stations")
        for volunteer in volunteers:
            self.stdout.write(f"    {volunteer.email:28s} volunteer with measurements")
        self.stdout.write("")
        self.stdout.write(f"  projects: {radiation_project.id} radiation, "
                          f"{light_project.id} light pollution, {empty_project.id} empty")
        self.stdout.write(f"  protected campaign password: 'campana-local-2026'")
        self.stdout.write("")
        self.stdout.write("  device ingest tokens (shown once, as in production):")
        for serial, token in station_tokens.items():
            self.stdout.write(f"    {serial}: {token}")
