"""Bounded HTTP staff simulation, exclusively against a private cloned application."""
import argparse
import concurrent.futures
import http.cookies
import json
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest', required=True)
parser.add_argument('--base', default='http://quality_loadtest:8000')
parser.add_argument('--users', type=int, default=320)
parser.add_argument('--concurrency', type=int, default=320)
parser.add_argument('--login-concurrency', type=int, default=8)
parser.add_argument('--output', required=True)
args = parser.parse_args()
if args.base not in ('http://quality_loadtest:8000', 'http://quality_loadproxy:8000') or not 1 <= args.users <= 320 or not 1 <= args.concurrency <= 320:
    parser.error('Only the private loadtest container, up to 320 staff, is allowed')
if not 1 <= args.login_concurrency <= 32:
    parser.error('Login concurrency is limited to 32')
manifest = json.loads(Path(args.manifest).read_text())
assert manifest['database'].startswith('quality_loadtest_')
rows, failures, samples = [], [], []
lock, stop = threading.Lock(), threading.Event()
threading.stack_size(256 * 1024)
active_requests, peak_requests = 0, 0


class Client:
    def __init__(self, user):
        self.user, self.cookies, self.csrf = user, {}, None
        # Private plain-HTTP stand: do not allocate 320 unused TLS trust stores.
        self.opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.HTTPHandler(), urllib.request.HTTPDefaultErrorHandler(),
                        NoRedirect(), urllib.request.HTTPErrorProcessor()):
            self.opener.add_handler(handler)

    def request(self, path, data=None, label=None):
        global active_requests, peak_requests
        if stop.is_set():
            raise RuntimeError('Stopped by resource/health safeguard')
        if isinstance(data, list):
            data = [tuple(item) for item in data]
        headers = {'Host': 'oncomap-abai.kz', 'Cookie': '; '.join(f'{k}={v}' for k, v in self.cookies.items())}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            headers['X-CSRFToken'] = self.csrf or self.cookies.get('csrftoken', '')
        request = urllib.request.Request(args.base + path, headers=headers,
                                         data=urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None)
        started = time.monotonic()
        status = None
        with lock:
            active_requests += 1
            peak_requests = max(peak_requests, active_requests)
        try:
            # No redirects: inspect login/start responses and preserve secure cookies
            # explicitly on this private HTTP network (never a public endpoint).
            try:
                response = self.opener.open(request, timeout=90)
            except urllib.error.HTTPError as error:
                if error.code not in (301, 302, 303):
                    raise
                error.__traceback__ = None
                response = error
            with response:
                status = response.code
                body = response.read().decode('utf-8')
                for value in response.headers.get_all('Set-Cookie', []):
                    cookie = http.cookies.SimpleCookie(value)
                    self.cookies.update({k: item.value for k, item in cookie.items()})
                match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
                self.csrf = match.group(1) if match else self.cookies.get('csrftoken', self.csrf)
                location = response.headers.get('Location', '')
            with lock:
                rows.append({'action': label or path, 'status': status, 'seconds': time.monotonic() - started})
            return status, body, location
        except Exception as error:
            with lock:
                rows.append({'action': label or path, 'status': status or str(error), 'seconds': time.monotonic() - started})
            raise
        finally:
            with lock:
                active_requests -= 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def login(user):
    client = Client(user)
    assert client.request('/quality/login/', label='login_form')[0] == 200
    status, body, location = client.request('/quality/login/',
        [('username', user['username']), ('password', manifest['password'])], 'login_submit')
    assert status == 302 and location == '/quality/', f'Login failed: {status} {location}'
    return client


def phase(client, action):
    user = client.user
    course = user['course']
    try:
        assert client.request('/quality/session/ping/', [], 'activity_ping')[0] == 204
        if action == 'browse':
            for path, label in [('/quality/', 'dashboard'), ('/quality/courses/', 'catalog'),
                                (f'/quality/courses/{course}/', 'course'), ('/quality/documents/', 'documents')]:
                status, _, location = client.request(path, label=label)
                if status == 302:
                    assert location.startswith('/quality/') and location not in ('/quality/login/', '/quality/password/')
                    assert client.request(location, label=label + '_redirect')[0] == 200
                else:
                    assert status == 200, f'{label}: {status}'
        elif action == 'start':
            assert client.request(f"/quality/assignments/{user['assignment']}/acknowledge/", [], 'acknowledge')[0] == 302
            assert client.request(f'/quality/tests/{course}/', label='test_intro')[0] == 200
            assert client.request(f'/quality/tests/{course}/start/', [], 'test_start')[0] == 302
            status, body, _ = client.request(f'/quality/tests/{course}/', label='test_questions')
            assert status == 200 and 'type="radio"' in body, 'No questions displayed'
        elif action == 'submit':
            status, body, _ = client.request(f'/quality/tests/{course}/', user['answers'], 'test_submit')
            assert status == 200 and '100%' in body, f'Result missing: {status}'
            status, _, location = client.request(f'/quality/tests/{course}/start/', [], 'retry_blocked')
            assert status == 302 and location == f'/quality/courses/{course}/'
            assert client.request('/quality/session/ping/', [], 'activity_ping')[0] == 204
    except Exception as error:
        with lock:
            failures.append({'user_index': user['user_id'], 'phase': action, 'error': str(error)})


def monitor():
    bad = 0
    while not stop.wait(1):
        memory = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                  if len(line.split()) >= 2 and line.split()[1].isdigit()}
        record = {'at': time.time(), 'available_mb': round(memory['MemAvailable'] / 1024),
                  'swap_used_mb': round((memory['SwapTotal'] - memory['SwapFree']) / 1024)}
        try:
            started = time.monotonic()
            for path in ('/', '/applications/', '/quality/health/'):
                with urllib.request.urlopen('https://oncomap-abai.kz' + path, timeout=8) as response:
                    assert response.status == 200
                    response.read()
            record['public_health_seconds'] = time.monotonic() - started
            bad = 0
        except Exception as error:
            bad += 1
            record['health_error'] = str(error)
        with lock:
            samples.append(record)
        if record['available_mb'] < 400 or bad >= 2:
            stop.set()
            return


started = time.monotonic()
thread = threading.Thread(target=monitor, daemon=True)
thread.start()
clients = []
with concurrent.futures.ThreadPoolExecutor(max_workers=args.login_concurrency) as pool:
    futures = [pool.submit(login, user) for user in manifest['users'][:args.users]]
    for future in concurrent.futures.as_completed(futures):
        try:
            clients.append(future.result())
        except Exception as error:
            failures.append({'phase': 'login', 'error': str(error)})
print(json.dumps({'logged_in': len(clients), 'elapsed': round(time.monotonic() - started, 2)}), flush=True)
for action in ('browse', 'start', 'submit'):
    if stop.is_set():
        break
    before = time.monotonic()
    barrier = threading.Barrier(len(clients)) if args.concurrency >= len(clients) and clients else None
    def run_phase(client):
        if barrier:
            barrier.wait(timeout=30)
        phase(client, action)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(run_phase, clients))
    print(json.dumps({'phase': action, 'elapsed': round(time.monotonic() - before, 2), 'failures': len(failures)}), flush=True)
    if failures:
        break
stopped_by_safeguard = stop.is_set()
stop.set()
thread.join(timeout=10)
actions = {}
for action in sorted({row['action'] for row in rows}):
    group = [row for row in rows if row['action'] == action]
    times = sorted(row['seconds'] for row in group)
    actions[action] = {'requests': len(group), 'p50': round(statistics.median(times), 3),
                      'p95': round(times[int(.95 * (len(times) - 1))], 3), 'max': round(max(times), 3),
                      'errors': sum(row['status'] not in (200, 204, 302) for row in group)}
result = {'users': args.users, 'logged_in': len(clients), 'concurrency': args.concurrency,
          'base': args.base,
          'login_concurrency': args.login_concurrency, 'elapsed': round(time.monotonic() - started, 2),
          'peak_simultaneous_http_requests': peak_requests,
          'requests': len(rows), 'actions': actions, 'failures': failures,
          'stopped_by_safeguard': stopped_by_safeguard, 'resource_samples': samples}
Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in result.items() if key != 'resource_samples'}), flush=True)
raise SystemExit(1 if failures or stopped_by_safeguard else 0)
