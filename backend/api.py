from __future__ import annotations
import hashlib
import hmac
import io
import ipaddress
import os
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from psycopg.types.json import Jsonb
from .db import connection, migrate
from .scoring import POLICY, bands, score

register_heif_opener()
TZ = ZoneInfo("Europe/Istanbul")
STORE = Path(os.environ.get("PRIVATE_STORE", "/private"))
MAX_UPLOAD = 20 * 1024 * 1024
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def fail(code: str, status: int = 400):
    raise HTTPException(status, detail={"code": code})


def iso(value):
    return value.isoformat() if value else None


def session(request: Request, csrf: str | None = None):
    token = request.cookies.get("swimmable_session", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connection() as db:
        row = db.execute("SELECT csrf_token FROM sessions WHERE token_hash=%s AND expires_at > now()", (digest,)).fetchone()
    if not row:
        fail("unauthorized", 401)
    if csrf is not None and not hmac.compare_digest(row["csrf_token"], csrf):
        fail("csrf_invalid", 403)
    return row


def capture_time(image: Image.Image):
    try:
        exif = image.getexif()
        photo_exif = exif.get_ifd(34665) if 34665 in exif else exif
        raw = photo_exif.get(36867)
    except (OSError, TypeError, ValueError):
        raw = None
    if not raw:
        return datetime.now(TZ).astimezone(timezone.utc), False, True
    try:
        value = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
        sub = photo_exif.get(37521)
        if sub:
            value = value.replace(microsecond=int((str(sub) + "000000")[:6]))
        offset = photo_exif.get(36881)
        if offset:
            sign = 1 if str(offset)[0] == "+" else -1
            hours, minutes = map(int, str(offset)[1:].split(":"))
            if hours > 14 or minutes > 59:
                raise ValueError("invalid offset")
            value = value.replace(tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
            assumed = False
        else:
            value = value.replace(tzinfo=TZ)
            assumed = True
        return value.astimezone(timezone.utc), assumed, False
    except (ValueError, OverflowError):
        return datetime.now(TZ).astimezone(timezone.utc), False, True


@app.on_event("startup")
def startup():
    STORE.joinpath("originals").mkdir(parents=True, exist_ok=True)
    migrate()


@app.get("/api/health")
def health():
    with connection() as db:
        db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/today")
def today():
    now = datetime.now(timezone.utc)
    today_local = now.astimezone(TZ).date()
    with connection() as db:
        obs = {kind: db.execute("SELECT * FROM source_observations WHERE source=%s ORDER BY measured_at DESC, id DESC LIMIT 1", (kind,)).fetchone() for kind in ("air", "sea")}
        prediction = db.execute("""SELECT p.*, ph.captured_at FROM predictions p JOIN photos ph ON ph.id=p.photo_id ORDER BY ph.captured_at DESC, p.id DESC LIMIT 1""").fetchone()
        result = None
        if prediction and all(obs.values()):
            result = score(prediction["waviness"], obs["sea"]["temperature_c"], obs["air"]["temperature_c"])
            db.execute("""INSERT INTO score_revisions(policy_version, air_observation_id, sea_observation_id, prediction_id, score, policy_snapshot)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (POLICY.version, obs["air"]["id"], obs["sea"]["id"], prediction["id"], result, Jsonb(asdict(POLICY))))
    def serialize_obs(row):
        return None if not row else {"value": row["temperature_c"], "measured_at": iso(row["measured_at"]), "retrieved_at": iso(row["retrieved_at"]), "station": row["station"], "stale": now - row["measured_at"] > timedelta(hours=24)}
    wave = None if not prediction else {"value": prediction["waviness"], "captured_at": iso(prediction["captured_at"]), "stale": now - prediction["captured_at"] > timedelta(hours=24), "previous_day": prediction["captured_at"].astimezone(TZ).date() != today_local, "preview_url": f"/api/wave-preview/{prediction['id']}" if Path(prediction["input_path"]).is_file() else None}
    return {"today": today_local.isoformat(), "fetched_at": iso(now), "timezone": "Europe/Istanbul", "air": serialize_obs(obs["air"]), "sea": serialize_obs(obs["sea"]), "wave": wave, "bands": bands(prediction["waviness"] if prediction else None, obs["sea"]["temperature_c"] if obs["sea"] else None, obs["air"]["temperature_c"] if obs["air"] else None), "score": result, "score_old_inputs": bool(result is not None and (wave["stale"] or wave["previous_day"] or any(now - row["measured_at"] > timedelta(hours=24) for row in obs.values()))), "policy_version": POLICY.version}


@app.get("/api/wave-preview/{prediction_id}")
def wave_preview(prediction_id: int):
    with connection() as db:
        row = db.execute("""SELECT pr.input_path FROM predictions pr JOIN photos ph ON ph.id=pr.photo_id
            WHERE pr.id=%s AND pr.id=(SELECT latest.id FROM predictions latest JOIN photos latest_photo ON latest_photo.id=latest.photo_id
              ORDER BY latest_photo.captured_at DESC, latest.id DESC LIMIT 1)""", (prediction_id,)).fetchone()
    if not row:
        fail("preview_not_found", 404)
    model_inputs = (STORE / "model-inputs").resolve()
    path = Path(row["input_path"]).resolve()
    if not path.is_relative_to(model_inputs) or path.suffix.lower() != ".jpg" or not path.is_file():
        fail("preview_not_found", 404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.post("/api/admin/login")
async def login(request: Request, response: Response):
    body = await request.json()
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    raw_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        ip = str(ipaddress.ip_address(raw_ip))
    except ValueError:
        ip = request.client.host if request.client else "unknown"
    secret = os.environ["SESSION_SECRET"].encode()
    ip_hash = hmac.new(secret, ip.encode(), hashlib.sha256).hexdigest()
    with connection() as db:
        db.execute("DELETE FROM login_attempts WHERE attempted_at < now() - interval '1 day'")
        count = db.execute("SELECT count(*) AS n FROM login_attempts WHERE ip_hash=%s AND success=false AND attempted_at > now() - interval '15 minutes'", (ip_hash,)).fetchone()["n"]
        if count >= 5:
            fail("login_throttled", 429)
        expected = os.environ["ADMIN_PASSWORD"]
        # A PBKDF2 digest is derived before constant-time comparison; no plaintext comparison.
        salt = hashlib.sha256(secret + b"admin-password").digest()
        supplied_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
        expected_hash = hashlib.pbkdf2_hmac("sha256", expected.encode(), salt, 310000)
        valid = hmac.compare_digest(username, os.environ["ADMIN_USERNAME"]) and hmac.compare_digest(supplied_hash, expected_hash)
        db.execute("INSERT INTO login_attempts(ip_hash,success) VALUES (%s,%s)", (ip_hash, valid))
    if not valid:
        fail("invalid_credentials", 401)
    with connection() as db:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES (%s,%s,now() + interval '7 days')", (hashlib.sha256(token.encode()).hexdigest(), csrf))
    response.set_cookie("swimmable_session", token, httponly=True, secure=True, samesite="strict", max_age=604800, path="/api/admin")
    return {"csrf": csrf}


@app.get("/api/admin/session")
def check_session(request: Request):
    return {"csrf": session(request)["csrf_token"]}


@app.post("/api/admin/logout")
def logout(request: Request, response: Response, x_csrf_token: str = Header(default="")):
    session(request, x_csrf_token)
    digest = hashlib.sha256(request.cookies["swimmable_session"].encode()).hexdigest()
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=%s", (digest,))
    response.delete_cookie("swimmable_session", path="/api/admin")
    return {"status": "ok"}


@app.post("/api/admin/photos", status_code=202)
async def upload(request: Request, x_csrf_token: str = Header(default="")):
    session(request, x_csrf_token)
    if int(request.headers.get("content-length", "0")) > MAX_UPLOAD + 1_000_000:
        fail("image_too_large")
    form = await request.form(max_files=1, max_fields=0, max_part_size=MAX_UPLOAD + 1)
    image = form.get("image")
    if not isinstance(image, UploadFile):
        fail("image_invalid")
    data = await image.read(MAX_UPLOAD + 1)
    await form.close()
    if len(data) > MAX_UPLOAD:
        fail("image_too_large")
    try:
        with Image.open(io.BytesIO(data)) as decoded:
            if decoded.format not in ("HEIF", "JPEG"):
                fail("image_format_unsupported")
            captured, assumed, capture_assumed = capture_time(decoded)
            width, height, image_format = decoded.width, decoded.height, decoded.format
            if min(width, height) < 224 or width * height > 50_000_000:
                fail("image_dimensions_invalid")
            ImageOps.exif_transpose(decoded).convert("RGB").load()
    except (UnidentifiedImageError, OSError, ValueError):
        fail("image_invalid")
    digest = hashlib.sha256(data).hexdigest()
    photo_id, job_id = uuid.uuid4(), uuid.uuid4()
    extension = ".heic" if image_format == "HEIF" else ".jpg"
    path = STORE / "originals" / f"{photo_id}{extension}"
    with connection() as db:
        existing = db.execute("SELECT j.id,j.state,p.captured_at FROM photos p JOIN prediction_jobs j ON j.photo_id=p.id WHERE p.sha256=%s", (digest,)).fetchone()
        if existing:
            return {"job_id": str(existing["id"]), "state": existing["state"], "duplicate": True, "captured_at": iso(existing["captured_at"])}
        path.write_bytes(data)
        try:
            inserted = db.execute("""INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,capture_time_assumed,width,height,format)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(sha256) DO NOTHING RETURNING id""", (photo_id, digest, str(path), Path(image.filename or "photo").name[:255], captured, assumed, capture_assumed, width, height, image_format)).fetchone()
            if not inserted:
                path.unlink(missing_ok=True)
                existing = db.execute("SELECT j.id,j.state,p.captured_at FROM photos p JOIN prediction_jobs j ON j.photo_id=p.id WHERE p.sha256=%s", (digest,)).fetchone()
                if not existing:
                    fail("upload_conflict", 409)
                return {"job_id": str(existing["id"]), "state": existing["state"], "duplicate": True, "captured_at": iso(existing["captured_at"])}
            db.execute("INSERT INTO prediction_jobs(id,photo_id) VALUES (%s,%s)", (job_id, photo_id))
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return {"job_id": str(job_id), "state": "queued", "duplicate": False, "captured_at": iso(captured), "offset_assumed": assumed, "capture_time_assumed": capture_assumed}


@app.get("/api/admin/jobs/{job_id}")
def job(request: Request, job_id: uuid.UUID):
    session(request)
    with connection() as db:
        row = db.execute("SELECT id,state,queued_at,started_at,finished_at,error_code FROM prediction_jobs WHERE id=%s", (job_id,)).fetchone()
    if not row:
        fail("job_not_found", 404)
    return {key: iso(value) if isinstance(value, datetime) else str(value) if isinstance(value, uuid.UUID) else value for key, value in row.items()}
