"""Integration checks using an isolated database and a real local HTTP server."""
import json
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'events.db'
        # Exercise migration from the old deployed schema.
        with closing(sqlite3.connect(cls.db)) as db:
            db.executescript('CREATE TABLE nodes(node_id TEXT PRIMARY KEY,name TEXT,lat REAL,lon REAL);'
                "INSERT INTO nodes VALUES('N01','Old point',0,0);"
                'CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,node_id TEXT,cam_id TEXT,ts TEXT,type TEXT,track_id TEXT,class TEXT,conf REAL,zone_id TEXT,plate_text TEXT,plate_conf REAL,plate_img TEXT,clip TEXT,received_at TEXT DEFAULT CURRENT_TIMESTAMP);'
                "INSERT INTO events(node_id,cam_id,ts,type,class,clip) VALUES('N01','C01','2026-11-01T00:00:00Z','wrong_way','car','old.mp4');")
            db.commit()
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); cls.port=s.getsockname()[1]
        cls.base=f'http://127.0.0.1:{cls.port}'
        # The Windows venv launcher spawns a child; run the interpreter directly
        # so terminate/wait also releases the server's database handles.
        cls.server=subprocess.Popen([os.sys._base_executable,'-m','uvicorn','m6_backend.main:app','--host','127.0.0.1','--port',str(cls.port)],cwd=ROOT,env={**os.environ,'EVENTS_DB_PATH':str(cls.db),'PYTHONPATH':str(ROOT/'.venv/Lib/site-packages')},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                cls.request('GET','/health'); break
            except Exception:
                time.sleep(.1)
        else:
            cls.server.terminate();cls.server.wait(timeout=10);raise RuntimeError('Test API did not start')

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate();cls.server.wait(timeout=10);cls.tmp.cleanup()

    @classmethod
    def request(cls,method,path,data=None):
        req=Request(cls.base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=10) as res:
            body=res.read();return json.loads(body) if res.headers.get_content_type()=='application/json' else body

    def create(self,node='N01',camera='C01'):
        return self.request('POST','/events',{'node_id':node,'cam_id':camera,'ts':'2026-11-18T10:30:15.125+05:30','type':'wrong_way','track_id':41,'class':'car','conf':.95,'zone_id':'Z1','plate':None,'clip':'camera_recordings/point_1.mp4#t=15.125','video_ts_s':15.125})['id']

    def test_coordinates_and_migration(self):
        nodes=self.request('GET','/nodes')
        self.assertEqual([n['node_id'] for n in nodes],['N01','N02'])
        self.assertAlmostEqual(nodes[0]['lat'],29.8639444444)
        self.assertAlmostEqual(nodes[0]['lon'],77.8954166667)
        self.assertAlmostEqual(nodes[1]['lat'],29.8649444444)
        self.assertAlmostEqual(nodes[1]['lon'],77.9008333333)
        old=next(e for e in self.request('GET','/events') if e['id']==1)
        self.assertTrue(old['archived'])
        self.assertNotIn(1,[e['id'] for e in self.request('GET','/events?active=true')])

    def test_false_alert_persists_and_keeps_clip_time(self):
        eid=self.create()
        self.request('PATCH',f'/events/{eid}/status',{'status':'false_alert'})
        self.assertNotIn(eid,[e['id'] for e in self.request('GET','/events?status=pending&active=true')])
        event=next(e for e in self.request('GET','/events?status=false_alert') if e['id']==eid)
        self.assertEqual(event['ts'],'2026-11-18T10:30:15.125+05:30')
        self.assertEqual(event['video_ts_s'],15.125)
        self.assertTrue(event['status_at'].endswith('+00:00'))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT status FROM events WHERE id=?',(eid,)).fetchone()[0],'false_alert')

    def test_action_and_authority_audit_and_duplicate_clicks(self):
        eid=self.create('N02','C02')
        with self.assertRaises(HTTPError) as err:
            self.request('PATCH',f'/events/{eid}/status',{'status':'authority_requested'})
        self.assertEqual(err.exception.code,409)
        for status in ['action_taken','action_taken','authority_requested','authority_requested']:
            self.request('PATCH',f'/events/{eid}/status',{'status':status})
        actions=self.request('GET',f'/events/{eid}/actions')
        self.assertEqual([a['status'] for a in actions],['action_taken','authority_requested'])
        self.assertTrue(all(a['ts'] for a in actions))
        with self.assertRaises(HTTPError) as err:
            self.request('PATCH',f'/events/{eid}/status',{'status':'false_alert'})
        self.assertEqual(err.exception.code,409)

    def test_pagination(self):
        ids=[self.create() for _ in range(3)]
        page=self.request('GET',f'/events?before_id={ids[-1]}&limit=2')
        self.assertEqual([e['id'] for e in page],list(reversed(ids[:-1])))

    def test_pages_and_recording_config(self):
        for path in ['/','/map','/history','/dashboard-assets/dashboard.js','/dashboard-assets/dashboard.css']:
            self.assertTrue(self.request('GET',path))
        cameras=self.request('GET','/cameras')
        self.assertEqual([c['cam_id'] for c in cameras],['C01','C02'])
        for camera in cameras:
            for recording in camera['recordings']:
                self.assertTrue((ROOT/'m6_backend/media'/recording['path']).is_file())
        self.assertIn('pending',self.request('GET','/events-summary'))

    def test_standard_dashboard_routes_and_cache(self):
        for path in ['/', '/map', '/history']:
            with urlopen(self.base + path, timeout=10) as response:
                html = response.read().decode('utf-8')
                self.assertIn('id="nav-history"', html)
                self.assertIn('/dashboard-assets/dashboard.js', html)
                self.assertIn('id="map"', html)
                self.assertNotIn('id="video-form"', html)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with urlopen(self.base + '/dashboard-assets/dashboard.js', timeout=10) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_bad_status_and_missing_event(self):
        with self.assertRaises(HTTPError) as err:
            self.request('PATCH','/events/999999/status',{'status':'false_alert'})
        self.assertEqual(err.exception.code,404)
        with self.assertRaises(HTTPError) as err:
            self.request('PATCH','/events/1/status',{'status':'deleted'})
        self.assertEqual(err.exception.code,422)

if __name__=='__main__':unittest.main()
