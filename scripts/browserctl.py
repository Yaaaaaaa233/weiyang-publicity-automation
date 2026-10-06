"""Cross-platform host bridge: execute browser tools and retrieve screenshot files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    docker = shutil.which('docker')
    if not docker:
        candidate = Path.home() / '.docker/bin/docker'
        if not candidate.exists():
            raise RuntimeError('Docker CLI not found. Start Docker Desktop and check PATH.')
        docker = str(candidate)
    env = os.environ.copy()
    # The CLI also launches its credential helper; both must be discoverable.
    env['PATH'] = str(Path(docker).resolve().parent) + os.pathsep + env.get('PATH', '')
    command = [docker, 'compose', 'exec', '-T', 'browser', 'python3', '/opt/project/browser.py']
    lease = env.get('WEIYANG_BROWSER_LEASE')
    arguments = (['--lease-token', lease] if lease else []) + sys.argv[1:]
    process = subprocess.run(command + arguments, cwd=ROOT, env=env, capture_output=True)
    if not process.stdout:
        raise RuntimeError(process.stderr.decode('utf-8', errors='replace').strip() or 'Browser command failed.')
    data = json.loads(process.stdout.decode('utf-8'))
    if process.returncode:
        return data, process.returncode
    result = data.get('result', {})
    for kind, extension in (('screenshot', '.png'), ('artifact', '.json')):
        artifact = result.get(kind) if isinstance(result, dict) else None
        if not artifact:
            continue
        name = Path(artifact).name
        if artifact != 'artifacts/' + name or not name.endswith(extension):
            raise RuntimeError('Invalid artifact path returned by browser tool.')
        destination = ROOT / 'artifacts' / name
        destination.parent.mkdir(exist_ok=True)
        copy = subprocess.run([docker, 'compose', 'cp', 'browser:/opt/artifacts/' + name,
                               str(destination)], cwd=ROOT, env=env, capture_output=True)
        if copy.returncode:
            raise RuntimeError('Artifact retrieval failed: ' + copy.stderr.decode('utf-8', errors='replace'))
        result['local_' + kind] = str(destination)
    return data, 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result, code = main()
    except (OSError, ValueError, RuntimeError) as exc:
        result, code = {'ok': False, 'error': 'host bridge error', 'message': str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(code)
