"""Explicit Foundation v10 environment and Windows-to-WSL process adapter."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

DISTRO = 'Ubuntu-22.04'
BASHRC = '/opt/openfoam10/etc/bashrc'
STAGES = ('blockMesh', 'checkMesh', 'icoFoam')
ALLOWED_STAGES = frozenset((*STAGES, 'simpleFoam'))


def _text(data):
    return data.decode('utf-16-le' if b'\x00' in data else 'utf-8', errors='replace').strip()


def _capture(command):
    result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    if result.returncode:
        raise OSError(_text(result.stderr or result.stdout) or 'OpenFOAM environment probe failed')
    return _text(result.stdout)


def discover(stages=STAGES):
    stages = tuple(stages)
    if not stages or len(set(stages)) != len(stages) or not set(stages) <= ALLOWED_STAGES:
        raise ValueError('Unsupported OpenFOAM command plan')
    native = {stage: shutil.which(stage) for stage in stages}
    if all(native.values()):
        version = os.environ.get('WM_PROJECT_VERSION')
        if version != '10':
            raise OSError(f'Expected Foundation OpenFOAM 10; WM_PROJECT_VERSION={version!r}')
        return {'backend': 'native', 'version': version, 'executables': native}
    if os.name != 'nt' or not shutil.which('wsl'):
        raise OSError('Foundation OpenFOAM 10 commands unavailable')
    prefix = [shutil.which('wsl'), '--distribution', DISTRO, '--exec']
    output = _capture(prefix + ['bash', '-c',
        'source "$1" >/dev/null || exit; shift; '
        'test "$WM_PROJECT_VERSION" = 10 || exit 1; '
        'for command in "$@"; do command -v -- "$command" || exit; done; '
        'printf "VERSION=%s\\n" "$WM_PROJECT_VERSION"',
        'cfd-memo-probe', BASHRC, *stages])
    lines = output.splitlines()
    if (len(lines) != len(stages) + 1 or lines[-1] != 'VERSION=10'
            or not all(s.startswith('/') for s in lines[:-1])):
        raise OSError('Unexpected OpenFOAM 10 environment probe response')
    return {'backend': 'wsl', 'version': '10', 'distribution': DISTRO,
            'prefix': prefix, 'executables': dict(zip(stages, lines[:-1]))}


def wsl_path(environment, path):
    # Preserve the caller's lexical path; OpenFOAM rejects case paths containing spaces.
    return _capture(environment['prefix'] + ['wslpath', '-a', '-u', str(Path(path).absolute())])


def execute_wsl(environment, stage, case, log, timeout, execute):
    """A Linux watchdog and an invocation-specific process group bound cancellation."""
    linux_case = wsl_path(environment, case)
    pid_file = '/tmp/cfd-memo-' + uuid4().hex + '.pid'
    prefix = environment['prefix']
    script = (
        'source "$1" >/dev/null || exit; shift; '
        'exec setsid --wait bash -c '\
        "'pidfile=$1; shift; "
        'printf "%s %s\\n" "$$" "$(cut -d " " -f 22 /proc/$$/stat)" > "$pidfile"; '
        'exec timeout --signal=TERM --kill-after=5 "$@"' + "' cfd-memo-group \"$@\""
    )
    command = prefix + ['bash', '-c', script, 'cfd-memo', BASHRC, pid_file,
                        str(timeout), environment['executables'][stage], '-case', linux_case]
    cleanup = (
        'if read -r pid born < "$1" 2>/dev/null; then '
        'case "$pid:$born" in *[!0-9:]*|:*) exit 1;; esac; '
        'if test -r "/proc/$pid/stat" && '
        'test "$(cut -d " " -f 22 /proc/$pid/stat)" = "$born"; then '
        'kill -KILL -- "-$pid" 2>/dev/null; fi; fi; rm -f -- "$1"'
    )
    try:
        # Do not launch WSL with the Windows case as cwd: a caller may still pass
        # a spaced path even though the explicit -case path is safe.
        launch_dir = Path(prefix[0]).resolve().parent
        outcome = execute(command, launch_dir, log, timeout + 10)
        if outcome['returncode'] in (124, 137):
            outcome['timed_out'] = True
        return command, outcome
    finally:
        subprocess.run(prefix + ['bash', '-c', cleanup, 'cfd-memo-cleanup', pid_file],
                       capture_output=True, timeout=15, check=False)
