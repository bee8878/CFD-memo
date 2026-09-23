"""C6 contract tests. Synthetic output is not an OpenFOAM acceptance run."""
import math
from pathlib import Path

import pytest

from cfd_memo_agent.mesh import cylinder_mesh, mesh_resolution
from cfd_memo_agent.planner import plan_task
from cfd_memo_agent.generator import generate_case
from cfd_memo_agent.validator import validate_case
from cfd_memo_agent.validator.foam import parse_foam
from cfd_memo_agent.runner import backend, execution
from cfd_memo_agent.runner.evidence import mesh_evidence, field_evidence


@pytest.fixture
def case_data(tmp_path):
    task = plan_task('cylinder flow')
    task['mesh']['target_cells'] = 512
    generated = generate_case(task, runs_dir=tmp_path)
    return task, Path(generated['case_path'])


def write_mesh(case):
    # Deliberately small counted-list parser fixture; topology is not simulated.
    folder = case / 'constant/polyMesh'
    folder.mkdir(parents=True)
    def counted(name, count, content):
        (folder / name).write_text(
            f'FoamFile {{ format ascii; object {name}; }}\n{count}\n({content})\n', encoding='utf-8')
    counted('owner', 6, '511 511 511 511 511 511')
    counted('neighbour', 0, '')
    counted('points', 4, '(0 0 0) (1 0 0) (1 1 0) (0 1 0)')
    counted('faces', 6, '4(0 1 2 3) ' * 6)
    types = {'inlet': 'patch', 'outlet': 'patch', 'top': 'patch',
             'bottom': 'patch', 'cylinder': 'wall', 'frontAndBack': 'empty'}
    counted('boundary', 6, '\n'.join(f'{n} {{ type {t}; nFaces 1; startFace {i}; }}'
                                    for i, (n, t) in enumerate(types.items())))


def write_fields(case):
    folder = case / '10'
    folder.mkdir(exist_ok=True)
    for name, kind, dims, values in (
        ('U', 'volVectorField', '0 1 -1 0 0 0 0', 'List<vector> 512 (' + '(1 0 0) ' * 512 + ')'),
        ('p', 'volScalarField', '0 2 -2 0 0 0 0', 'List<scalar> 512 (' + '0 ' * 512 + ')'),
    ):
        (folder / name).write_text(
            f'FoamFile {{ format ascii; class {kind}; object {name}; }}\n'
            f'dimensions [{dims}]; internalField nonuniform {values};\n'
            'boundaryField {' + ' '.join(f'{n} {{ type zeroGradient; }}' for n in
            ('inlet', 'outlet', 'top', 'bottom', 'cylinder', 'frontAndBack')) + '}', encoding='utf-8')


def test_ogrid_has_round_surface_eight_positive_blocks_and_two_dimensional_layers(case_data):
    task, case = case_data
    doc = parse_foam((case / 'system/blockMeshDict').read_text())
    vertices = [[float(x) for x in v.items] for v in doc['vertices'][0].items]
    assert len(vertices) == 32
    for offset in (0, 16):
        for x, y, z in vertices[offset:offset+8]:
            assert math.isclose(math.hypot(x, y), 0.5, rel_tol=1e-10)
    blocks = doc['blocks'][0].items
    assert len(blocks) == 40 and len(doc['edges'][0].items) == 64
    for i in range(0, 40, 5):
        assert blocks[i+2].items[2] == '1'
        ids = list(map(int, blocks[i+1].items[:4]))
        points = [vertices[j] for j in ids]
        area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, points[1:]+points[:1]))
        assert area > 0
    assert validate_case(task, case)['config_valid']
    assert mesh_resolution(10000)[2] == 10368


def test_template_matches_default_mesh():
    template = Path(__file__).resolve().parents[1] / 'cases/templates/cylinder-2d/system/blockMeshDict'
    task = plan_task('cylinder flow')
    assert parse_foam(template.read_text()) == parse_foam(cylinder_mesh(task['geometry']))


def test_output_evidence_uses_actual_files(case_data):
    task, case = case_data
    write_mesh(case)
    mesh = mesh_evidence(case, task)
    assert mesh['actual_cells'] == 512 and mesh['boundary_faces']['cylinder'] == 1
    write_fields(case)
    result = field_evidence(case, task, mesh)
    assert result['end_time'] == 10 and result['physical_validated'] is False
    assert Path(result['fields']['U']).is_file()


@pytest.mark.parametrize('damage', ['missing', 'nan', 'count', 'time', 'config', 'mesh'])
def test_output_damage_rejected(case_data, damage):
    task, case = case_data
    write_mesh(case)
    mesh = mesh_evidence(case, task)
    write_fields(case)
    field = case / '10/U'
    if damage == 'missing':
        field.unlink()
    elif damage == 'nan':
        field.write_text(field.read_text().replace('(1 0 0)', '(nan 0 0)', 1))
    elif damage == 'count':
        field.write_text(field.read_text().replace('512', '511'))
    elif damage == 'time':
        (case / '10').rename(case / '9')
    elif damage == 'config':
        (case / 'system/controlDict').write_text('changed')
    else:
        with (case / 'constant/polyMesh/owner').open('a') as f:
            f.write('// changed')
    with pytest.raises((ValueError, OSError)):
        field_evidence(case, task, mesh)


def test_empty_actual_cylinder_rejected(case_data):
    task, case = case_data
    write_mesh(case)
    path = case / 'constant/polyMesh/boundary'
    path.write_text(path.read_text().replace('cylinder { type wall; nFaces 1;', 'cylinder { type wall; nFaces 0;'))
    with pytest.raises(ValueError):
        mesh_evidence(case, task)


@pytest.mark.parametrize('mesh_ok,fields_ok,expected_steps', [(False, False, 2), (True, False, 3), (True, True, 3)])
def test_real_pipeline_requires_fresh_evidence(case_data, monkeypatch, mesh_ok, fields_ok, expected_steps):
    task, case = case_data
    # Pre-existing outputs must not be copied into the attempt.
    write_mesh(case)
    write_fields(case)
    monkeypatch.setattr(execution, 'discover', lambda: {
        'backend': 'native', 'version': '10', 'executables': {s: s for s in execution.COMMANDS}})
    calls = []
    def execute(command, output, log, timeout):
        stage = command[0]
        calls.append(stage)
        if stage == 'blockMesh':
            assert not (output / '10').exists()
            assert not (output / 'constant/polyMesh').exists()
            if mesh_ok:
                write_mesh(output)
        if stage == 'icoFoam' and fields_ok:
            write_fields(output)
        log.write_text('Mesh OK.\nEnd\n' if stage == 'checkMesh' else 'End\n')
        return {'returncode': 0, 'timed_out': False, 'duration_seconds': 0.01}
    monkeypatch.setattr(execution, '_execute', execute)
    result = execution.run_case(case.parent, mode='real')
    assert len(calls) == expected_steps
    assert result['status'] == ('completed' if fields_ok else 'failed')
    assert not result['physical_validated']
    assert (case / '10/U').is_file()


def test_wsl_path_is_argument_not_shell_source(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(backend, '_capture', lambda command: calls.append(command) or '/mnt/d/CFD MEMO/case')
    path = tmp_path / 'space & quote case'
    assert backend.wsl_path({'prefix': ['wsl', '--exec']}, path) == '/mnt/d/CFD MEMO/case'
    assert calls[0][-1] == str(path.absolute()) and calls[0][2] == 'wslpath'


@pytest.mark.parametrize('interrupt,returncode', [(False, 0), (False, 124), (True, 0)])
def test_wsl_watchdog_and_group_cleanup(tmp_path, monkeypatch, interrupt, returncode):
    monkeypatch.setattr(backend, 'wsl_path', lambda *a: '/mnt/d/CFD MEMO/case')
    cleanups = []
    monkeypatch.setattr(backend.subprocess, 'run', lambda command, **kw: cleanups.append(command))
    env = {'prefix': ['C:/Windows/System32/wsl.exe', '-d', 'Ubuntu-22.04', '--exec'],
           'executables': {'icoFoam': '/opt/icoFoam'}}
    def execute(command, case, log, timeout):
        assert case == Path('C:/Windows/System32')
        assert command[-1] == '/mnt/d/CFD MEMO/case'
        assert command[-2] == '-case' and timeout == 12
        assert 'setsid --wait' in command[6] and 'timeout' in command[6]
        if interrupt:
            raise KeyboardInterrupt
        return {'returncode': returncode, 'timed_out': False}
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            backend.execute_wsl(env, 'icoFoam', tmp_path, tmp_path/'log', 2, execute)
    else:
        _, result = backend.execute_wsl(env, 'icoFoam', tmp_path, tmp_path/'log', 2, execute)
        assert result['timed_out'] == (returncode == 124)
    assert len(cleanups) == 1 and cleanups[0][-1].startswith('/tmp/cfd-memo-')
    assert '--shutdown' not in cleanups[0]


def test_native_version_is_fixed(monkeypatch):
    monkeypatch.setattr(backend.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setenv('WM_PROJECT_VERSION', '11')
    with pytest.raises(OSError, match='OpenFOAM 10'):
        backend.discover()
