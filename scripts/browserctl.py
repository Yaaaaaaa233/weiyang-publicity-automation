"""Cross-platform host bridge: execute browser tools and retrieve screenshot files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# browser/Dockerfile COPYs scripts/browser.py into the image and compose.yaml mounts
# only the browser volumes, so a repository edit never reaches the container until the
# image is rebuilt. The stale copy rejects subcommands the repository already
# implements; that is a deployment fault and must not be reported as a browser fault.
STALE_MARKER = 'invalid choice'
PROBE_SOURCE = (
    "import hashlib,os,sys;"
    "p='/opt/project/browser.py';"
    "sys.stdout.write(hashlib.sha256(open(p,'rb').read()).hexdigest() if os.path.exists(p) else 'missing')"
)
HEAL_TARGET = 'browser:/opt/project/browser.py'


def classify_failure(message):
    """Name the real failure: a stale image, not a generic bridge error."""
    return 'browser_image_stale' if STALE_MARKER in str(message or '') else 'host bridge error'


def docker_cli():
    docker = shutil.which('docker')
    if not docker:
        candidate = Path.home() / '.docker/bin/docker'
        if not candidate.exists():
            raise RuntimeError('Docker CLI not found. Start Docker Desktop and check PATH.')
        docker = str(candidate)
    env = os.environ.copy()
    # The CLI also launches its credential helper; both must be discoverable.
    env['PATH'] = str(Path(docker).resolve().parent) + os.pathsep + env.get('PATH', '')
    return docker, env


def diagnose(docker, env):
    """Compare the image's /opt/project/browser.py with the repository copy."""
    repository = hashlib.sha256((ROOT / 'scripts/browser.py').read_bytes()).hexdigest()
    probe = subprocess.run([docker, 'compose', 'exec', '-T', 'browser', 'python3', '-c', PROBE_SOURCE],
                           cwd=ROOT, env=env, capture_output=True)
    if probe.returncode:
        raise RuntimeError(probe.stderr.decode('utf-8', errors='replace').strip() or 'Container probe failed.')
    container = probe.stdout.decode('utf-8', errors='replace').strip()
    return {'repository_sha256': repository, 'container_sha256': container, 'match': repository == container}


def heal(docker, env):
    """Copy the repository browser.py over the image copy without recreating anything.

    A rebuild would need registry/apt network and would recreate the container, which
    is exactly the interactive step unattended operation must not depend on. The
    container survives host reboots, so this holds until the next deliberate rebuild.
    """
    copy = subprocess.run([docker, 'compose', 'cp', 'scripts/browser.py', HEAL_TARGET],
                          cwd=ROOT, env=env, capture_output=True)
    if copy.returncode:
        raise RuntimeError('Self-heal failed: ' + copy.stderr.decode('utf-8', errors='replace').strip())


def main():
    docker, env = docker_cli()
    if sys.argv[1:2] == ['doctor']:
        result = diagnose(docker, env)
        if not result['match'] and '--heal' in sys.argv[2:]:
            heal(docker, env)
            result = diagnose(docker, env)
            result['healed'] = True
        return {'ok': True, 'result': result}, 0
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
        message = str(exc)
        result, code = {'ok': False, 'error': classify_failure(message), 'message': message}, 1
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(code)
