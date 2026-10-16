# Swimmable

V1 shows today's experimental comfort score for İncesu and Atakum in Samsun. A neural network estimates visual waviness from an administrator photo; Open-Meteo supplies model-based air temperature and MGM Piri Reis supplies nearby sea temperature. The score does not assess currents or swimming safety.

## Run

1. Copy `.env.example` to `.env`, set all credentials, and keep it private.
2. Fetch the pinned checkpoint: `hf download kubilaycaglayan/wave-regression model.ckpt --revision 84edd4eee1c2918260e583e1eaf3b0984132a616 --local-dir .private/model`. Its SHA-256 must be `91938b857aa4d6f44d23f0b289f2a76fefd7ed8de2a8cb350e5c7d0e95599289`. The submodule checkout contains a Git LFS pointer until fetched. The worker downloads SegFormer at pinned revision `de01bae28967510f9ddd496c60a969357195400c` into its persistent cache on first use.
3. Run `docker compose up -d --build db api worker frontend proxy`. The local proxy binds only `127.0.0.1:43255`. In Cloudflare Tunnel, set the `swimmable.blaqc.space` public hostname's service URL to `http://127.0.0.1:43255`. Port 43155 belongs to `draw-app` and must remain untouched. After verifying this route, start the connector with `docker compose --profile deployment up -d tunnel`. The tunnel uses host networking so its `127.0.0.1` reaches the host-local proxy.
4. Run `scripts/backup.sh .private/backups/<date>` regularly. `scripts/verify-restore.sh BACKUP_DIRECTORY` restores the database to a temporary database and extracts the photo archive to a temporary directory. Keep encrypted off-host copies of both artifacts.

Database and worker ports are private. Uploaded originals and processed model inputs live in the private `photos` volume; the database and model cache have separate persistent volumes. The API never serves photo bytes. `PHOTO_RETENTION_DAYS=0` retains originals and model inputs indefinitely; setting a positive number prunes only private files after completed or failed jobs, while retaining photo metadata and predictions. `/api/today` is public; all `/api/admin/*` routes require the configured administrator session except login. Sessions are Secure, HTTP-only, SameSite Strict cookies, and mutations require CSRF tokens.

## Collection

The worker attempts Open-Meteo and the explicit MGM station `İLKADIM/SAMSUN LİMAN KUZEY MENDİREK FENERİ` at the configured 06:00, 12:00, and 18:00 Türkiye-time slots. Its database claim is recorded before each request. Each source gets at most three attempts per local day, including failures. It does not retry or backfill missed slots. Browser visits and uploads do not call either provider. An unchanged measurement retains its original timestamp. The API reports missing and stale inputs independently.

## Score policy v1

`backend/scoring.py` holds the policy version, weights, band thresholds, cap, and piecewise linear curves. Wave comfort is 1 at 0, .85 at .3, .2 at .6, and 0 at 1. Sea comfort is 0 at 0°C, .1 at 16°C, .5 at 20°C, .9 at 24°C, and 1 at 28°C. Air comfort is 0 at −20°C, .2 at 20°C, 1 from 29–32°C, .6 at 40°C, and 0 at 50°C. Values interpolate linearly and clamp at the ends. Base weights are 60% wave, 30% sea, 10% air. Wave share falls by up to half as waviness rises; 75% of the released share goes to sea and 25% to air. Waviness ≥ .6 caps the final score at 20. These curves are experimental product choices, not validated safety thresholds. Increment the policy version when changing any constant or curve. Score revisions store immutable source IDs and the policy version.

## Verification

Run `python3 -m unittest discover -s tests`. The supplied HEIC has original capture time `2026-10-16 12:43:47.069 +03:00`. Physical iPhone Safari and standalone checks remain necessary before claiming full iOS validation.

The supplied HEIC completed full CPU inference in about 174 seconds on this host, with a sampled worker peak near 9.1 GiB RAM. Its saved 224 × 224 JPEG reproduced waviness 0.43999361991882324 when rerun with `--preprocessed`. This is a deployment sizing observation, not a speed guarantee.
