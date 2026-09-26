"""Deterministic eight-sector O-grid for the cylinder engineering baseline.

This is a project-authored mesh, not a published physical benchmark.
Quality must be checked by OpenFOAM for each generated case.
"""
from __future__ import annotations

import math

SPAN = 0.1


def mesh_resolution(target_cells):
    n = max(8, math.ceil(math.sqrt(target_cells / 8)))
    return n, n, 8 * n * n


def planned_cells(target_cells=10000, mesh_options=None):
    options = mesh_options or {}
    if "near_field_radius" not in options:
        return mesh_resolution(target_cells)[2]
    angular = options["angular_cells"]
    wake = options.get("wake_angular_cells", angular)
    angular_total = 6 * angular + 2 * wake
    return angular_total * (options["near_radial_cells"] + options["far_radial_cells"])


def cylinder_mesh(geometry, target_cells=10000, radial_grading=10, mesh_options=None):
    options = mesh_options or {}
    radius = geometry['cylinder_diameter'] / 2
    upstream = geometry.get('upstream_length', geometry['domain_length'] / 2)
    downstream = geometry.get('downstream_length', geometry['domain_length'] / 2)
    height = geometry['domain_height'] / 2
    outer = [(downstream, 0), (downstream, height), (0, height), (-upstream, height),
             (-upstream, 0), (-upstream, -height), (0, -height), (downstream, -height)]
    inner = [(radius * math.cos(i * math.pi / 4), radius * math.sin(i * math.pi / 4))
             for i in range(8)]
    local = "near_field_radius" in options
    if local:
        near_radius = options["near_field_radius"]
        if not radius < near_radius < min(upstream, downstream, height):
            raise ValueError("near_field_radius must lie between the cylinder and outer boundary")
        middle = [(near_radius * math.cos(i * math.pi / 4),
                   near_radius * math.sin(i * math.pi / 4)) for i in range(8)]
        rings = [inner, middle, outer]
        radial_counts = [options["near_radial_cells"], options["far_radial_cells"]]
        radial_gradings = [options.get("near_radial_grading", 20),
                           options.get("far_radial_grading", 10)]
        angular_counts = [options.get("wake_angular_cells", options["angular_cells"])
                          if i in (0, 7) else options["angular_cells"] for i in range(8)]
    else:
        radial, angular, _ = mesh_resolution(target_cells)
        rings = [inner, outer]
        radial_counts = [radial]
        radial_gradings = [radial_grading]
        angular_counts = [angular] * 8
    layer_size = 8 * len(rings)
    vertices = [(x, y, z) for z in (-SPAN / 2, SPAN / 2)
                for ring in rings for x, y in ring]
    fmt = lambda values: '(' + ' '.join(format(v, '.12g') for v in values) + ')'
    lines = ['FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }',
             'convertToMeters 1;', 'vertices', '(']
    lines += ['    ' + fmt(v) for v in vertices]
    lines += [');', 'blocks', '(']
    patches = {name: [] for name in ('inlet', 'outlet', 'top', 'bottom', 'frontAndBack', 'cylinder')}
    for ring_index, (radial, grading) in enumerate(zip(radial_counts, radial_gradings)):
        for i in range(8):
            j = (i + 1) % 8
            a, b = ring_index * 8 + i, (ring_index + 1) * 8 + i
            c, d = (ring_index + 1) * 8 + j, ring_index * 8 + j
            lines.append(f'    hex {fmt([a,b,c,d,a+layer_size,b+layer_size,c+layer_size,d+layer_size])} '
                         f'({radial} {angular_counts[i]} 1) simpleGrading ({grading:.12g} 1 1)')
            if ring_index == 0:
                patches['cylinder'].append([d, a, a+layer_size, d+layer_size])
            if ring_index == len(radial_counts) - 1:
                side = ('outlet', 'top', 'top', 'inlet', 'inlet', 'bottom', 'bottom', 'outlet')[i]
                patches[side].append([b, c, c+layer_size, b+layer_size])
            patches['frontAndBack'] += [
                [a, d, c, b],
                [a+layer_size, b+layer_size, c+layer_size, d+layer_size],
            ]
    lines += [');', 'edges', '(']
    circular_radii = [radius] + ([options["near_field_radius"]] if local else [])
    for z_offset, z in ((0, -SPAN / 2), (layer_size, SPAN / 2)):
        for ring_index, ring_radius in enumerate(circular_radii):
            offset = z_offset + ring_index * 8
            for i in range(8):
                angle = (i + 0.5) * math.pi / 4
                mid = [ring_radius * math.cos(angle), ring_radius * math.sin(angle), z]
                lines.append(f'    arc {offset+i} {offset+(i+1)%8} {fmt(mid)}')
    lines += [');', 'boundary', '(']
    for name, faces in patches.items():
        kind = {'cylinder': 'wall', 'frontAndBack': 'empty'}.get(name, 'patch')
        lines += [f'    {name}', '    {', f'        type {kind};', '        faces', '        (']
        lines += ['            ' + fmt(face) for face in faces]
        lines += ['        );', '    }']
    lines += [');', 'mergePatchPairs ();', '']
    return '\n'.join(lines)
