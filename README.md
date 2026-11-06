# Swimmable

V1 shows today's experimental comfort score for İncesu and Atakum in Samsun. A neural network estimates visual waviness from an administrator photo; Open-Meteo supplies model-based air temperature and MGM Piri Reis supplies nearby sea temperature. The score does not assess currents or swimming safety.

## Run

1. Copy `.env.example` to `.env`, set all credentials, and keep it private.
2. Fetch both fast-v2.2 ONNX artifacts at the pinned release: `hf download kubilaycaglayan/wave-regression regressor-fast-v2.2.onnx segformer-b0-step1-448.onnx --revision 092b6a4f1b96208313fef27cf8cda8b9b3715f26 --local-dir .private/model-fast-v2.2`. Verify the regressor SHA-256 is `a1a8f65500d88debe2980c900ccc55b2b8b6d46f6492d3b3ddf2a6b7ad3eb8e4` and the segmenter SHA-256 is `96410800203e3f19b7dab26980ea6413f6f9369bb868e23bfa7f14eca6681fda`. The submodule checkout contains Git LFS pointers; these local files are mounted read-only into the worker.
3. Run `docker compose up -d --build db api worker frontend proxy`. The local proxy binds only `127.0.0.1:43255`. It limits all routes to 10 requests per second per client, with a 30-request burst; admin login is limited to 5 per minute with a 2-request burst. The API also throttles failed logins at five per 15 minutes. In Cloudflare Tunnel, set the `swimmable.blaqc.space` public hostname's service URL to `http://127.0.0.1:43255`. Port 43155 belongs to `draw-app` and must remain untouched. After verifying this route, start the connector with `docker compose --profile deployment up -d tunnel`. The tunnel uses host networking so its `127.0.0.1` reaches the host-local proxy.
4. Run `scripts/backup.sh .private/backups/<date>` regularly. `scripts/verify-restore.sh BACKUP_DIRECTORY` restores the database to a temporary database and extracts the photo archive to a temporary directory. Keep encrypted off-host copies of both artifacts.

Database and worker ports are private. Uploaded originals and processed model inputs live in `.private/photos`; the database has a persistent volume, and the pinned ONNX models live in `.private/model-fast-v2.2`. The public dashboard shows the 224 × 224 JPEG used for the highest-waviness prediction among successful photos captured within three minutes of the newest successful photo; original uploads and normalized images remain private. `PHOTO_RETENTION_DAYS=0` retains originals and model inputs indefinitely; setting a positive number prunes private files after completed or failed jobs, while retaining photo metadata and predictions. `/api/today` is public; all `/api/admin/*` routes require the configured administrator session except login. Sessions are Secure, HTTP-only, SameSite Strict cookies, and mutations require CSRF tokens.

When upgrading an installation that used the `photos` Docker volume, back up the database and photos, stop the API and worker, and copy the volume contents into `.private/photos` before starting the updated services. Verify the copied files before switching storage.

Every Compose service uses Docker's `always` restart policy. With the Docker daemon enabled at boot, the database, API, worker, frontend, proxy, and deployment tunnel return after a host reboot and restart after process exits.

## Image inference

The worker loads the pinned fast-v2.2 SegFormer-B0 segmenter and EfficientNet-B0 regressor into ONNX Runtime sessions once in a supervised process, warms both graphs and the crop selector before reporting ready, then reuses them for queued photos. `INFERENCE_DEVICE=auto` tries NVIDIA CUDA first and falls back to CPU if CUDA setup or warm-up fails; set it to `cuda` to require CUDA or `cpu` to disable GPU use. Both models use CUDA with CPU fallback for unsupported graph operations. On the deployment host, warmed end-to-end predictions averaged about 195–201 ms with CUDA for both models, versus 259–283 ms with both models on CPU across the same three photos. The CUDA route is therefore the production default. `INFERENCE_THREADS` defaults to 4. Each raw photo gets one 448 × 448 segmentation pass and a lower-water crop mapped from the 224 × 224 mask onto the source image. The resulting masked JPEG is the regressor input; if a reliable water crop cannot be selected, the job fails with `no_water`. Diagnostics are stored under `diagnostics/<photo-id>` in the private store. An inference process that exits or times out is restarted and fully warmed before processing resumes. The worker records a score after successful inference when air and sea observations are available.

On three recent uploads, warmed fast-v2.2 CPU inference took 0.27–0.29 seconds per photo end to end on the deployment host. The same uploads previously took 0.91–1.38 seconds in the v19 CUDA pipeline. These are local measurements, not latency guarantees. The new release reports validation MAE 0.0985 and held-out test MAE 0.1324 on a different split snapshot; compare neither directly with v19 nor treat them as a safety assessment.

The admin can select multiple photos in one upload; each photo is queued and processed individually, and its prediction, score, queue time, preprocessing time and prediction time are shown separately. The admin panel and public dashboard use the highest waviness prediction among distinct photos captured within the three minutes preceding the newest successful photo. The public comfort score and preview photo are calculated from that same highest-waviness prediction using current temperature observations. Every photo still gets its own prediction and score revision. The newest group remains visible when no newer photo is available.

The model submodule's `preprocessing.json` defines the fast-v2.2 preprocessing contract. `backend/inference_service.py` implements it, and prediction rows record the ONNX artifact hashes as model and preprocessing versions.

## Collection

The worker attempts Open-Meteo and the explicit MGM station `İLKADIM/SAMSUN LİMAN KUZEY MENDİREK FENERİ` at the configured 06:00, 12:00, and 18:00 Türkiye-time slots. Its database claim is recorded before each request. Each source gets at most three attempts per local day, including failures. It does not retry or backfill missed slots. Browser visits and uploads do not call either provider. An unchanged measurement retains its original timestamp. The API reports missing and stale inputs independently.

## Score policy v1

`backend/scoring.py` holds the policy version, weights, band thresholds, cap, and piecewise linear curves. Wave comfort is 1 at 0, .85 at .3, .2 at .6, and 0 at 1. Sea comfort is 0 at 0°C, .1 at 16°C, .5 at 20°C, .9 at 24°C, and 1 at 28°C. Air comfort is 0 at −20°C, .2 at 20°C, 1 from 29–32°C, .6 at 40°C, and 0 at 50°C. Values interpolate linearly and clamp at the ends. Base weights are 60% wave, 30% sea, 10% air. Wave share falls by up to half as waviness rises; 75% of the released share goes to sea and 25% to air. Waviness ≥ .6 caps the final score at 20. These curves are experimental product choices, not validated safety thresholds. Increment the policy version when changing any constant or curve. Score revisions store immutable source IDs, the policy version, and an exact policy snapshot so historical results can be recalculated after policy changes.

## Verification

Run `python3 -m unittest discover -s tests` for focused scoring checks and `scripts/check-integration.sh` for disposable-database API and worker checks. The supplied HEIC has original capture time `2026-10-16 12:43:47.069 +03:00`. Physical iPhone Safari and standalone checks remain necessary before claiming full iOS validation.

The supplied HEIC produced waviness approximately 0.573 with v19 on CPU and GPU. The fast-v2.2 CPU comparison used three uploaded photos and repeated warm runs after model initialization. These observations are not speed guarantees.
