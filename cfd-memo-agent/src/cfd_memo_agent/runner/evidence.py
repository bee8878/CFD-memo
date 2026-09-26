"""Read fresh ASCII mesh and field output; do not infer physical accuracy."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

from cfd_memo_agent.validator.foam import Group, parse_foam
from cfd_memo_agent.mesh import planned_cells


def fingerprint(case):
    digest = hashlib.sha256()
    for folder in ('0', 'system', 'constant'):
        for path in sorted((Path(case) / folder).rglob('*')):
            if path.is_file() and 'polyMesh' not in path.parts:
                digest.update(path.relative_to(case).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def foam_list(path):
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', text)
    match = re.match(r'\s*FoamFile\s*\{([^{}]*)\}', text)
    if not match or not re.search(r'\bformat\s+ascii\s*;', match[1]):
        raise ValueError(f'Expected ASCII mesh file: {path}')
    values = parse_foam('payload ' + text[match.end():].strip().rstrip(';') + ';')['payload']
    if (len(values) != 2 or not isinstance(values[0], str) or not values[0].isdigit()
            or not isinstance(values[1], Group) or values[1].opening != '('):
        raise ValueError(f'Invalid counted list: {path}')
    return int(values[0]), values[1].items


def mesh_evidence(case, task):
    mesh = Path(case) / 'constant/polyMesh'
    count, owner = foam_list(mesh / 'owner')
    if count != len(owner) or not owner or not all(isinstance(i, str) and i.isdigit() for i in owner):
        raise ValueError('Invalid mesh owner list')
    neighbour_count, neighbour = foam_list(mesh / 'neighbour')
    if neighbour_count != len(neighbour) or not all(isinstance(i, str) and i.isdigit() for i in neighbour):
        raise ValueError('Invalid mesh neighbour list')
    cells = max(map(int, owner + neighbour)) + 1
    mesh_options = task.get('mesh', {})
    if cells != planned_cells(mesh_options.get('target_cells', 10000), mesh_options):
        raise ValueError('Actual cell count differs from generated block resolution')
    point_count, points = foam_list(mesh / 'points')
    if point_count != len(points) or not points:
        raise ValueError('Invalid mesh points')
    for point in points:
        if not isinstance(point, Group) or len(point.items) != 3 or not all(math.isfinite(float(v)) for v in point.items):
            raise ValueError('Invalid mesh coordinates')
    face_count, faces = foam_list(mesh / 'faces')
    if face_count != count or len(faces) != 2 * face_count:
        raise ValueError('Invalid mesh face count')
    for size, face in zip(faces[::2], faces[1::2]):
        if size != '4' or not isinstance(face, Group) or len(face.items) != 4:
            raise ValueError('Expected quadrilateral mesh faces')
        if not all(isinstance(v, str) and v.isdigit() and int(v) < point_count for v in face.items):
            raise ValueError('Invalid mesh face vertex')
    patch_count, boundaries = foam_list(mesh / 'boundary')
    if patch_count != 6 or len(boundaries) != 12:
        raise ValueError('Expected six generated mesh boundaries')
    patches = {}
    for name, body in zip(boundaries[::2], boundaries[1::2]):
        if not isinstance(name, str) or name in patches or not isinstance(body, dict):
            raise ValueError('Invalid generated mesh boundary')
        faces = body.get('nFaces', [])
        if len(faces) != 1 or not isinstance(faces[0], str) or not faces[0].isdigit() or int(faces[0]) <= 0:
            raise ValueError(f'Empty or invalid generated boundary: {name}')
        patches[name] = int(faces[0])
        expected_type = {'cylinder': 'wall', 'frontAndBack': 'empty'}.get(name, 'patch')
        if body.get('type') != [expected_type]:
            raise ValueError(f'Wrong generated patch type: {name}')
    if set(patches) != set(task['boundary_conditions']):
        raise ValueError('Generated mesh patch names mismatch')
    return {'mesh_verified': True, 'actual_cells': cells, 'boundary_faces': patches,
            'case_path': str(case), 'input_sha256': fingerprint(case),
            'mesh_sha256': mesh_fingerprint(mesh),
            'physical_validated': False}


def mesh_fingerprint(mesh):
    digest = hashlib.sha256()
    for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
        digest.update((mesh / name).read_bytes())
    return digest.hexdigest()


def _finite_tree(value):
    if isinstance(value, Group):
        return _finite_tree(value.items)
    if isinstance(value, dict):
        return all(_finite_tree(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite_tree(v) for v in value)
    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError):
        return True


def field_evidence(case, task, mesh):
    end = task['time_control']['end_time']
    candidates = []
    for path in Path(case).iterdir():
        try:
            value = float(path.name)
        except ValueError:
            continue
        if path.is_dir() and math.isfinite(value) and value > task['time_control']['start_time']:
            if math.isclose(value, end, rel_tol=1e-8, abs_tol=1e-10):
                candidates.append(path)
    if len(candidates) != 1:
        raise ValueError('Missing or ambiguous end-time result directory')
    paths = {}
    for name, components in (('U', 3), ('p', 1)):
        path = candidates[0] / name
        doc = parse_foam(path.read_text(encoding='utf-8'))
        expected_class = 'volVectorField' if name == 'U' else 'volScalarField'
        header = doc.get('FoamFile', {})
        if header.get('class') != [expected_class] or header.get('format') != ['ascii'] or header.get('object') != [name]:
            raise ValueError(f'Invalid field header: {name}')
        expected_dims = ['0', '1', '-1', '0', '0', '0', '0'] if name == 'U' else ['0', '2', '-2', '0', '0', '0', '0']
        dims = doc.get('dimensions', [])
        if len(dims) != 1 or not isinstance(dims[0], Group) or dims[0].items != expected_dims:
            raise ValueError(f'Invalid field dimensions: {name}')
        internal = doc.get('internalField', [])
        if len(internal) == 2 and internal[0] == 'uniform':
            values = [internal[1]]
        elif (len(internal) == 4 and internal[0] == 'nonuniform'
              and internal[1] == ('List<vector>' if name == 'U' else 'List<scalar>')
              and internal[2] == str(mesh['actual_cells']) and isinstance(internal[3], Group)):
            values = internal[3].items
            if len(values) != mesh['actual_cells']:
                raise ValueError(f'Wrong field length: {name}')
        else:
            raise ValueError(f'Unsupported or wrong-sized internal field: {name}')
        for value in values:
            numbers = value.items if isinstance(value, Group) else [value]
            if len(numbers) != components or not all(math.isfinite(float(n)) for n in numbers):
                raise ValueError(f'Invalid field values: {name}')
        if not _finite_tree(doc) or set(doc.get('boundaryField', {})) != set(task['boundary_conditions']):
            raise ValueError(f'Invalid boundary field: {name}')
        paths[name] = str(path)
    if fingerprint(case) != mesh['input_sha256']:
        raise ValueError('Case configuration changed during execution')
    if mesh_fingerprint(Path(case) / 'constant/polyMesh') != mesh['mesh_sha256']:
        raise ValueError('Mesh changed after checkMesh')
    return {'end_time': end, 'fields': paths, 'actual_cells': mesh['actual_cells'],
            'physical_validated': False}
