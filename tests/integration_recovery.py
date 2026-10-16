"""Run against disposable swimmable_recovery_test DB from the API container."""
from uuid import uuid4
from backend import db, worker

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_recovery_test'
db.migrate()
processing, completed = uuid4(), uuid4()
with db.connection() as conn:
    for photo_id, state in ((processing, 'processing'), (completed, 'completed')):
        conn.execute("""INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,width,height,format)
          VALUES (%s,%s,'/private/test.jpg','test.jpg',now(),false,224,224,'JPEG')""", (photo_id, str(photo_id)))
        conn.execute('INSERT INTO prediction_jobs(id,photo_id,state,started_at) VALUES (%s,%s,%s,now())', (uuid4(), photo_id, state))
worker.recover_interrupted_jobs()
with db.connection() as conn:
    rows = conn.execute('SELECT photo_id,state,started_at FROM prediction_jobs').fetchall()
by_photo = {row['photo_id']: row for row in rows}
assert by_photo[processing]['state'] == 'queued' and by_photo[processing]['started_at'] is None
assert by_photo[completed]['state'] == 'completed' and by_photo[completed]['started_at'] is not None
print('interrupted job requeued; completed job preserved: OK')
