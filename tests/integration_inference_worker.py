"""Run with the worker environment against a disposable database."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import os
from backend import db, worker

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_inference_test'
db.migrate()
photo_id, job_id = uuid4(), uuid4()
with TemporaryDirectory() as directory:
    os.environ['PRIVATE_STORE'] = directory
    original = Path(directory) / 'photo.jpg'
    original.write_bytes(b'test photo')
    with db.connection() as conn:
        for kind in ('air', 'sea'):
            conn.execute('INSERT INTO source_observations(source,station,measured_at,temperature_c) VALUES (%s,%s,now(),25)', (kind, 'test'))
        conn.execute("INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,width,height,format) VALUES (%s,%s,%s,'test.jpg',now(),false,224,224,'JPEG')", (photo_id, str(photo_id), str(original)))
        conn.execute('INSERT INTO prediction_jobs(id,photo_id) VALUES (%s,%s)', (job_id, photo_id))
    class FakeInference:
        def predict(self, photo, model_input, diagnostics):
            assert photo == original
            model_input.write_bytes(b'processed')
            return 'ok', 0.5
    assert worker.process_one(FakeInference())
    with db.connection() as conn:
        row = conn.execute('SELECT j.state,p.waviness,p.model_version,r.score FROM prediction_jobs j JOIN predictions p ON p.photo_id=j.photo_id JOIN score_revisions r ON r.prediction_id=p.id WHERE j.id=%s', (job_id,)).fetchone()
    assert row['state'] == 'completed' and row['waviness'] == 0.5 and row['model_version'] == 'v19:a77c370d' and row['score'] is not None, row
    assert (Path(directory) / 'model-inputs' / f'{photo_id}.jpg').read_bytes() == b'processed'
print('v19 job, saved input and immediate score: OK')
