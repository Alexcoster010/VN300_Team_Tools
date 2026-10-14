#!/usr/bin/env python3
"""Application entry point and HTTP API for the Pi dashboard."""
import datetime as dt
import json
import logging
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import logger

WEB_DIR = Path(__file__).resolve().parent / "web"


# The panel service and its worker share this file; no per-page Python modules.
import argparse
import atexit
import csv
import hashlib
import importlib.util
import io
import math
import mimetypes
import os
import re
import signal
import subprocess
import sys
import uuid
import zipfile
from urllib.parse import parse_qs, unquote

APP_DIR = Path(__file__).resolve().parent
MAX_UPLOAD = 256 * 1024 * 1024
ALLOWED_INPUTS = {'.csv', '.json', '.bin', '.tir', '.dbc'}


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def safe_files(root):
    """Never follow links from removable storage, imports, or result directories."""
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                yield path


def file_id(path):
    return hashlib.sha256(str(path.absolute()).encode()).hexdigest()[:24]


def input_info(path, origin, name=None):
    item = {'id': file_id(path), 'name': name or path.name, 'size': path.stat().st_size, 'origin': origin}
    if path.suffix.lower() == '.csv':
        with path.open('rb') as stream:
            line = stream.readline(8192).decode('utf-8-sig', errors='replace')
        try:
            item['columns'] = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            item['columns'] = []
    return item


def telemetry_input(item):
    return item['name'].endswith(('_BINARY.csv', '_VNINS.csv')) or {'Pi_Elapsed_Time_s', 'Latitude_deg', 'Longitude_deg', 'Vel_N_mps', 'Vel_E_mps'}.issubset(item.get('columns', []))


def capture_active():
    return logger.logging_requested.is_set() or logger.recording_busy.is_set() or bool(logger.latest_packet.get('logging'))


def analysis_schema():
    import analysis
    result = {}
    for command, factory in analysis.COMMAND_PARSERS.items():
        parser = factory()
        fields = []
        for action in parser._actions:
            if action.dest in ('help', 'out', 'csv_files', 'no_prompts'):
                continue
            fields.append({'name': action.dest, 'flag': action.option_strings[0] if action.option_strings else None,
                           'type': 'file' if action.type is Path else 'bool' if isinstance(action, argparse._StoreTrueAction) else 'number' if action.type in (int, float) else 'text',
                           'integer': action.type is int, 'multiple': action.nargs == '+',
                           'required': action.required or not action.option_strings,
                           'default': action.default, 'choices': action.choices, 'help': action.help or ''})
        missing = [name for name in ('numpy', 'pandas', 'scipy', 'matplotlib') if importlib.util.find_spec(name) is None] if command == 'minimum-lap' else []
        result[command] = {'fields': fields, 'description': parser.description or '', 'missing_dependencies': missing}
    return result


class PanelService:
    def can_snapshot(self):
        snapshot = logger.public_can_snapshot()
        with self.can_scaling_lock:
            adjustments = dict(self.can_scaling)
        for row in snapshot['signals']:
            key = json.dumps([row['id'], row['extended'], row['channel']], separators=(',', ':'))
            row['key'] = key
            row['scaling'] = adjustments.get(key, {'multiplier': 1, 'divider': 1})
            row['display_value'] = row['value'] * row['scaling']['multiplier'] / row['scaling']['divider']
        return snapshot

    def save_can_scaling(self, body):
        key = body.get('key')
        if not isinstance(key, str) or len(key) > 1024:
            raise ValueError('Select a received CAN signal')
        multiplier, divider = body.get('multiplier'), body.get('divider')
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e12 for v in (multiplier, divider)) or divider == 0:
            raise ValueError('Use finite multipliers/dividers within ±1e12; divider cannot be zero')
        rows = {row['key']: row for row in self.can_snapshot()['signals']}
        if key not in rows:
            raise ValueError('Select a received CAN signal')
        adjusted = rows[key]['value'] * multiplier / divider
        if not math.isfinite(adjusted):
            raise ValueError('Adjusted value is too large')
        adjustment = {'multiplier': multiplier, 'divider': divider}
        with self.can_scaling_lock:
            updated = dict(self.can_scaling)
            if multiplier == divider == 1:
                updated.pop(key, None)
            else:
                updated[key] = adjustment
            atomic_json(self.root / 'can_scaling.json', updated)
            self.can_scaling = updated
        return {'ok': True, 'key': key, 'scaling': adjustment}

    def storage_snapshot(self):
        # Cache filesystem queries independently of the acquisition state lock.
        with self.storage_lock:
            if self.storage_cache is not None and time.monotonic() - self.storage_checked < 5:
                return self.storage_cache
            volumes = {}
            for name, directory in [('system', Path('/')), ('logs', logger.active_log_dir or logger.active_base_log_dir or self.root)]:
                directory = Path(directory)
                try:
                    usage = shutil.disk_usage(directory)
                    volumes[name] = {'path': str(directory), 'total_bytes': usage.total,
                                     'free_bytes': usage.free, 'used_bytes': usage.used}
                except OSError as exc:
                    volumes[name] = {'path': str(directory), 'error': str(exc)}
            self.storage_cache = volumes
            self.storage_checked = time.monotonic()
            return volumes

    def __init__(self, directory):
        self.root = Path(directory).expanduser().resolve()
        if self.root == APP_DIR or self.root.is_relative_to(APP_DIR):
            raise ValueError('Web runtime data must be outside application source; use --data-dir')
        self.root.mkdir(parents=True, exist_ok=True)
        self.can_scaling_lock = threading.Lock()
        self.can_scaling = {}
        try:
            saved = json.loads((self.root / 'can_scaling.json').read_text())
            if not isinstance(saved, dict):
                raise ValueError('Expected saved CAN scaling object')
            for key, adjustment in saved.items():
                values = (adjustment['multiplier'], adjustment['divider'])
                if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e12 for v in values) or values[1] == 0:
                    raise ValueError('Invalid saved CAN scaling')
                self.can_scaling[key] = dict(adjustment)
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError):
            self.can_scaling = {}
            logging.warning('Could not load saved CAN display scaling')
        self.imports = self.root / 'imports'
        self.results = self.root / 'results'
        self.imports.mkdir(exist_ok=True)
        self.results.mkdir(exist_ok=True)
        self.schema = analysis_schema()
        self.lock = threading.RLock()
        self.storage_cache = None
        self.storage_checked = 0
        self.storage_lock = threading.Lock()
        self.jobs = {}
        self.process = None
        self.running = None
        self.output = None
        self.stopped = threading.Event()
        for path in safe_files(self.results):
            if path.name == 'job.json':
                try:
                    job = json.loads(path.read_text())
                    if job['state'] in ('running', 'paused'):
                        job.update(state='error', error='Worker interrupted by application restart; submit again.', progress='Interrupted')
                        atomic_json(path, job)
                    self.jobs[job['id']] = (path, job)
                except (ValueError, KeyError, OSError):
                    logging.warning('Unreadable job manifest: %s', path)
        self.thread = threading.Thread(target=self._loop, name='analysis-scheduler', daemon=True)
        self.thread.start()

    def catalog(self):
        roots = [*logger.log_archive_roots(), ('imported', self.imports)]
        files, groups = {}, {}
        seen = set()
        for origin, root in roots:
            for path in safe_files(root):
                if path in seen or path.suffix.lower() not in ALLOWED_INPUTS:
                    continue
                seen.add(path)
                item = input_info(path, origin)
                files[item['id']] = (path, item)
                # New run identities and legacy timestamp identities are both supported.
                match = re.match(r'^(VN300_\d{4}-\d{2}-\d{2}_(?:RUN\d+|\d{2}-\d{2}-\d{2}))(?:_|\.)', path.name)
                stem = match.group(1) if match else re.sub(r'_(BINARY|VNINS|VNIMU|STEERING|MASS_FLOW|MOTEC_RAW_CAN|MOTEC_CHANNELS|session_metadata)$', '', path.stem)
                if path.name.startswith(('VN300_run_metadata', 'VN300_dashboard_timing')) or path.suffix.lower() in ('.tir', '.dbc'):
                    continue
                sid = file_id(path.parent / stem)
                group = groups.setdefault(sid, {'id': sid, 'name': stem, 'origin': origin, 'folder': str(path.parent), 'files': [], 'metadata': {}, 'modified': 0})
                group['files'].append(item)
                group['modified'] = max(group['modified'], path.stat().st_mtime)
                if path.name.endswith('_session_metadata.json') and path.stat().st_size <= 1024 * 1024:
                    try:
                        metadata = json.loads(path.read_text())
                        if not isinstance(metadata, dict):
                            raise ValueError('Metadata must be an object')
                        group['metadata'] = metadata
                    except (OSError, ValueError):
                        group['metadata_error'] = 'Session metadata could not be read'
        sessions = sorted(groups.values(), key=lambda s: s['modified'], reverse=True)
        for session in sessions:
            session['analyzable'] = any(telemetry_input(f) for f in session['files'])
            session['active'] = capture_active() and (session['name'] == logger.latest_packet.get('session') or (not logger.latest_packet.get('session') and session['folder'] == str(logger.active_log_dir)))
        with self.lock:
            complete_directories = [path.parent / 'out' for path, job in self.jobs.values() if job['state'] == 'complete']
        for directory in complete_directories:
            for path in safe_files(directory):
                if path.suffix.lower() in ALLOWED_INPUTS:
                    fid = file_id(path)
                    files[fid] = (path, input_info(path, 'result', f'{directory.parent.name[:8]} / {path.name}'))
        return sessions, files

    def public_jobs(self):
        with self.lock:
            result = []
            for path, job in self.jobs.values():
                public = {k: v for k, v in job.items() if k not in ('argv', 'metadata_rows')}
                public['files'] = [{'name': str(p.relative_to(path.parent / 'out')), 'url': f'/api/results/{job["id"]}/{p.relative_to(path.parent / "out").as_posix()}'} for p in safe_files(path.parent / 'out')]
                log = path.parent / 'worker.log'
                if log.exists():
                    with log.open('rb') as stream:
                        stream.seek(max(0, log.stat().st_size - 8000))
                        public['log'] = stream.read().decode('utf-8', errors='replace')
                result.append(public)
            return sorted(result, key=lambda j: j['created'], reverse=True)

    def submit(self, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a job object')
        command = body.get('command')
        if command not in self.schema:
            raise ValueError('Unknown analysis command')
        spec = self.schema[command]
        if spec['missing_dependencies']:
            raise ValueError('Unavailable optional dependencies: ' + ', '.join(spec['missing_dependencies']))
        sessions, files = self.catalog()
        selected = body.get('sessions', [])
        if not isinstance(selected, list) or len(selected) > 100:
            raise ValueError('Select up to 100 sessions')
        selected = list(dict.fromkeys(selected))
        by_id = {s['id']: s for s in sessions}
        if any(s not in by_id for s in selected):
            raise ValueError('Selected session is unavailable')
        source = [by_id[s] for s in selected]
        if any(s['active'] for s in source):
            raise ValueError('Wait for the source recording to finish before analyzing it')
        argv = [command]
        if command in ('laps', 'gg'):
            if not source or any(not s['analyzable'] for s in source):
                raise ValueError('Select sessions containing BINARY or VNINS CSV files')
            for session in source:
                # BINARY preference is retained by analysis.expand_input_paths.
                argv.extend(str(files[f['id']][0]) for f in session['files'] if telemetry_input(f))
        params = body.get('params', {})
        if not isinstance(params, dict) or set(params) - {f['name'] for f in spec['fields']}:
            raise ValueError('Unknown analysis parameter')
        input_files = []
        for field in spec['fields']:
            value = params.get(field['name'])
            if value is None or value == '' or value is False:
                if field['required']:
                    raise ValueError('Required input: ' + field['name'])
                continue
            if field['type'] == 'file':
                if field['name'] == 'propulsion_root':
                    if value not in by_id or by_id[value]['active']:
                        raise ValueError('Select a finished propulsion source session')
                    value = by_id[value]['folder']
                else:
                    if value not in files:
                        raise ValueError('Input file unavailable: ' + field['name'])
                    path, item = files[value]
                    if any(s['active'] and any(f['id'] == value for f in s['files']) for s in sessions):
                        raise ValueError('Cannot analyze an active recording')
                    input_files.append(item)
                    value = str(path)
            elif field['type'] == 'number':
                values = value if field['multiple'] and isinstance(value, list) else [value]
                for number in values:
                    if type(number) not in (int, float) or not math.isfinite(number) or (field['integer'] and int(number) != number):
                        raise ValueError('Finite ' + ('integer' if field['integer'] else 'number') + ' required: ' + field['name'])
            elif field['type'] == 'bool':
                if type(value) is not bool:
                    raise ValueError('Boolean required: ' + field['name'])
            elif not isinstance(value, str) or len(value) > 4000:
                raise ValueError('Invalid parameter: ' + field['name'])
            if field['choices'] and value not in field['choices']:
                raise ValueError('Invalid choice: ' + field['name'])
            if field['flag']:
                argv.append(field['flag'])
            if field['type'] != 'bool':
                argv.extend(str(v) for v in value) if isinstance(value, list) else argv.append(str(value))
        if command == 'laps':
            argv.append('--no-prompts')
        jid = uuid.uuid4().hex
        anchors = [file_id(files[f['id']][0].parent / re.sub(r'_(BINARY|VNINS|VNIMU)$', '', files[f['id']][0].stem)) for f in input_files if f['origin'] != 'result']
        anchors.extend(files[f['id']][0].relative_to(self.results).parts[0] for f in input_files if f['origin'] == 'result')
        anchor = selected[0] if selected else anchors[0] if anchors else 'models'
        # Model results link to input files; session analyses are filed beneath the source session.
        directory = self.results / anchor / jid
        directory.mkdir(parents=True)
        argv.extend(['--out', str(directory / 'out')])
        job = {'id': jid, 'command': command, 'sessions': [{'id': s['id'], 'name': s['name']} for s in source],
               'inputs': input_files, 'params': params, 'result_group': anchor, 'created': time.time(), 'state': 'queued',
               'progress': 'Waiting for recording to stop' if capture_active() else 'Queued', 'argv': argv,
               'metadata_rows': [dict(s['metadata'].get('run_metadata', {}), session_file=s['name']) for s in source if isinstance(s['metadata'].get('run_metadata'), dict)]}
        with self.lock:
            if sum(j['state'] in ('queued', 'running', 'paused') for _, j in self.jobs.values()) >= 20:
                shutil.rmtree(directory)
                raise ValueError('Analysis queue is full (20 jobs)')
            path = directory / 'job.json'
            atomic_json(path, job)
            self.jobs[jid] = (path, job)
        return {'ok': True, 'id': jid, 'state': job['state']}

    def pause_for_capture(self):
        # Called under logger.acquisition_lock for HTTP, GPIO, and auto-start.
        with self.lock:
            if self.process and self.process.poll() is None and self.running:
                path, job = self.jobs[self.running]
                if job['state'] == 'running':
                    try:
                        os.killpg(self.process.pid, signal.SIGSTOP)
                        _, status = os.waitpid(self.process.pid, os.WUNTRACED)
                        if not os.WIFSTOPPED(status):
                            self.process.returncode = os.waitstatus_to_exitcode(status)
                            return
                    except ProcessLookupError:
                        return
                    job.update(state='paused', progress='Paused for recording')
                    try:
                        atomic_json(path, job)
                    except OSError:
                        logging.exception('Worker paused; could not persist state')

    def cancel(self, jid):
        with self.lock:
            if jid not in self.jobs:
                raise ValueError('Job unavailable')
            path, job = self.jobs[jid]
            if self.running == jid and self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
                self.output.close()
                self.process = self.running = self.output = None
            if job['state'] in ('queued', 'running', 'paused'):
                job.update(state='cancelled', progress='Cancelled')
                atomic_json(path, job)

    def step(self):
        with logger.acquisition_lock, self.lock:
            busy = capture_active()
            if self.process:
                path, job = self.jobs[self.running]
                code = self.process.poll()
                if code is not None:
                    job.update(state='complete' if code == 0 else 'error', progress='Results saved' if code == 0 else 'Analysis failed; see worker output', finished=time.time(), exit_code=code)
                    if code:
                        job['error'] = 'Worker exited with code ' + str(code)
                    atomic_json(path, job)
                    self.output.close()
                    self.process = self.running = self.output = None
                elif busy:
                    self.pause_for_capture()
                elif job['state'] == 'paused':
                    os.killpg(self.process.pid, signal.SIGCONT)
                    job.update(state='running', progress='Resumed; computing')
                    atomic_json(path, job)
            if self.process:
                return
            for path, job in self.jobs.values():
                if job['state'] != 'queued':
                    continue
                if busy:
                    if job['progress'] != 'Waiting for recording to stop':
                        job['progress'] = 'Waiting for recording to stop'
                        atomic_json(path, job)
                    continue
                env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', MPLCONFIGDIR=str(self.root / 'matplotlib'))
                try:
                    self.output = (path.parent / 'worker.log').open('ab', buffering=0)
                    self.process = subprocess.Popen([sys.executable, '-u', str(APP_DIR / 'app.py'), '--analysis-worker', str(path)], stdout=self.output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=self.root, env=env, start_new_session=True)
                    self.running = job['id']
                    job.update(state='running', progress='Loading source data / computing', started=time.time())
                except OSError as exc:
                    if self.output:
                        self.output.close()
                    self.output = None
                    job.update(state='error', error=str(exc), progress='Worker could not start')
                atomic_json(path, job)
                break

    def _loop(self):
        while not self.stopped.wait(.5):
            try:
                self.step()
            except Exception:
                logging.exception('Analysis scheduler error')

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        with self.lock:
            if self.running:
                jid = self.running
                self.cancel(jid)
                path, job = self.jobs[jid]
                job.update(state='error', progress='Interrupted by service shutdown', error='Worker stopped by service shutdown; submit again.')
                atomic_json(path, job)


def run_analysis_worker(manifest):
    os.nice(10)
    job = json.loads(Path(manifest).read_text())
    argv = list(job['argv'])
    print('Loading inputs for', job['command'], flush=True)
    if job['command'] in ('laps', 'gg') and job['metadata_rows'] and '--metadata' not in argv:
        metadata = Path(manifest).parent / 'source_metadata.csv'
        columns = sorted(set().union(*(row.keys() for row in job['metadata_rows'])))
        with metadata.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(job['metadata_rows'])
        argv.extend(['--metadata', str(metadata)])
    import analysis
    sys.argv = ['analysis.py', *argv]
    if job["command"] == "minimum-lap":
        analysis.load_model_dependencies()
        from cycler import cycler
        analysis.plt.rcParams["axes.prop_cycle"] = cycler(color=["#841617", "#171717", "#777777", "#aa5555", "#aaaaaa", "#501011"])
    analysis.main()
    # Keep exported standalone HTML offline and visually consistent with the console.
    out = Path(argv[argv.index('--out') + 1])
    for path in out.glob('*.html'):
        document = path.read_text()
        css = (WEB_DIR / 'report.css').read_text()
        document = document.replace('</head>', '<style>' + css + '</style></head>')
        for old, new in {'#58a6ff': '#841617', '#f778ba': '#171717', '#56d364': '#777777', '#e3b341': '#aa5555', '#ff7b72': '#aaaaaa', '#a371f7': '#501011', '#39c5cf': '#444444', '#1464a5': '#841617', '#c03a2b': '#171717', '#12805c': '#777777', '#8a5a00': '#aa5555', '#6f42c1': '#aaaaaa', '#007582': '#501011', '#a23b72': '#444444', '#4f6f00': '#841617', '#48cae4': '#171717', '#f72585': '#777777', '#80ed99': '#aa5555', '#ffd166': '#aaaaaa', '#bdb2ff': '#501011', '#ff9f1c': '#444444', '#06d6a0': '#841617', '#3a86ff': '#171717', '#ef476f': '#777777', '#d1495b': '#aa5555', '#287271': '#aaaaaa', '#2563eb': '#501011', '#dc2626': '#444444', '#059669': '#841617', '#0f766e': '#171717', '#2a9d8f': '#777777', '#7c3aed': '#aa5555', '#7e22ce': '#aaaaaa', '#457b4c': '#501011', '#236b8e': '#444444', '#c06c84': '#841617', '#6d597a': '#171717'}.items():
            document = document.replace(old, new)
        path.write_text(document)
    print('Complete. Results saved.', flush=True)


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict, status: int = 200):
        body = json.dumps(json_safe(payload), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 128 * 1024:
            raise ValueError("JSON body exceeds 128 KiB")
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result

    def download_logs(self):
        if capture_active():
            raise RuntimeError("Stop recording before preparing log archives")
        if not logger.archive_lock.acquire(blocking=False):
            self.send_json({"ok": False, "error": "A log download is already being prepared."}, status=429)
            return
        try:
            temp_directory = tempfile.gettempdir()
            with tempfile.TemporaryFile(dir=temp_directory) as archive:
                count = logger.write_log_archive(logger.SpaceCheckedArchive(archive, temp_directory))
                imported = list(safe_files(self.panel.imports))
                if imported:
                    with zipfile.ZipFile(logger.SpaceCheckedArchive(archive, temp_directory), 'a' if count else 'w', zipfile.ZIP_DEFLATED) as bundle:
                        for path in imported:
                            bundle.write(path, 'imported/' + path.relative_to(self.panel.imports).as_posix())
                            count += 1
                if not count:
                    self.send_json({"ok": False, "error": "No logs are available."}, status=404)
                    return
                size = archive.tell()
                archive.seek(0)
                filename = f"vn300_logs_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                shutil.copyfileobj(archive, self.wfile, length=1024 * 1024)
        except OSError as exc:
            logging.warning("Could not download logs: %s", exc)
            try:
                self.send_json({"ok": False, "error": "Could not create log archive; check available temporary storage."}, status=507)
            except (OSError, ValueError):
                pass
        finally:
            logger.archive_lock.release()

    def legacy_GET(self):
        if self.path == "/api/download_logs":
            self.download_logs()
            return
        path = urlsplit(self.path).path
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            **{route: ("index.html", "text/html; charset=utf-8") for route in ("/track", "/live", "/sensors", "/sessions", "/analysis", "/models", "/setup", "/help")},
            "/report.css": ("report.css", "text/css; charset=utf-8"),
            "/gates.js": ("gates.js", "text/javascript; charset=utf-8"),
            "/sensors.js": ("sensors.js", "text/javascript; charset=utf-8"),
            "/trace.js": ("trace.js", "text/javascript; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        for name in ("team-logo.png", "team-car.jpg", "ou-map.jpg", "riverwind-map.jpg", "map-areas.json"):
            assets["/assets/" + name] = ("assets/" + name, mimetypes.guess_type(name)[0])
        for name in ("airspeed_calibration.example.json", "steering_calibration.example.json", "can_profile.example.json", "motec_can_signal_map_template.csv", "motec_m1_reference.csv"):
            assets["/examples/" + name] = ("../config/examples/" + name, mimetypes.guess_type(name)[0] or "application/json")
        if path in assets:
            filename, content_type = assets[path]
            try:
                body = (WEB_DIR / filename).read_bytes()
            except OSError:
                self.send_error(404, "Dashboard asset unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == '/api/storage':
            self.send_json(self.panel.storage_snapshot())
            return
        if path == '/api/can':
            self.send_json(self.panel.can_snapshot())
            return

        if self.path.startswith("/api/latest"):
            with logger.state_lock:
                age_s = None
                if logger.latest_packet.get("updated_monotonic") is not None:
                    age_s = time.monotonic() - logger.latest_packet["updated_monotonic"]
                live_capture_age_s = None
                if logger.latest_packet.get("live_capture_monotonic") is not None:
                    live_capture_age_s = time.monotonic() - logger.latest_packet["live_capture_monotonic"]
                payload = {k: v for k, v in logger.latest_packet.items() if k not in ("updated_monotonic", "live_capture_monotonic")}
                if isinstance(payload.get("can"), dict):
                    payload["can"] = {k: v for k, v in payload["can"].items() if k != "_last_monotonic"}
                payload["age_s"] = age_s
                payload["live_capture_age_s"] = live_capture_age_s
            payload["setup_streaming"] = logger.setup_stream_requested.is_set()
            payload["logging_requested"] = logger.logging_requested.is_set()
            payload["steering"] = logger.steering_reader.snapshot() if logger.steering_reader else {"enabled": False, "status": "disabled"}
            payload["airspeed"] = payload["steering"].get("airspeed") or {"enabled": False, "status": "disabled"}
            payload["mass_flow"] = logger.mass_flow_reader.snapshot() if logger.mass_flow_reader else {"enabled": False, "status": "disabled"}
            payload["timing"] = logger.public_timing_snapshot()
            payload["run_metadata"] = logger.public_run_metadata_snapshot()
            cpu_temp_c = logger.cpu_temperature_c()
            payload["web_only"] = logger.web_effective_settings.get("web_only", False)
            payload["cpu_temp_c"] = cpu_temp_c
            payload["cpu_temp_f"] = None if cpu_temp_c is None else cpu_temp_c * 9.0 / 5.0 + 32.0
            self.send_json(payload)
            return

        if self.path.startswith("/api/run_metadata"):
            self.send_json(logger.public_run_metadata_snapshot())
            return

        if self.path.startswith("/api/config"):
            self.send_json({"config": logger.public_timing_snapshot()["config"], "timing": logger.public_timing_snapshot()})
            return

        self.send_error(404)

    def legacy_POST(self):
        if self.path == "/api/logging":
            try:
                body = self.read_json_body()
                if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
                    raise ValueError("enabled must be true or false")
                enabled = logger.toggle_logging_request(body["enabled"])
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except RuntimeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=409)
                return
            self.send_json({"ok": True, "logging_requested": enabled})
            return
        if self.path == "/api/steering_calibration":
            try:
                steering = logger.save_steering_calibration(self.read_json_body())
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except RuntimeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=409)
                return
            except OSError as exc:
                logging.error("Could not save steering calibration: %s", exc)
                self.send_json({"ok": False, "error": "Could not save steering calibration file"}, status=500)
                return
            self.send_json({"ok": True, "steering": steering})
            return
        if self.path.startswith("/api/setup_stream"):
            try:
                enabled = self.read_json_body().get("enabled")
            except json.JSONDecodeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if not isinstance(enabled, bool):
                self.send_json({"ok": False, "error": "enabled must be true or false"}, status=400)
                return
            with logger.state_lock:
                logging_active = bool(logger.latest_packet.get("logging"))
            if enabled and (logger.logging_requested.is_set() or logging_active):
                self.send_json(
                    {"ok": False, "error": "Stop the active logging run before starting track setup data."},
                    status=409,
                )
                return
            if enabled:
                logger.setup_stream_requested.set()
                logging.info("Dashboard requested non-recording track setup data")
            else:
                logger.setup_stream_requested.clear()
                logging.info("Dashboard stopped non-recording track setup data")
            self.send_json({"ok": True, "setup_streaming": logger.setup_stream_requested.is_set()})
            return

        if self.path.startswith("/api/config"):
            try:
                logger.configure_timing(self.read_json_body())
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json({"ok": True, "timing": logger.public_timing_snapshot()})
            return

        if self.path.startswith("/api/run_metadata"):
            try:
                logger.update_run_metadata(self.read_json_body())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            payload = logger.public_run_metadata_snapshot()
            payload["ok"] = True
            self.send_json(payload)
            return

        if self.path.startswith("/api/reset_timing"):
            with logger.timing_lock:
                logger.reset_timing_state("waiting for start" if logger.timing_state["configured"] else "not configured")
            self.send_json({"ok": True, "timing": logger.public_timing_snapshot()})
            return

        self.send_error(404)

    @property
    def panel(self):
        return self.server.panel

    def send_file(self, path, download=False, filename=None):
        size = path.stat().st_size
        self.send_response(200)
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_header('Content-Type', content_type + ('; charset=utf-8' if content_type.startswith('text/') else ''))
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type == 'text/html':
            self.send_header('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:")
        if download:
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', filename or path.name)
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.end_headers()
        with path.open('rb') as stream:
            shutil.copyfileobj(stream, self.wfile)

    def zip_session(self, session, files):
        if capture_active():
            raise RuntimeError('Session archive downloads wait until recording has stopped')
        with tempfile.NamedTemporaryFile(suffix='.zip', dir=self.panel.root) as temporary:
            with zipfile.ZipFile(logger.SpaceCheckedArchive(temporary, self.panel.root), 'w', zipfile.ZIP_DEFLATED) as archive:
                paths = [files[f['id']][0] for f in session['files']]
                for name in ('VN300_run_metadata.csv', 'VN300_dashboard_timing_config.csv'):
                    path = Path(session['folder']) / name
                    if path.is_file() and not path.is_symlink():
                        paths.append(path)
                for path in paths:
                    archive.write(path, path.name)
            temporary.flush()
            self.send_file(Path(temporary.name), True, session['name'] + '.zip')

    def import_logs(self):
        if capture_active():
            raise RuntimeError('Stop recording before importing files')
        name = parse_qs(urlsplit(self.path).query).get('name', [''])[0]
        if not name or Path(name).name != name or '\\' in name or Path(name).suffix.lower() not in ALLOWED_INPUTS | {'.zip'}:
            raise ValueError('Import a ZIP, CSV, BIN, JSON, TIR, or DBC file with a plain filename')
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError('Import must be between 1 byte and 256 MiB')
        directory = self.panel.imports / uuid.uuid4().hex
        directory.mkdir()
        try:
            path = directory / name
            with path.open('wb') as stream:
                remaining = length
                while remaining:
                    if capture_active():
                        raise RuntimeError('Import interrupted because recording started; retry between runs')
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('Upload ended early')
                    if shutil.disk_usage(directory).free < len(chunk) + logger.ARCHIVE_FREE_SPACE_RESERVE_BYTES:
                        raise OSError('Insufficient import storage')
                    stream.write(chunk)
                    remaining -= len(chunk)
            if path.suffix.lower() == '.zip':
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > 5000 or sum(m.file_size for m in members) > 1024 * 1024 * 1024:
                        raise ValueError('ZIP exceeds 5000 files or 1 GiB expanded limit')
                    expanded = sum(m.file_size for m in members)
                    if shutil.disk_usage(directory).free < expanded + logger.ARCHIVE_FREE_SPACE_RESERVE_BYTES:
                        raise OSError('Insufficient space for expanded ZIP')
                    for member in members:
                        if capture_active():
                            raise RuntimeError('Import interrupted because recording started; retry between runs')
                        rel = Path(member.filename)
                        if rel.is_absolute() or '..' in rel.parts or '\\' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('Unsafe ZIP member')
                        if member.is_dir():
                            continue
                        if rel.suffix.lower() not in ALLOWED_INPUTS:
                            continue
                        target = directory / 'files' / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, target.open('wb') as destination:
                            shutil.copyfileobj(source, destination)
                path.unlink()
                if not any(safe_files(directory)):
                    raise ValueError('ZIP contains no supported input files')
        except Exception:
            shutil.rmtree(directory)
            raise
        self.send_json({'ok': True, 'message': 'Imported saved files', 'import_id': directory.name}, 201)

    def settings_snapshot(self):
        parser = logger.settings_parser()
        fields = []
        for action in parser._actions:
            if action.dest in ('help', 'config', 'web_only', 'data_dir', 'shutdown_command', 'auto_start'):
                continue
            fields.append({'name': action.dest, 'type': 'bool' if isinstance(action, argparse._StoreTrueAction) else 'number' if action.type in (int, float) else 'text',
                           'integer': action.type is int, 'choices': action.choices, 'default': str(action.default) if isinstance(action.default, Path) else action.default,
                           'help': action.help or ''})
        path = logger.web_settings_path
        saved = logger.migrate_sensor_settings(json.loads(path.read_text())) if path.exists() else {}
        return {'saved': saved, 'effective': logger.web_effective_settings, 'fields': fields, 'settings_file': str(path),
                'runtime_directory': str(self.panel.root), 'restart_required': True}

    def save_settings(self, body):
        if capture_active():
            raise RuntimeError('Stop recording before editing device settings')
        snapshot = self.settings_snapshot()
        allowed = {f['name'] for f in snapshot['fields']}
        if set(body) - allowed:
            raise ValueError('Unknown or read-only settings')
        settings = dict(snapshot['saved'])
        for key, value in body.items():
            if value is None or value == '':
                settings.pop(key, None)
            else:
                settings[key] = value
        path = logger.web_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=path.parent) as temporary:
            json.dump(settings, temporary)
            temporary.flush()
            validated = logger.load_settings(Path(temporary.name), logger.settings_parser())
        parser = logger.settings_parser()
        parser.set_defaults(**validated)
        args = parser.parse_args([])
        for name in ('baud', 'steering_baud', 'arduino_reference_voltage', 'arduino_adc_max_count'):
            value = getattr(args, name)
            if value <= 0 or not math.isfinite(value):
                raise ValueError(name + ' must be positive and finite')
        logger.validate_mass_flow_settings(args.mass_flow_i2c_bus, args.mass_flow_sample_hz)
        if not 1 <= args.dashboard_port <= 65535 or not 0 <= args.can_bitrate <= 10000000:
            raise ValueError('Invalid dashboard port or CAN bitrate')
        if args.port and args.steering_port and args.port == args.steering_port:
            raise ValueError('VN300 and Arduino require separate devices')
        if args.arduino_format in logger.ARDUINO_ANALOG_FORMATS and not (args.steering_port and args.airspeed_calibration):
            raise ValueError('Paired Arduino input requires steering_port and airspeed_calibration')
        if args.airspeed_calibration and args.arduino_format not in logger.ARDUINO_ANALOG_FORMATS:
            raise ValueError('airspeed_calibration requires a0-a1-volts')
        for calibration, cls in ((args.steering_calibration, logger.SteeringCalibration), (args.airspeed_calibration, logger.AirspeedCalibration)):
            if calibration:
                try:
                    cls.from_file(calibration)
                except OSError as exc:
                    raise ValueError(f'Cannot read calibration file {calibration}: {exc}') from exc
        logger.resolve_can_config({'enabled': args.can_enable, 'profile': args.can_profile, 'interface': args.can_interface,
                                   'channel': args.can_channel, 'bitrate': args.can_bitrate, 'signal_map': args.can_signal_map, 'dbc': args.can_dbc})
        atomic_json(path, settings)
        self.send_json({'ok': True, 'message': 'Saved. Restart the service between runs to apply device settings. Command-line flags retain precedence.'})

    def panel_GET(self, path):
        if path == '/api/sessions':
            sessions, files = self.panel.catalog()
            self.send_json({'sessions': sessions, 'inputs': [item for _, item in files.values()]})
        elif path.startswith('/api/files/'):
            sessions, files = self.panel.catalog()
            fid = path.split('/')[-1]
            if any(s['active'] and any(f['id'] == fid for f in s['files']) for s in sessions):
                raise RuntimeError('Wait for the recording to finish before downloading its files')
            if fid not in files:
                self.send_error(404)
            else:
                self.send_file(files[fid][0], True)
        elif path.startswith('/api/sessions/') and path.endswith('/download'):
            sessions, files = self.panel.catalog()
            session = next((s for s in sessions if s['id'] == path.split('/')[3]), None)
            if session is None:
                self.send_error(404)
            else:
                self.zip_session(session, files)
        elif path == '/api/input_path':
            _, files = self.panel.catalog()
            fid = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            if fid not in files:
                raise ValueError('Input unavailable')
            self.send_json({'path': str(files[fid][0])})
        elif path == '/api/analysis':
            self.panel.schema = analysis_schema()
            self.send_json({'commands': self.panel.schema})
        elif path == '/api/jobs':
            self.send_json({'jobs': self.panel.public_jobs(), 'recording': capture_active()})
        elif path.startswith('/api/results/'):
            parts = unquote(path).split('/')
            entry = self.panel.jobs.get(parts[3]) if len(parts) > 4 else None
            relative = '/'.join(parts[4:])
            matches = {p.relative_to(entry[0].parent / 'out').as_posix(): p for p in safe_files(entry[0].parent / 'out')} if entry else {}
            if relative not in matches:
                self.send_error(404)
            else:
                self.send_file(matches[relative], 'download' in parse_qs(urlsplit(self.path).query))
        elif path == '/api/settings':
            self.send_json(self.settings_snapshot())
        elif path == '/api/airspeed_calibration':
            effective = logger.web_effective_settings.get('airspeed_calibration')
            path = Path(effective) if effective else logger.CONFIG_DIR / 'airspeed_calibration.json'
            self.send_json({'calibration': json.loads(path.read_text()) if path.exists() else None, 'path': str(path)})
        elif path == '/api/history':
            sources = {file_id(APP_DIR / 'README.md'): APP_DIR / 'README.md'}
            sources.update({file_id(p): p for p in safe_files(APP_DIR / 'docs') if p.suffix.lower() == '.md'})
            sources.update({file_id(p): p for p in safe_files(APP_DIR / 'archive') if p.suffix.lower() in ('.md', '.html', '.csv', '.png', '.pdf', '.docx') and not any(part in ('build', 'work') for part in p.relative_to(APP_DIR / 'archive').parts)})
            fid = parse_qs(urlsplit(self.path).query).get('id', [None])[0]
            if fid:
                if fid not in sources:
                    self.send_error(404)
                elif sources[fid].suffix.lower() == '.md':
                    self.send_json({'name': sources[fid].name, 'text': sources[fid].read_text()})
                else:
                    self.send_file(sources[fid], 'download' in parse_qs(urlsplit(self.path).query))
            else:
                self.send_json({'documents': [{'id': fid, 'name': p.name, 'path': str(p.relative_to(APP_DIR)), 'historical': 'archive' in p.relative_to(APP_DIR).parts, 'type': p.suffix.lower()} for fid, p in sources.items()]})
        else:
            return False
        return True

    def do_GET(self):
        try:
            if not self.panel_GET(urlsplit(self.path).path):
                self.legacy_GET()
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 400)
        except RuntimeError as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 409)
        except OSError as exc:
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                return
            logging.exception('Panel read failed')
            self.send_json({'ok': False, 'error': str(exc)}, 507)

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            if path == '/api/import':
                self.import_logs()
            elif path == '/api/can_scaling':
                self.send_json(self.panel.save_can_scaling(self.read_json_body()))
            elif path == '/api/jobs':
                self.send_json(self.panel.submit(self.read_json_body()), 202)
            elif path.startswith('/api/jobs/') and path.endswith('/cancel'):
                self.panel.cancel(path.split('/')[3])
                self.send_json({'ok': True})
            elif path == '/api/settings':
                with logger.acquisition_lock:
                    self.save_settings(self.read_json_body())
            elif path == '/api/config':
                body = self.read_json_body()
                logger.configure_timing(body)
                atomic_json(self.panel.root / 'timing.json', logger.public_timing_snapshot()['config'])
                self.send_json({'ok': True, 'timing': logger.public_timing_snapshot()})
            elif path == '/api/run_metadata':
                logger.update_run_metadata(self.read_json_body())
                atomic_json(self.panel.root / 'run_metadata.json', logger.current_run_metadata())
                self.send_json(dict(logger.public_run_metadata_snapshot(), ok=True))
            elif path == '/api/airspeed_calibration':
                with logger.acquisition_lock:
                    if capture_active():
                        raise RuntimeError('Stop recording before changing airspeed calibration')
                    calibration = logger.AirspeedCalibration(**self.read_json_body())
                    effective = logger.web_effective_settings.get('airspeed_calibration')
                    destination = Path(effective) if effective else logger.CONFIG_DIR / 'airspeed_calibration.json'
                    atomic_json(destination, calibration.metadata())
                    reader = logger.steering_reader
                    applied = bool(reader and reader.input_format in logger.ARDUINO_ANALOG_FORMATS)
                    if applied:
                        with reader.lock:
                            reader.airspeed_calibration = calibration
                            reader.state['airspeed'].update(sensor=calibration.sensor, calibration=calibration.metadata(), max_speed_mps=calibration.voltage_to_speed[-1][1])
                    self.send_json({'ok': True, 'applied': applied, 'path': str(destination), 'message': 'Saved and applied' if applied else 'Saved. Set airspeed_calibration to this path and select a0-a1-volts in Device settings.'})
            else:
                self.legacy_POST()
        except (ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 400)
        except RuntimeError as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 409)
        except OSError as exc:
            logging.exception('Panel write failed')
            self.send_json({'ok': False, 'error': str(exc)}, 507)

    def log_message(self, format, *args):
        return


def start_dashboard(host: str, port: int):
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    directory = os.environ.get("SRT_DATA_DIR") or logger.web_effective_settings.get("data_dir") or Path.home() / "vn300_data"
    try:
        server.panel = PanelService(directory)
    except Exception:
        server.server_close()
        raise
    logger.capture_guard = server.panel.pause_for_capture
    atexit.register(server.panel.close)
    original_close = server.server_close
    def close():
        server.panel.close()
        if logger.capture_guard == server.panel.pause_for_capture:
            logger.capture_guard = None
        atexit.unregister(server.panel.close)
        original_close()
    server.server_close = close
    metadata = server.panel.root / "run_metadata.json"
    if metadata.exists():
        logger.update_run_metadata(json.loads(metadata.read_text()))
    timing = server.panel.root / "timing.json"
    if timing.exists():
        config = json.loads(timing.read_text())
        if config.get("mode") and config.get("start_line"):
            payload = {"mode": config["mode"], "min_speed_mph": config["min_speed_mph"], "min_gap_s": config["min_gap_s"]}
            for prefix in ("start", "finish"):
                line = config.get(prefix + "_line")
                if line:
                    payload.update({prefix + "_" + key: value for key, value in line.items()})
            logger.configure_timing(payload)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info("Dashboard listening on http://%s:%d", host, port)
    return server


def main():
    logger.main(web_server=start_dashboard)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--analysis-worker":
        run_analysis_worker(sys.argv[2])
    else:
        main()
