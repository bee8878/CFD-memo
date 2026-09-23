"""Deterministic eight-sector O-grid for the cylinder engineering baseline.

This is a project-authored mesh, not a published physical benchmark.
Quality must be checked by OpenFOAM for each generated case.
"""
from __future__ import annotations

import math


def mesh_resolution(target_cells):
    n = max(8, math.ceil(math.sqrt(target_cells / 8)))
    return n, n, 8 * n * n


def cylinder_mesh(geometry, target_cells=10000):
    radius = geometry['cylinder_diameter'] / 2
    length = geometry['domain_length'] / 2
    height = geometry['domain_height'] / 2
    outer = [(length, 0), (length, height), (0, height), (-length, height),
             (-length, 0), (-length, -height), (0, -height), (length, -height)]
    inner = [(radius * math.cos(i * math.pi / 4), radius * math.sin(i * math.pi / 4))
             for i in range(8)]
    vertices = [(x, y, z) for z in (-0.05, 0.05) for x, y in inner + outer]
    radial, angular, _ = mesh_resolution(target_cells)
    fmt = lambda values: '(' + ' '.join(format(v, '.12g') for v in values) + ')'
    lines = ['FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }',
             'convertToMeters 1;', 'vertices', '(']
    lines += ['    ' + fmt(v) for v in vertices]
    lines += [');', 'blocks', '(']
    patches = {name: [] for name in ('inlet', 'outlet', 'top', 'bottom', 'frontAndBack', 'cylinder')}
    for i in range(8):
        j = (i + 1) % 8
        a, b, c, d = i, i + 8, j + 8, j
        lines.append(f'    hex {fmt([a,b,c,d,a+16,b+16,c+16,d+16])} '
                     f'({radial} {angular} 1) simpleGrading (10 1 1)')
        patches['cylinder'].append([d, a, a+16, d+16])
        side = ('outlet', 'top', 'top', 'inlet', 'inlet', 'bottom', 'bottom', 'outlet')[i]
        patches[side].append([b, c, c+16, b+16])
        patches['frontAndBack'] += [[a,d,c,b], [a+16,b+16,c+16,d+16]]
    lines += [');', 'edges', '(']
    for offset, z in ((0, -0.05), (16, 0.05)):
        for i in range(8):
            angle = (i + 0.5) * math.pi / 4
            mid = [radius * math.cos(angle), radius * math.sin(angle), z]
            lines.append(f'    arc {offset+i} {offset+(i+1)%8} {fmt(mid)}')
    lines += [');', 'boundary', '(']
    for name, faces in patches.items():
        kind = {'cylinder': 'wall', 'frontAndBack': 'empty'}.get(name, 'patch')
        lines += [f'    {name}', '    {', f'        type {kind};', '        faces', '        (']
        lines += ['            ' + fmt(face) for face in faces]
        lines += ['        );', '    }']
    lines += [');', 'mergePatchPairs ();', '']
    return '\n'.join(lines)
