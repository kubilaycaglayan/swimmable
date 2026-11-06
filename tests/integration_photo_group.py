"""Verify recent-photo maximum selection and separate historical score records."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from backend import api, db

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_photo_group_test'
db.migrate()
now = datetime.now(timezone.utc)
with db.connection() as conn:
    for kind in ('air', 'sea'):
        conn.execute('INSERT INTO source_observations(source,station,measured_at,temperature_c) VALUES (%s,%s,%s,25)', (kind, 'test', now))
    for seconds, waviness in ((0, 0.2), (179, 0.8), (181, 0.99)):
        photo = uuid4()
        conn.execute("INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,width,height,format) VALUES (%s,%s,'/private/test.jpg','test.jpg',%s,false,224,224,'JPEG')", (photo, str(photo), now - timedelta(seconds=seconds)))
        conn.execute('INSERT INTO predictions(photo_id,waviness,model_version,preprocessing_version,input_path) VALUES (%s,%s,%s,%s,%s)', (photo, waviness, 'test', 'test', '/private/test.jpg'))
public = api.today()
assert public['wave']['value'] == 0.8 and public['score'] == api.score(0.8, 25, 25), public
assert public['wave']['captured_at'] == (now - timedelta(seconds=179)).isoformat(), public
api.session = lambda request: None
result = api.photo_average(None)
assert result['photo_count'] == 2 and result['waviness'] == 0.8, result
assert result['score'] == api.score(0.8, 25, 25), result
with db.connection() as conn:
    rows = conn.execute('SELECT p.waviness,r.score FROM score_revisions r JOIN predictions p ON p.id=r.prediction_id ORDER BY p.waviness').fetchall()
assert len(rows) == 2 and [row['score'] for row in rows] == [api.score(0.2,25,25), api.score(0.8,25,25)], rows
print('public and admin three-minute maximum, unique photos and separate score history: OK')
