"""HTTP, worker, import, persistence, and capture-isolation regressions."""
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from test_vn300_button_logger import logger
import app


def sample_csv():
    fields = 'Pi_Elapsed_Time_s,Latitude_deg,Longitude_deg,Vel_N_mps,Vel_E_mps,Yaw_deg,PosUncertainty_m,Longitudinal_G,Lateral_G,Checksum_OK\n'
    return (fields + ''.join(f'{i / 10},35.{100000+i:06d},-97.1,10,0,{i % 360},0.5,{math.sin(i/20):.3f},{math.cos(i/20):.3f},True\n' for i in range(1000))).encode()


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {'SRT_DATA_DIR': str(self.root / 'runtime')})
        self.env.start()
        self.patch = mock.patch.multiple(logger, LOCAL_FALLBACK=self.root / 'logs', active_base_log_dir=self.root / 'logs',
                                         active_log_dir=self.root / 'logs' / 'boot', web_settings_path=self.root / 'config' / 'settings.json', web_effective_settings={})
        self.patch.start()
        logger.active_log_dir.mkdir(parents=True)
        logger.logging_requested.clear()
        logger.recording_busy.clear()
        logger.stop_requested.clear()
        logger.latest_packet['logging'] = False
        self.server = app.start_dashboard('127.0.0.1', 0)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        logger.logging_requested.clear()
        logger.recording_busy.clear()
        logger.latest_packet['logging'] = False
        self.server.shutdown()
        self.server.server_close()
        self.patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def request(self, route, data=None, raw=None):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + route, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read(), response.headers

    def get(self, route):
        return json.loads(self.request(route)[1])

    def post(self, route, data):
        return json.loads(self.request(route, data)[1])

    def upload(self, name='VN300_2026-07-12_RUN001_BINARY.csv'):
        self.request('/api/import?name='+name, raw=sample_csv())
        return self.get('/api/sessions')['sessions'][0]

    def wait_job(self, jid):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = next(j for j in self.get('/api/jobs')['jobs'] if j['id'] == jid)
            if job['state'] in ('complete', 'error'):
                return job
            time.sleep(.1)
        self.fail('Worker did not complete')

    def test_direct_routes_offline_assets_and_path_boundaries(self):
        for page in ('track','live','sensors','sessions','analysis','models','setup','help'):
            status, data, headers = self.request('/'+page+'?refresh=1')
            self.assertEqual(status, 200)
            self.assertIn(b'SOONER RACING TEAM', data)
        for route in ('/assets/team-logo.png','/assets/team-car.jpg','/report.css','/gates.js','/sensors.js','/trace.js','/assets/ou-map.jpg','/assets/riverwind-map.jpg','/assets/map-areas.json','/examples/airspeed_calibration.example.json'):
            self.assertEqual(self.request(route)[0], 200)
        for route in ('/logger.py','/assets/../config/settings.json','/api/results/no-job/../../app.py'):
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request(route)
            self.assertEqual(err.exception.code, 404)

    def test_storage_and_live_can_api(self):
        logger.reset_can_live()
        msg = type('Frame', (), {'arbitration_id': 291, 'is_extended_id': False,
                                'data': bytes([0, 128]), 'dlc': 2})()
        logger.update_can_live(msg, [({'channel': 'RPM', 'unit': 'rpm'}, 32768, 1000)])
        logger.update_can_live(msg, [({'channel': 'RPM', 'unit': 'rpm'}, 32769, 1001)])
        extended = type('Frame', (), {'arbitration_id': 291, 'is_extended_id': True,
                                    'data': bytes([1]), 'dlc': 1})()
        logger.update_can_live(extended, [])
        snapshot = self.get('/api/can')
        self.assertEqual(len(snapshot['frames']), 2)
        self.assertEqual(snapshot['frames'][0]['count'], 2)
        self.assertEqual(snapshot['frames'][0]['data'], '00 80')
        self.assertEqual(snapshot['signals'][0]['value'], 1001)
        self.assertEqual(snapshot['signals'][0]['unit'], 'rpm')
        self.assertGreaterEqual(snapshot['signals'][0]['age_s'], 0)
        with mock.patch.object(logger, 'CAN_LIVE_FRAME_LIMIT', 2):
            extended.arbitration_id = 292
            logger.update_can_live(extended, [])
        self.assertEqual(len(logger.public_can_snapshot()['frames']), 2)
        self.assertEqual(logger.public_can_snapshot()['evictions'], 1)
        logger.reset_can_live()
        storage = self.get('/api/storage')
        self.assertEqual(storage['logs']['path'], str(logger.active_log_dir))
        self.assertGreater(storage['system']['total_bytes'], storage['system']['free_bytes'])
        # Cached disk probes never take the acquisition lock or run on every poll.
        with mock.patch('app.shutil.disk_usage', side_effect=AssertionError('uncached query')):
            self.assertEqual(self.get('/api/storage'), storage)

    def test_can_display_scaling_is_live_validated_and_persistent(self):
        logger.reset_can_live()
        msg = type('Frame', (), {'arbitration_id': 0x640, 'is_extended_id': False,
                                'data': bytes(8), 'dlc': 8})()
        logger.update_can_live(msg, [({'channel': 'Engine_Speed', 'display_name': 'Engine › Speed', 'unit': 'source units'}, 1000, 6000)])
        row = self.get('/api/can')['signals'][0]
        key = row['key']
        logger.logging_requested.set()  # Display adjustments are safe during capture.
        self.post('/api/can_scaling', {'key': key, 'multiplier': 2, 'divider': 3})
        adjusted = self.get('/api/can')['signals'][0]
        self.assertEqual(adjusted['display_value'], 4000)
        self.assertEqual(adjusted['value'], 6000)
        self.assertEqual(adjusted['raw'], 1000)
        self.assertEqual(logger.public_can_snapshot()['signals'][0]['value'], 6000)
        for invalid in ({'key': key, 'multiplier': 1, 'divider': 0},
                        {'key': key, 'multiplier': True, 'divider': 1},
                        {'key': key, 'multiplier': '2', 'divider': 1},
                        {'key': key, 'multiplier': 1e20, 'divider': 1},
                        {'key': 'unknown', 'multiplier': 1, 'divider': 1}):
            with self.subTest(invalid=invalid), self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/can_scaling', invalid)
            self.assertEqual(error.exception.code, 400)
        logger.logging_requested.clear()
        self.server.shutdown()
        self.server.server_close()
        self.server = app.start_dashboard('127.0.0.1', 0)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.get('/api/can')['signals'][0]['display_value'], 4000)
        # A later sample uses the same saved adjustment.
        logger.update_can_live(msg, [({'channel': 'Engine_Speed'}, 1200, 7200)])
        self.assertEqual(self.get('/api/can')['signals'][0]['display_value'], 4800)
        self.post('/api/can_scaling', {'key': key, 'multiplier': 1, 'divider': 1})
        self.assertEqual(self.get('/api/can')['signals'][0]['display_value'], 7200)
        self.assertEqual(json.loads((self.root / 'runtime' / 'can_scaling.json').read_text()), {})

    def test_import_catalog_individual_and_session_download(self):
        session = self.upload()
        self.assertTrue(session['analyzable'])
        self.assertEqual(self.request('/api/files/'+session['files'][0]['id'])[1], sample_csv())
        data = self.request('/api/sessions/'+session['id']+'/download')[1]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read(session['files'][0]['name']), sample_csv())
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/jobs', {'command':'gg','sessions':[session['id']], 'params':{'driver_map':'/etc/passwd'}})
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/jobs', {'command':'gg','sessions':[session['id']], 'params':{'out':'/tmp/override'}})

    def test_rejects_zip_traversal_symlinks_and_recording_import(self):
        for name, mode in (('../escape.csv',0),('linked.csv',0o120777)):
            data = io.BytesIO()
            with zipfile.ZipFile(data,'w') as archive:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = mode << 16
                archive.writestr(entry,b'bad')
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.request('/api/import?name=logs.zip', raw=data.getvalue())
            self.assertEqual(err.exception.code,400)
        logger.logging_requested.set()
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.upload()
        self.assertEqual(err.exception.code,409)
        self.assertFalse(any(self.server.panel.imports.iterdir()))

    def test_real_analysis_queue_defers_and_saves_exports(self):
        session = self.upload()
        logger.logging_requested.set()
        job = self.post('/api/jobs', {'command':'laps', 'sessions':[session['id']], 'params':{'auto_sectors':3}})
        time.sleep(.7)
        self.assertIsNone(self.server.panel.process)
        self.assertEqual(self.get('/api/jobs')['jobs'][0]['state'], 'queued')
        logger.logging_requested.clear()
        result = self.wait_job(job['id'])
        self.assertEqual(result['state'], 'complete', result.get('log'))
        self.assertTrue(any(f['name']=='summary.csv' for f in result['files']))
        report = next(f for f in result['files'] if f['name']=='report.html')
        status, html, headers = self.request(report['url'])
        self.assertIn(b'SOONER RACING TEAM', html)
        self.assertIn('sandbox allow-scripts',headers['Content-Security-Policy'])
        self.assertTrue((self.server.panel.results / session['id'] / job['id'] / 'job.json').exists())
        self.assertFalse((app.APP_DIR / 'data').exists())

    def test_capture_guard_stops_real_process_and_waits_through_flush(self):
        panel = self.server.panel
        directory = panel.results / 'test' / 'slow'
        directory.mkdir(parents=True)
        path = directory / 'job.json'
        job={'id':'slow','state':'running','progress':'Computing','command':'gg','sessions':[], 'inputs':[], 'created':time.time(), 'params':{}}
        with panel.lock:
            panel.output=(directory/'worker.log').open('ab')
            panel.process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
            panel.running='slow'
            panel.jobs['slow']=(path,job)
        logger.toggle_logging_request(True)
        self.assertEqual(job['state'],'paused')
        acquired=logger.begin_acquisition()
        logger.toggle_logging_request(False)
        panel.step()
        self.assertEqual(job['state'],'paused')
        logger.finish_acquisition(*acquired)
        panel.step()
        self.assertEqual(job['state'],'running')
        panel.cancel('slow')
        self.assertEqual(job['state'],'cancelled')

    def test_missing_dependencies_inputs_worker_errors_and_integer_zero(self):
        schema=self.get('/api/analysis')['commands']
        self.server.panel.schema['minimum-lap']['missing_dependencies']=['missing-test-package']
        with self.assertRaises(urllib.error.HTTPError) as unavailable:
            self.post('/api/jobs',{'command':'minimum-lap','params':{}})
        self.assertIn('Unavailable optional dependencies', unavailable.exception.read().decode())
        self.server.panel.schema['minimum-lap']['missing_dependencies']=schema['minimum-lap']['missing_dependencies']
        self.assertIn('minimum-lap',schema)
        self.assertTrue(any(f['name']=='reference_start_row' for f in schema['minimum-lap']['fields']))
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post('/api/jobs',{'command':'tire-model','params':{}})
        self.assertIn('Required input',err.exception.read().decode())
        session=self.upload()
        response=self.post('/api/jobs',{'command':'gg','sessions':[session['id']], 'params':{'angle_bin_deg':7}})
        result=self.wait_job(response['id'])
        self.assertEqual(result['state'],'error')
        self.assertIn('divide evenly',result['log'])
        self.server.panel.schema['minimum-lap']['missing_dependencies']=[]
        # Required zero-based start row must survive argument validation.
        self.request('/api/import?name=fit.tir',raw=b'[MODEL]\n')
        files=self.get('/api/sessions')['inputs']
        tid=next(f['id'] for f in files if f['name']=='fit.tir')
        logger.logging_requested.set()
        response=self.post('/api/jobs',{'command':'minimum-lap','params':{'reference_csv':session['files'][0]['id'],'reference_start_row':0,'reference_samples':100,'reference_duration_s':10,'tir':tid}})
        argv=self.server.panel.jobs[response['id']][1]['argv']
        self.assertEqual(argv[argv.index('--reference-start-row')+1],'0')
        self.server.panel.cancel(response['id'])

    def test_settings_preserve_existing_and_calibration_validates(self):
        settings=logger.web_settings_path
        settings.parent.mkdir()
        settings.write_text(json.dumps({'baud':921600,'no_buttons':True,'auto_start':True}))
        self.post('/api/settings',{'baud':115200})
        saved=json.loads(settings.read_text())
        self.assertTrue(saved['auto_start'])
        self.assertTrue(saved['no_buttons'])
        self.assertEqual(saved['baud'],115200)
        for invalid in ({'baud':False},{'baud':-1},{'can_enable':'false'},{'unknown':1}):
            with self.assertRaises(urllib.error.HTTPError):
                self.post('/api/settings',invalid)
        logger.logging_requested.set()
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.post('/api/settings',{'baud':921600})
        self.assertEqual(err.exception.code,409)
        logger.logging_requested.clear()
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/airspeed_calibration',{'sensor':'bad','source':'test','voltage_to_speed':[[1,1],[2,2]]})
        with mock.patch.object(logger, 'CONFIG_DIR', self.root/'config'):
            self.post('/api/airspeed_calibration',{'sensor':'test','source':'bench','voltage_to_speed':[[1,0],[2,10]]})
            self.assertEqual(self.get('/api/airspeed_calibration')['calibration']['sensor'],'test')

    def test_all_real_model_tools_and_gg_chain(self):
        if self.server.panel.schema['minimum-lap']['missing_dependencies']:
            self.skipTest('Optional numerical model dependencies unavailable')
        # Explicit synthetic inputs verify interfaces, not physical tire performance.
        text = sample_csv().decode().replace('Checksum_OK\n', 'Checksum_OK,Speed_mps,Common_AngularRate_Z_rps,Speed_mph\n')
        text = text.splitlines()[0] + '\n' + '\n'.join(line + ',10,0.1,22.36936' for line in text.splitlines()[1:]) + '\n'
        self.request('/api/import?name=VN300_2026-07-12_RUN001_BINARY.csv',raw=text.encode())
        session=self.get('/api/sessions')['sessions'][0]
        gg=self.post('/api/jobs',{'command':'gg','sessions':[session['id']],'params':{'min_bin_points':1,'min_driver_points':1}})
        result=self.wait_job(gg['id'])
        self.assertEqual(result['state'],'complete',result.get('log'))
        tir=b'FNOMIN = 750\nNOMPRES = 82737\nPDX1 = 1.8\nPDX2 = -0.1\nPDX3 = 0\nPDY1 = 2\nPDY2 = -0.1\nPDY3 = 0\n'
        self.request('/api/import?name=bench-fit.tir',raw=tir)
        files=self.get('/api/sessions')['inputs']
        tid=next(f['id'] for f in files if f['name']=='bench-fit.tir')
        envelope=next(f['id'] for f in files if 'gg_envelope_by_driver.csv' in f['name'])
        for command,params in (
            ('tire-model',{'tir':tid,'driver_envelope':envelope}),
            ('tire-comparison',{'lc0_tir':tid,'r20_tir':tid}),
            ('minimum-lap',{'tir':tid,'reference_csv':session['files'][0]['id'],'reference_start_row':0,'reference_samples':1000,'reference_duration_s':100,'grip_scales':'0.7,1.0'}),
        ):
            with self.subTest(command=command):
                submitted=self.post('/api/jobs',{'command':command,'sessions':[session['id']],'params':params})
                result=self.wait_job(submitted['id'])
                self.assertEqual(result['state'],'complete',result.get('log'))
                self.assertTrue(any(f['name'].endswith('.html') for f in result['files']))
                self.assertEqual(result['result_group'],session['id'])

    def test_saved_configuration_and_jobs_survive_restart(self):
        timing={'mode':'lap','start_lat1':35.1,'start_lon1':-97.1,'start_lat2':35.2,'start_lon2':-97.1,'min_speed_mph':5,'min_gap_s':8}
        self.post('/api/config',timing)
        self.post('/api/run_metadata',{'driver':'Persistence driver','course':'Test'})
        self.server.shutdown();self.server.server_close()
        logger.track_config['mode']=None
        logger.update_run_metadata({'driver':''})
        self.server=app.start_dashboard('127.0.0.1',0)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.get('/api/config')['config']['mode'],'lap')
        self.assertEqual(self.get('/api/run_metadata')['metadata']['driver'],'Persistence driver')


if __name__=='__main__':
    unittest.main()
