"""Run against disposable swimmable_budget_test DB from the API container."""
from datetime import datetime, time, timezone, timedelta
from urllib.error import HTTPError
from multiprocessing import get_context
from backend import db, worker

db.DATABASE_URL = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_budget_test'
db.migrate()
local_day = datetime.now(worker.TZ).date()

def fake_air():
    return 'test-air', datetime.now(timezone.utc), 21.0, {}

def fake_sea():
    raise ValueError('parser_failure')

worker.fetch_air = fake_air
worker.fetch_sea = fake_sea

def run(hour):
    worker.collect_due(datetime.combine(local_day, time(hour), worker.TZ).astimezone(timezone.utc))

ctx = get_context('fork')
children = [ctx.Process(target=run, args=(12,)) for _ in range(4)]
for child in children: child.start()
for child in children:
    child.join(30)
    assert child.exitcode == 0
with db.connection() as conn:
    rows = conn.execute('SELECT source,count(*) AS n FROM collection_attempts GROUP BY source').fetchall()
assert {r['source']: r['n'] for r in rows} == {'air':1,'sea':1}, rows
run(12)
run(6)
run(18)
with db.connection() as conn:
    rows = conn.execute('SELECT source,count(*) AS n FROM collection_attempts GROUP BY source').fetchall()
    failures = conn.execute("SELECT count(*) AS n FROM collection_attempts WHERE source='sea' AND state='failed'").fetchone()['n']
assert {r['source']: r['n'] for r in rows} == {'air':3,'sea':3}, rows
assert failures == 3
# A provider 429 with Retry-After suppresses the next slot.
local_day = local_day + timedelta(days=1)
def limited():
    raise HTTPError('https://example.invalid', 429, 'rate limited', {'Retry-After':'43200'}, None)
worker.fetch_air = limited
run(6)
run(12)
with db.connection() as conn:
    count = conn.execute("SELECT count(*) AS n FROM collection_attempts WHERE source='air' AND local_day=%s", (local_day,)).fetchone()['n']
    backoff = conn.execute("SELECT retry_after_at IS NOT NULL AS set FROM collection_attempts WHERE source='air' AND local_day=%s", (local_day,)).fetchone()['set']
assert count == 1 and backoff
run(18)
with db.connection() as conn:
    count = conn.execute("SELECT count(*) AS n FROM collection_attempts WHERE source='air' AND local_day=%s", (local_day,)).fetchone()['n']
assert count == 2
print('concurrent claims, restart, failed attempts, three-per-day budget, rate-limit backoff: OK')
