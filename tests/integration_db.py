"""Run against a disposable database from inside the API container."""
from datetime import datetime, timedelta, time, timezone
from uuid import uuid4
from backend import api, db

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_selection_test'
db.migrate()
now = datetime.now(timezone.utc)
local_day = now.astimezone(api.TZ).date()
with db.connection() as conn:
    for source in ('air', 'sea'):
        conn.execute('INSERT INTO source_observations(source,station,measured_at,temperature_c) VALUES (%s,%s,%s,%s)', (source, 'test', now, 25.0))
    photo_ids = [uuid4() for _ in range(3)]
    captures = [datetime.combine(local_day, time(hour), api.TZ).astimezone(timezone.utc) for hour in (8, 10, 11)]
    for i, photo_id in enumerate(photo_ids):
        conn.execute('INSERT INTO photos(id,sha256,private_path,original_name,captured_at,offset_assumed,width,height,format) VALUES (%s,%s,%s,%s,%s,false,224,224,\'JPEG\')', (photo_id, str(photo_id), '/private/test.jpg', 'test.jpg', captures[i]))
        conn.execute('INSERT INTO prediction_jobs(id,photo_id,state) VALUES (%s,%s,%s)', (uuid4(), photo_id, 'completed' if i < 2 else 'failed'))
    # The later capture completes first; the earlier capture finishes later.
    conn.execute('INSERT INTO predictions(photo_id,waviness,model_version,preprocessing_version,input_path,completed_at) VALUES (%s,.8,\'test\',\'test\',\'test\',%s)', (photo_ids[1], now - timedelta(minutes=30)))
    conn.execute('INSERT INTO predictions(photo_id,waviness,model_version,preprocessing_version,input_path,completed_at) VALUES (%s,.2,\'test\',\'test\',\'test\',%s)', (photo_ids[0], now))
result = api.today()
assert result['wave']['value'] == .8, result
assert result['score'] <= 20, result
assert not result['wave']['previous_day'], result
with db.connection() as conn:
    for i, photo_id in enumerate(photo_ids[:2]):
        captured = datetime.combine(local_day - timedelta(days=1), time(21 + i), api.TZ)
        conn.execute('UPDATE photos SET captured_at=%s WHERE id=%s', (captured, photo_id))
result = api.today()
assert result['wave']['previous_day'] and result['score_old_inputs'], result
with db.connection() as conn:
    conn.execute('UPDATE source_observations SET measured_at=%s', (now - timedelta(hours=25),))
result = api.today()
assert result['air']['stale'] and result['sea']['stale'] and result['score_old_inputs'], result
with db.connection() as conn:
    conn.execute('DELETE FROM score_revisions')
    conn.execute('DELETE FROM source_observations WHERE source=\'sea\'')
result = api.today()
assert result['sea'] is None and result['score'] is None, result
# One minute after Istanbul midnight, yesterday's 23:59 capture is still
# younger than 24 hours but must carry the previous-day note.
fixed_now = datetime(2026, 10, 16, 21, 1, tzinfo=timezone.utc)
class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)
api.datetime = FixedDatetime
with db.connection() as conn:
    conn.execute('UPDATE photos SET captured_at=%s WHERE id=%s', (datetime(2026,10,16,20,59,tzinfo=timezone.utc), photo_ids[1]))
    conn.execute('UPDATE photos SET captured_at=%s WHERE id=%s', (datetime(2026,10,16,20,58,tzinfo=timezone.utc), photo_ids[0]))
    conn.execute('UPDATE source_observations SET measured_at=%s', (datetime(2026,10,16,20,59,tzinfo=timezone.utc),))
    conn.execute("INSERT INTO source_observations(source,station,measured_at,temperature_c) VALUES ('sea','test',%s,25)", (datetime(2026,10,16,20,59,tzinfo=timezone.utc),))
result = api.today()
assert result['today'] == '2026-10-17' and result['wave']['previous_day'] and not result['wave']['stale'], result
assert result['score_old_inputs'], result
print('selection, failed job, previous day, stale, missing data, midnight rollover: OK')
