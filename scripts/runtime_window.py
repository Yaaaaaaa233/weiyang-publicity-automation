"""Own the dedicated browser container's lifecycle across a run window.

Docker Desktop is closed while the machine is used for other work, so an unattended run
has to bring the engine and the container up itself and stop the container when the
window closes. Login state lives in the `browser-data` named volume, which Chromium uses
as its profile: stopping the container, or quitting Docker Desktop, does not touch named
volumes, so no new scan-code login is required. Only `docker compose down -v` deletes
them, and this module refuses to run that: every command passes a guard that rejects
`down`, `rm`, `prune`, `-v` and `--volumes`.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

SERVICE = 'browser'
VOLUMES = ('weiyang-publicity_browser-data', 'weiyang-publicity_browser-artifacts')
CONFIG = ROOT / 'local/runtime-window.json'
ENGINE_WAIT_SECONDS = 240
CONTAINER_WAIT_SECONDS = 180

# The named volumes hold the logged-in Chromium profile. A stop/start cycle is safe;
# anything that could remove or prune them is not reachable from this module.
FORBIDDEN = ('down', 'rm', 'prune', '-v', '--volumes')


class RuntimeUnavailable(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def config():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def guard(command):
    """Refuse any command that could delete the named volumes holding the login state."""
    parts = [str(part) for part in command]
    for part in parts:
        if part in FORBIDDEN:
            raise RuntimeUnavailable('volume_guard',
                                     'Refused a command that could delete the browser volumes: ' + ' '.join(parts))
    return parts


def run(command, seconds=180):
    parts = guard(command)
    try:
        return subprocess.run(parts, cwd=ROOT, capture_output=True, text=True,
                              encoding='utf-8', timeout=seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeUnavailable('command_timeout', 'Timed out: ' + ' '.join(parts)) from exc
    except OSError as exc:
        raise RuntimeUnavailable('command_failed', 'Could not run: ' + ' '.join(parts) + ': ' + str(exc)) from exc


def docker_cli():
    docker = shutil.which('docker')
    if not docker:
        candidate = Path.home() / '.docker/bin/docker'
        if not candidate.exists():
            raise RuntimeUnavailable('docker_cli_missing',
                                     'Docker CLI not found. Start Docker Desktop and check PATH.')
        docker = str(candidate)
    env = os.environ.copy()
    env['PATH'] = str(Path(docker).resolve().parent) + os.pathsep + env.get('PATH', '')
    return docker, env


def engine_reachable(docker, env):
    try:
        return run([docker, 'info', '--format', '{{.ServerVersion}}'], seconds=60).returncode == 0
    except RuntimeUnavailable:
        return False


def container_identity(docker, env):
    result = run([docker, 'compose', 'ps', '-a', '-q', SERVICE], seconds=60)
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines and lines[0].strip() else None


def container_state(docker, env):
    identity = container_identity(docker, env)
    if not identity:
        return 'absent'
    result = run([docker, 'inspect', '--format', '{{.State.Status}}', identity], seconds=60)
    if result.returncode:
        return 'unknown'
    return result.stdout.strip() or 'unknown'


def volumes_present(docker, env):
    present = []
    for name in VOLUMES:
        result = run([docker, 'volume', 'ls', '-q', '--filter', 'name=' + name], seconds=60)
        if result.returncode == 0 and name in result.stdout.split():
            present.append(name)
    return present


def start_engine(docker, env):
    """Launch the desktop engine when it is closed; the run window owns this step."""
    settings = config()
    configured = settings.get('docker_desktop_command')
    if configured:
        run(configured if isinstance(configured, list) else [configured], seconds=60)
        return 'configured-command'
    if sys.platform == 'darwin':
        run(['open', '-a', 'Docker'], seconds=60)
        return 'macos-open'
    if os.name == 'nt':
        path = settings.get('docker_desktop_path') or r'C:\Program Files\Docker\Docker\Docker Desktop.exe'
        if "'" in path:
            raise RuntimeUnavailable('engine_autostart_unsupported',
                                     'Configured Docker Desktop path must not contain a single quote.')
        run(['powershell', '-NoProfile', '-Command',
             "Start-Process -FilePath '" + path + "'"], seconds=60)
        return 'windows-start-process'
    raise RuntimeUnavailable('engine_autostart_unsupported',
                             'No configured way to start the Docker engine on this host.')


def stop_engine(docker, env):
    """Quitting Docker Desktop is opt-in; only `compose stop` is safe by default.

    `docker compose stop` keeps the container and the named volumes, but Docker Desktop's
    VM keeps its memory, so an operator who needs the memory back must configure an exact,
    already-validated quit command. An unvalidated kill is not shipped as a default.
    """
    settings = config()
    configured = settings.get('docker_desktop_stop_command')
    if not configured:
        return 'engine:left-running'
    run(configured if isinstance(configured, list) else [configured], seconds=120)
    return 'engine:stop-command-sent'


def wait_engine(docker, env, seconds=ENGINE_WAIT_SECONDS):
    deadline = time.monotonic() + seconds
    while True:
        if engine_reachable(docker, env):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


def status(docker, env):
    reachable = engine_reachable(docker, env)
    return {'engine': reachable,
            'container': container_state(docker, env) if reachable else None,
            'volumes': volumes_present(docker, env) if reachable else []}


def up(docker, env, engine=True):
    steps = []
    if engine_reachable(docker, env):
        steps.append('engine:already-running')
    else:
        if not engine:
            raise RuntimeUnavailable('engine_unavailable',
                                     'Docker engine is not reachable and engine autostart is disabled.')
        steps.append('engine:' + start_engine(docker, env))
        if not wait_engine(docker, env):
            raise RuntimeUnavailable('engine_unavailable',
                                     'Docker engine did not become reachable within %ds.' % ENGINE_WAIT_SECONDS)
        steps.append('engine:ready')
    state = container_state(docker, env)
    if state == 'running':
        steps.append('container:already-running')
    else:
        result = run([docker, 'compose', 'up', '-d', '--wait', SERVICE], seconds=CONTAINER_WAIT_SECONDS)
        if result.returncode:
            detail = (result.stderr or result.stdout or '').strip().splitlines()
            raise RuntimeUnavailable('container_unavailable',
                                     (detail[-1] if detail else 'docker compose up failed.'))
        steps.append('container:started')
    state = container_state(docker, env)
    if state != 'running':
        raise RuntimeUnavailable('container_unavailable',
                                 'Container did not reach a running state: ' + str(state))
    return {'steps': steps, 'engine': True, 'container': state, 'volumes': volumes_present(docker, env)}


def down(docker, env):
    if os.environ.get('WEIYANG_BROWSER_KEEP_RUNNING') == '1':
        return {'steps': ['kept-running'], 'container': None, 'engine': True, 'volumes': []}
    if not engine_reachable(docker, env):
        return {'steps': ['engine-already-stopped'], 'container': None, 'engine': False, 'volumes': []}
    volumes = volumes_present(docker, env)
    state = container_state(docker, env)
    steps = []
    if state == 'running':
        result = run([docker, 'compose', 'stop', SERVICE], seconds=CONTAINER_WAIT_SECONDS)
        if result.returncode:
            detail = (result.stderr or result.stdout or '').strip().splitlines()
            raise RuntimeUnavailable('container_stop_failed',
                                     (detail[-1] if detail else 'docker compose stop failed.'))
        steps.append('container:stopped')
    else:
        steps.append('container:' + str(state))
    steps.append(stop_engine(docker, env))
    return {'steps': steps, 'container': state, 'engine': True, 'volumes': volumes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'up', 'down'))
    parser.add_argument('--no-engine', action='store_true',
                        help='Never launch Docker Desktop; fail if the engine is closed.')
    args = parser.parse_args()
    docker, env = docker_cli()
    if args.command == 'status':
        result = status(docker, env)
    elif args.command == 'up':
        result = up(docker, env, engine=not args.no_engine)
    else:
        result = down(docker, env)
    return {'ok': True, 'result': result}, 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result, code = main()
    except RuntimeUnavailable as exc:
        result, code = {'ok': False, 'error': exc.code, 'message': str(exc)}, 1
    except (OSError, ValueError, RuntimeError) as exc:
        result, code = {'ok': False, 'error': 'runtime_window_error', 'message': str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(code)
