"""Single worker: scheduled collection plus durable, one-at-a-time inference."""
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from pillow_heif import register_heif_opener
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from dataclasses import asdict
from psycopg.types.json import Jsonb
from .db import connection, migrate
from .inference_service import InferenceService
from .scoring import POLICY, score

register_heif_opener()
TZ = ZoneInfo("Europe/Istanbul")
SLOTS = tuple(x.strip() for x in os.environ.get("COLLECTION_SLOTS", "06:00,12:00,18:00").split(","))
if len(SLOTS) > 3 or sorted(SLOTS) != list(SLOTS) or any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", slot) for slot in SLOTS):
    raise ValueError("COLLECTION_SLOTS must be at most three sorted HH:MM entries")
AIR_URL = "https://api.open-meteo.com/v1/forecast?latitude=41.3378&longitude=36.2691&current=temperature_2m,wind_speed_10m,wind_direction_10m&temperature_unit=celsius&wind_speed_unit=kmh&timezone=Europe%2FIstanbul"
SEA_URL = "https://pirireis.mgm.gov.tr/deniz-suyu-sicakliklari"
STATION = "İLKADIM/SAMSUN LİMAN KUZEY MENDİREK FENERİ"



def fetch_air():
    with urlopen(Request(AIR_URL, headers={"User-Agent": "Swimmable/1.0"}), timeout=20) as response:
        obj = json.load(response)["current"]
    measured = datetime.fromisoformat(obj["time"]).replace(tzinfo=TZ).astimezone(timezone.utc)
    temperature = float(obj["temperature_2m"])
    if not math.isfinite(temperature):
        raise ValueError("invalid_air_temperature")
    return "Atakum 41.3378,36.2691", measured, temperature, {"wind_speed_kmh": obj.get("wind_speed_10m"), "wind_direction_deg": obj.get("wind_direction_10m")}


def fetch_sea():
    with urlopen(Request(SEA_URL, headers={"User-Agent": "Mozilla/5.0 (Swimmable/1.0)"}), timeout=20) as response:
        page = response.read().decode("utf-8", errors="replace")
    for match in re.finditer(r'\{[^{}]*"denizSicaklik"\s*:\s*[^,}]+,[^{}]*\}', page):
        try:
            obj = json.loads(match.group(0))
            if obj.get("istAd") != STATION:
                continue
            measured = datetime.fromisoformat(obj["denizVeriZamani"].replace("Z", "+00:00"))
            if measured.tzinfo is None:
                measured = measured.replace(tzinfo=TZ)
            temperature = float(obj["denizSicaklik"])
            if not math.isfinite(temperature):
                continue
            return STATION, measured.astimezone(timezone.utc), temperature, {}
        except (ValueError, KeyError, TypeError):
            continue
    raise ValueError("station_unavailable")


def collect_due(now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TZ)
    slot = max((s for s in SLOTS if local.strftime("%H:%M") >= s), default=None)
    if slot is None:
        return
    for kind, fetch in (("air", fetch_air), ("sea", fetch_sea)):
        # The INSERT is the claim. Unique(source,day,slot) survives restarts and
        # concurrent workers; an attempted request is counted before it goes out.
        with connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{kind}:{local.date()}",))
            count = db.execute("SELECT count(*) AS n FROM collection_attempts WHERE source=%s AND local_day=%s", (kind, local.date())).fetchone()["n"]
            backoff = db.execute("SELECT retry_after_at FROM collection_attempts WHERE source=%s AND retry_after_at > %s ORDER BY retry_after_at DESC LIMIT 1", (kind, now)).fetchone()
            if count >= 3 or backoff:
                continue
            claimed = db.execute("INSERT INTO collection_attempts(source,local_day,slot) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id", (kind, local.date(), slot)).fetchone()
        if not claimed:
            continue
        try:
            station, measured, temperature, context = fetch()
            with connection() as db:
                db.execute("INSERT INTO source_observations(source,station,measured_at,temperature_c,context) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(source,station,measured_at) DO NOTHING", (kind, station, measured, temperature, json.dumps(context)))
                db.execute("UPDATE collection_attempts SET state='completed' WHERE id=%s", (claimed["id"],))
        except Exception as error:
            # Codes only: upstream payload and secrets never enter logs or API.
            code = "rate_limited" if getattr(error, "code", None) == 429 else "collection_failed"
            retry_after = None
            if getattr(error, "code", None) in (429, 503):
                header = error.headers.get("Retry-After", "")
                try:
                    retry_after = now + timedelta(seconds=max(0, int(header)))
                except ValueError:
                    try:
                        retry_after = parsedate_to_datetime(header).astimezone(timezone.utc)
                    except (ValueError, TypeError):
                        retry_after = now + timedelta(hours=24) if code == "rate_limited" else None
            with connection() as db:
                db.execute("UPDATE collection_attempts SET state='failed',error_code=%s,retry_after_at=%s WHERE id=%s", (code, retry_after, claimed["id"]))


def process_one(inference):
    with connection() as db:
        job = db.execute("""SELECT j.id,j.photo_id,p.private_path FROM prediction_jobs j JOIN photos p ON p.id=j.photo_id
          WHERE j.state='queued' ORDER BY j.queued_at FOR UPDATE OF j SKIP LOCKED LIMIT 1""").fetchone()
        if not job:
            return False
        db.execute("UPDATE prediction_jobs SET state='processing',started_at=now() WHERE id=%s", (job["id"],))
    original = Path(job["private_path"])
    private_store = Path(os.environ.get("PRIVATE_STORE", "/private"))
    model_input = private_store / "model-inputs" / f"{job['photo_id']}.jpg"
    diagnostic_dir = private_store / "diagnostics" / str(job["photo_id"])
    model_input.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_dir.mkdir(parents=True, exist_ok=True)
    try:
        state, value = inference.predict(original, model_input, diagnostic_dir)
        if state != "ok":
            with connection() as db:
                db.execute("UPDATE prediction_jobs SET state='failed',finished_at=now(),error_code=%s WHERE id=%s", (state, job["id"]))
            return True
        with connection() as db:
            prediction = db.execute("INSERT INTO predictions(photo_id,waviness,model_version,preprocessing_version,input_path) VALUES (%s,%s,%s,%s,%s) RETURNING id", (job["photo_id"], value, "v19:a77c370d", "v19-fast-v2-segformer-de01bae2", str(model_input))).fetchone()
            db.execute("UPDATE prediction_jobs SET state='completed',finished_at=now() WHERE id=%s", (job["id"],))
            observations = {kind: db.execute("SELECT id,temperature_c FROM source_observations WHERE source=%s ORDER BY measured_at DESC, id DESC LIMIT 1", (kind,)).fetchone() for kind in ("air", "sea")}
            if all(observations.values()):
                air, sea = observations["air"], observations["sea"]
                db.execute("INSERT INTO score_revisions(policy_version,air_observation_id,sea_observation_id,prediction_id,score,policy_snapshot) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (POLICY.version, air["id"], sea["id"], prediction["id"], score(value, sea["temperature_c"], air["temperature_c"]), Jsonb(asdict(POLICY))))
    except Exception as error:
        print(f"Prediction job failed: {type(error).__name__}: {error}", flush=True)
        with connection() as db:
            db.execute("UPDATE prediction_jobs SET state='failed',finished_at=now(),error_code='inference_failed' WHERE id=%s", (job["id"],))
    return True


def prune_private_files():
    days = int(os.environ.get("PHOTO_RETENTION_DAYS", "0"))
    if days <= 0:
        return
    with connection() as db:
        rows = db.execute("""SELECT p.id,p.private_path,pr.input_path FROM photos p
            JOIN prediction_jobs j ON j.photo_id=p.id
            LEFT JOIN predictions pr ON pr.photo_id=p.id
            WHERE p.uploaded_at < now() - (%s * interval '1 day')
              AND j.state IN ('completed','failed') AND p.files_pruned_at IS NULL""", (days,)).fetchall()
        for row in rows:
            diagnostic_dir = Path(os.environ.get("PRIVATE_STORE", "/private")) / "diagnostics" / str(row["id"])
            normalized = Path(os.environ.get("PRIVATE_STORE", "/private")) / "normalized" / f"{row['id']}.png"
            for path in (row["private_path"], row["input_path"], normalized):
                if path:
                    Path(path).unlink(missing_ok=True)
            if diagnostic_dir.is_dir():
                shutil.rmtree(diagnostic_dir)
            db.execute("UPDATE photos SET files_pruned_at=now() WHERE id=%s", (row["id"],))

def recover_interrupted_jobs():
    with connection() as db:
        db.execute("UPDATE prediction_jobs SET state='queued',started_at=NULL WHERE state='processing'")


def main():
    migrate()
    # A session lock keeps inference single-file across replicas. On restart,
    # reclaim jobs interrupted while processing; the previous lock is gone.
    with connection() as lock_db:
        locked = lock_db.execute("SELECT pg_try_advisory_lock(71820455) AS acquired").fetchone()["acquired"]
        if not locked:
            raise RuntimeError("another worker already holds the inference lock")
        lock_db.commit()
        recover_interrupted_jobs()
        inference = InferenceService(os.environ.get("MODEL_CHECKPOINT", "/model/model.ckpt"), os.environ.get("INFERENCE_DEVICE", "auto"))
        inference.start()
        last_prune = 0.0
        last_collect = 0.0
        while True:
            inference.start()
            if time.monotonic() - last_collect >= 30:
                collect_due()
                last_collect = time.monotonic()
            process_one(inference)
            if time.monotonic() - last_prune > 86400:
                prune_private_files()
                last_prune = time.monotonic()
            time.sleep(1)


if __name__ == "__main__":
    main()
