"""Run against disposable swimmable_upload_test DB from the API container."""
import io
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from PIL import Image
from backend import db

scratch_url = db.DATABASE_URL.rsplit('/', 1)[0] + '/swimmable_upload_test'
with tempfile.TemporaryDirectory(prefix='swimmable-upload-test-') as private_store:
    env = os.environ.copy()
    env['DATABASE_URL'] = scratch_url
    env['PRIVATE_STORE'] = private_store
    servers = [subprocess.Popen(['uvicorn','backend.api:app','--host','127.0.0.1','--port',str(port),'--no-access-log'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for port in (18001,18002)]
    try:
        for port in (18001,18002):
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1): break
                except Exception: time.sleep(.1)
            else: raise RuntimeError('test API did not start')
        exif = Image.Exif()
        exif[36867] = '2026:10:16 12:00:00'
        buffer = io.BytesIO()
        Image.new('RGB', (300,300), '#087b98').save(buffer, format='JPEG', exif=exif)
        image = buffer.getvalue()
        barrier = Barrier(2)
        def upload(port):
            base=f'http://127.0.0.1:{port}'
            credentials=json.dumps({'username':os.environ['ADMIN_USERNAME'],'password':os.environ['ADMIN_PASSWORD']}).encode()
            with urllib.request.urlopen(urllib.request.Request(base+'/api/admin/login',credentials,{'Content-Type':'application/json'}), timeout=15) as response:
                csrf=json.load(response)['csrf']
                cookie=response.headers['Set-Cookie'].split(';',1)[0]
            boundary='test-boundary'
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="same.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+image+(f'\r\n--{boundary}--\r\n').encode()
            barrier.wait(timeout=10)
            with urllib.request.urlopen(urllib.request.Request(base+'/api/admin/photos',body,{'Cookie':cookie,'X-CSRF-Token':csrf,'Content-Type':f'multipart/form-data; boundary={boundary}'}), timeout=15) as response:
                return response.status,json.load(response)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(upload,(18001,18002)))
        assert [status for status,_ in results]==[202,202], results
        assert sorted(item['duplicate'] for _,item in results)==[False,True], results
        db.DATABASE_URL=scratch_url
        with db.connection() as conn:
            assert conn.execute('SELECT count(*) AS n FROM photos').fetchone()['n']==1
            assert conn.execute('SELECT count(*) AS n FROM prediction_jobs').fetchone()['n']==1
        assert len(list((Path(private_store)/'originals').iterdir()))==1
        print('concurrent identical uploads return one queued job: OK')
    finally:
        for process in servers: process.terminate()
        for process in servers: process.wait(timeout=10)
