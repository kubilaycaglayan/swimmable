"""Run against disposable swimmable_retention_test DB from the API container."""
import os
from pathlib import Path
from uuid import uuid4
from backend import db, worker

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_retention_test'
db.migrate()
root = Path('/tmp/swimmable-retention-test')
(root / 'normalized').mkdir(parents=True, exist_ok=True)
original, model_input = root / 'original.jpg', root / 'input.jpg'
photo_id = uuid4()
normalized = root / 'normalized' / f'{photo_id}.png'
for path in (original, model_input, normalized): path.write_bytes(b'test-only')
with db.connection() as conn:
    conn.execute("""INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,width,height,format,uploaded_at)
      VALUES (%s,%s,%s,'test.jpg',now()-interval '3 days',false,224,224,'JPEG',now()-interval '3 days')""", (photo_id, str(photo_id), str(original)))
    conn.execute("INSERT INTO prediction_jobs(id,photo_id,state) VALUES (%s,%s,'completed')", (uuid4(),photo_id))
    conn.execute("INSERT INTO predictions(photo_id,waviness,model_version,preprocessing_version,input_path) VALUES (%s,.5,'test','test',%s)", (photo_id,str(model_input)))
os.environ['PHOTO_RETENTION_DAYS'] = '1'
os.environ['PRIVATE_STORE'] = str(root)
worker.prune_private_files()
assert all(not path.exists() for path in (original,model_input,normalized))
with db.connection() as conn:
    assert conn.execute('SELECT files_pruned_at IS NOT NULL AS pruned FROM photos WHERE id=%s', (photo_id,)).fetchone()['pruned']
    assert conn.execute('SELECT count(*) AS n FROM predictions').fetchone()['n'] == 1
import shutil
shutil.rmtree(root)
print('retention prunes private files and preserves metadata/predictions: OK')
