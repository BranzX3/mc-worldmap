"""Source topology and bounded terrain routing for the new water model.

Coordinates are world block coordinates in ``[x, z]`` order. The graph records
geometric connectivity, not flow direction. Its IDs depend on coordinates and
attributes, never the order in which source lines are supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
from itertools import permutations
import math

import numpy as np


@dataclass(frozen=True)
class Network:
    nodes: np.ndarray
    edges: np.ndarray
    kinds: np.ndarray
    widths: np.ndarray


def _clip_segment(a, b, limits):
    """Liang--Barsky clipping, evaluated in a canonical endpoint order."""
    a, b = sorted((tuple(a), tuple(b)))
    if a == b:
        return None
    xlo, xhi, zlo, zhi = limits
    dx, dz = b[0] - a[0], b[1] - a[1]
    lower, upper = 0.0, 1.0
    for p, q in ((-dx, a[0] - xlo), (dx, xhi - a[0]),
                 (-dz, a[1] - zlo), (dz, zhi - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        parameter = q / p
        if p < 0:
            lower = max(lower, parameter)
        else:
            upper = min(upper, parameter)
        if lower >= upper:
            return None

    def point(parameter):
        if parameter == 0.0:
            return a
        if parameter == 1.0:
            return b
        # Clamp only the numerical result of geometric clipping. Off-map source
        # vertices themselves are never projected onto a map boundary.
        return (min(xhi, max(xlo, a[0] + parameter * dx)),
                min(zhi, max(zlo, a[1] + parameter * dz)))

    return point(lower), point(upper)


def build_network(points_x, points_z, offsets, kind, width_m, *, bounds):
    """Build an undirected, deterministically indexed graph of source segments.

    Bounds use the usual half-open scene convention: valid cell centers extend
    from ``x0`` to ``x1 - 1`` and ``z0`` to ``z1 - 1``. Only exact coincident
    source vertices (or coincident clipped endpoints) become a shared node.
    Geometric crossings without a shared vertex remain separate edges.

    Repeated undirected segments use their maximum source width. If equally
    wide records disagree on kind, the lowest numeric kind wins deterministically.
    """
    x = np.asarray(points_x, dtype=np.float64)
    z = np.asarray(points_z, dtype=np.float64)
    offsets = np.asarray(offsets)
    kind = np.asarray(kind)
    widths = np.asarray(width_m, dtype=np.float64)
    if x.ndim != 1 or z.shape != x.shape or not np.isfinite(x).all() or not np.isfinite(z).all():
        raise ValueError("source coordinates must be finite, equally sized vectors")
    if (offsets.ndim != 1 or offsets.size == 0 or
            not np.issubdtype(offsets.dtype, np.integer) or offsets[0] != 0 or
            offsets[-1] != len(x) or np.any(np.diff(offsets) < 0)):
        raise ValueError("offsets must partition all source points in order")
    lines = len(offsets) - 1
    if kind.shape != (lines,) or widths.shape != (lines,):
        raise ValueError("each source line needs one kind and width")
    if (not np.issubdtype(kind.dtype, np.integer) or np.any(kind < 0) or
            np.any(kind > 255) or not np.isfinite(widths).all() or np.any(widths <= 0)):
        raise ValueError("kinds must fit uint8 and widths must be finite and positive")
    limits = np.asarray(bounds, dtype=np.float64)
    if (limits.shape != (4,) or not np.isfinite(limits).all() or
            limits[1] - limits[0] < 1 or limits[3] - limits[2] < 1):
        raise ValueError("bounds must contain at least one cell in each dimension")
    limits = (limits[0], limits[1] - 1, limits[2], limits[3] - 1)
    points = np.column_stack((x, z))
    segments = {}
    for line, (lo, hi) in enumerate(zip(offsets[:-1], offsets[1:])):
        attribute = (float(widths[line]), -int(kind[line]))
        for index in range(int(lo), int(hi) - 1):
            clipped = _clip_segment(points[index], points[index + 1], limits)
            if clipped is None:
                continue
            a, b = clipped
            if a == b:
                continue
            key = tuple(sorted((a, b)))
            if key not in segments or attribute > segments[key]:
                segments[key] = attribute
    vertices = sorted({point for segment in segments for point in segment})
    node_ids = {point: index for index, point in enumerate(vertices)}
    records = sorted((node_ids[a], node_ids[b], *attribute)
                     for (a, b), attribute in segments.items())
    return Network(
        nodes=np.asarray(vertices, dtype=np.float64).reshape(-1, 2),
        edges=np.asarray([(a, b) for a, b, _, _ in records], dtype=np.int32).reshape(-1, 2),
        kinds=np.asarray([-kind for _, _, _, kind in records], dtype=np.uint8),
        widths=np.asarray([width for _, _, width, _ in records], dtype=np.float32),
    )


def _corridor_cells(start, end, radius, shape):
    """Enumerate a thin segment capsule without allocating its bounding box."""
    delta = end - start
    major = int(abs(delta[1]) > abs(delta[0]))
    minor = 1 - major
    size = np.asarray([shape[1], shape[0]])
    low = max(0, int(math.floor(min(start[major], end[major]) - radius)))
    high = min(int(size[major]) - 1,
               int(math.ceil(max(start[major], end[major]) + radius)))
    majors = np.arange(low, high + 1, dtype=np.int32)
    fraction = np.clip((majors - start[major]) / delta[major], 0.0, 1.0)
    center_minor = start[minor] + fraction * delta[minor]
    reach = int(math.ceil(radius * math.sqrt(2))) + 1
    offsets = np.arange(-reach, reach + 1, dtype=np.int32)
    candidates = np.empty((len(majors), len(offsets), 2), dtype=np.int32)
    candidates[:, :, major] = majors[:, None]
    candidates[:, :, minor] = np.floor(center_minor[:, None]).astype(np.int32) + offsets
    cells = candidates.reshape(-1, 2)
    within = np.all((cells >= 0) & (cells < size), axis=1)
    cells = cells[within]
    projection = np.clip(((cells - start) @ delta) / float(delta @ delta), 0., 1.)
    nearest = start + projection[:, None] * delta
    distance2 = np.square(cells - nearest).sum(axis=1)
    allowed = distance2 <= radius * radius + 1e-10
    return cells[allowed], distance2[allowed]


class _NoRoute(ValueError):
    """The requested corridor cannot satisfy its spatial reservations."""


def _route_edge(start, end, terrain, corridor, can_visit=None, bank_weight=2.):
    if np.array_equal(start, end):
        return start[None].copy()
    cells, distance2 = _corridor_cells(start, end, corridor, terrain.shape)
    return _route_cells(start, end, cells, distance2, terrain, can_visit, bank_weight)


def _route_cells(start, end, cells, distance2, terrain, can_visit=None, bank_weight=2.):
    """Route within an explicit corridor, shared by segments and shape chains."""
    if can_visit is not None:
        available = np.asarray([can_visit(int(x), int(z)) for x, z in cells])
        cells, distance2 = cells[available], distance2[available]
    width = terrain.shape[1]
    keys = cells[:, 1].astype(np.int64) * width + cells[:, 0]
    lookup = {int(key): index for index, key in enumerate(keys)}
    heights = np.asarray(terrain[cells[:, 1], cells[:, 0]], dtype=np.float64)
    if not np.isfinite(heights).all():
        raise ValueError("terrain in a routing corridor contains non-finite heights")
    valley = heights.copy()
    for dz in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            x = np.clip(cells[:, 0] + dx, 0, width - 1)
            z = np.clip(cells[:, 1] + dz, 0, terrain.shape[0] - 1)
            np.minimum(valley, terrain[z, x], out=valley)
    if not np.isfinite(valley).all():
        raise ValueError("terrain beside a routing corridor contains non-finite heights")
    ridge = np.maximum(0., heights - valley)
    exposed = np.zeros(len(cells),dtype=np.float64)
    for dx,dz in ((-1,0),(0,-1),(0,1),(1,0)):
        x = np.clip(cells[:,0]+dx,0,width-1)
        z = np.clip(cells[:,1]+dz,0,terrain.shape[0]-1)
        exposed = np.maximum(exposed,heights-terrain[z,x])
    start_index = lookup[int(start[1]) * width + int(start[0])]
    end_index = lookup[int(end[1]) * width + int(end[0])]
    # Search downhill to distinguish avoidable climbing from genuine descent.
    # This does not assign graph flow direction; the returned path follows the
    # original edge indices, including when the search order is reversed.
    reverse = heights[start_index] < heights[end_index]
    if reverse:
        start_index, end_index = end_index, start_index
    target = cells[end_index]
    costs = np.full(len(cells), np.inf, dtype=np.float64)
    parents = np.full(len(cells), -1, dtype=np.int32)
    costs[start_index] = 0.

    def heuristic(index):
        return int(np.abs(cells[index] - target).sum())

    estimate = heuristic(start_index)
    pending = [(float(estimate), estimate, int(keys[start_index]), 0., start_index)]
    while pending:
        _, _, _, cost, current = heapq.heappop(pending)
        if cost > costs[current]:
            continue
        if current == end_index:
            indices = []
            while current != -1:
                indices.append(current)
                current = int(parents[current])
            path = cells[np.asarray(indices[::-1])]
            return path[::-1].copy() if reverse else path
        x, z = cells[current]
        for dx, dz in ((-1, 0), (0, -1), (0, 1), (1, 0)):
            nx, nz = int(x) + dx, int(z) + dz
            if nx < 0 or nx >= width or nz < 0 or nz >= terrain.shape[0]:
                continue
            neighbour = lookup.get(nz * width + nx)
            if neighbour is None:
                continue
            climb = heights[neighbour] - heights[current]
            step = (1. + .12 * distance2[neighbour] + .7 * ridge[neighbour] +
                    bank_weight * max(0.,exposed[neighbour]-2.)**2 +
                    .2 * abs(climb) + 3. * max(0., climb) ** 2)
            candidate = cost + step
            if candidate >= costs[neighbour]:
                continue
            costs[neighbour] = candidate
            parents[neighbour] = current
            estimate = heuristic(neighbour)
            heapq.heappush(pending, (candidate + estimate, estimate,
                                     int(keys[neighbour]), candidate, neighbour))
    raise _NoRoute("no cardinal route connects an edge within its terrain corridor")


def _source_chains(edges, cells):
    """Remove degree-two shape anchors without changing source edge identity."""
    adjacent = [[] for _ in cells]
    for index, (a, b) in enumerate(edges):
        adjacent[a].append((int(b), index))
        adjacent[b].append((int(a), index))
    anchors = {i for i, neighbours in enumerate(adjacent) if len(neighbours) != 2}
    # A closed component needs two anchors so its two arcs remain separate.
    unseen = set(range(len(cells)))
    while unseen:
        first = min(unseen)
        component, pending = set(), [first]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            unseen.discard(node)
            pending.extend(other for other, _ in adjacent[node] if other not in component)
        if not component.intersection(anchors):
            anchors.add(first)
            second = max(component, key=lambda node: (int(np.square(cells[node] - cells[first]).sum()), -node))
            anchors.add(second)
    used, chains = set(), []
    for start in sorted(anchors):
        for neighbour, edge in sorted(adjacent[start]):
            if edge in used:
                continue
            vertices, records = [start], []
            previous, current = start, neighbour
            while True:
                used.add(edge)
                records.append(edge)
                vertices.append(current)
                if current in anchors:
                    break
                following = [(other, item) for other, item in adjacent[current] if item != edge]
                other, item = following[0]
                previous, current, edge = current, other, item
            chains.append((vertices, records))
    return anchors, chains


def _chain_corridor(vertices, cells, radius, shape):
    """Union segment capsules, retaining distance to the original source line."""
    width = shape[1]
    distances = {}
    for a, b in zip(vertices[:-1], vertices[1:]):
        if np.array_equal(cells[a], cells[b]):
            distances.setdefault(int(cells[a, 1]) * width + int(cells[a, 0]), 0.)
            continue
        available, distance2 = _corridor_cells(cells[a], cells[b], radius, shape)
        for (x, z), value in zip(available, distance2):
            key = int(z) * width + int(x)
            distances[key] = min(distances.get(key, math.inf), float(value))
    keys = sorted(distances)
    return (np.asarray([(key % width, key // width) for key in keys], dtype=np.int32),
            np.asarray([distances[key] for key in keys], dtype=np.float64))


def _split_chain(path, vertices, records, cells, edges, corridor):
    """Allocate each routed station to an ordered source edge, allowing subcells.

    Shape nodes are correspondence markers, not hydrological junctions. Their
    nearest monotonically ordered stations preserve the original connectivity.
    A zero-length allocated edge remains explicit, so connected subcell nodes
    can be contracted by the compiler without aliasing unrelated source nodes.
    """
    cuts = [0]
    for vertex in vertices[1:-1]:
        distance2 = np.square(path[cuts[-1]:].astype(np.int64) - cells[vertex]).sum(axis=1)
        nearest = int(np.argmin(distance2))
        if distance2[nearest] > corridor*corridor+1e-9:
            raise _NoRoute('chain route skipped a source shape point outside its corridor')
        cuts.append(cuts[-1] + nearest)
    cuts.append(len(path) - 1)
    result = []
    for index, edge in enumerate(records):
        part = path[cuts[index]:cuts[index + 1] + 1]
        if edges[edge, 0] != vertices[index]:
            part = part[::-1]
        result.append((edge, part.copy()))
    return result


def _route_network(network, terrain, *, origin=(0, 0), corridor=3, chain_order='forward',
                   reserve_portals=True, bank_weight=2.):
    """Return one cardinal integer ``[x,z]`` path per edge in world coordinates.

    The search stays within the union of ``corridor`` capsules along each
    degree-two source chain. True junctions and terminal endpoints never move;
    degree-two shape points may move within the corridor, in source order.
    A radius of at least one block lets diagonal segments admit cardinal steps. Route costs
    prefer nearby valley cells and avoid climbs, while penalizing detours and
    departure from the mapped segment. Earlier routes reserve their occupied
    cells and lateral contacts, except at an explicitly shared source endpoint.
    Hydrological anchors are protected before routing begins. Dense shaping
    vertices no longer force adjacent tributaries into touching block cells.

    Some source geometries cannot be embedded without a crossing inside their
    bounded corridors. For those edges the unconstrained route is retained so
    the scene compiler can report the conflict; no collision is waived here.
    Output still has one path per source edge, in the edge's original order.
    """
    terrain = np.asarray(terrain)
    origin = np.asarray(origin)
    if terrain.ndim != 2 or not terrain.size or not np.issubdtype(terrain.dtype, np.number):
        raise ValueError("terrain must be a nonempty numeric height raster")
    if (origin.shape != (2,) or not np.isfinite(origin).all() or
            np.any(origin != np.rint(origin))):
        raise ValueError("origin must contain two integer block coordinates")
    if not math.isfinite(corridor) or corridor < 1:
        raise ValueError("corridor must be finite and at least one block")
    origin = origin.astype(np.int64)
    nodes = np.asarray(network.nodes, dtype=np.float64)
    edges = np.asarray(network.edges)
    if nodes.ndim != 2 or nodes.shape[1] != 2 or not np.isfinite(nodes).all():
        raise ValueError("network nodes must be finite [x,z] coordinates")
    if (edges.ndim != 2 or edges.shape[1] != 2 or
            not np.issubdtype(edges.dtype, np.integer) or
            np.any(edges < 0) or np.any(edges >= len(nodes))):
        raise ValueError("network edges must index existing nodes")
    world_cells = np.rint(nodes).astype(np.int64)
    cells = world_cells - origin
    size = np.asarray([terrain.shape[1], terrain.shape[0]])
    if np.any(cells < 0) or np.any(cells >= size):
        raise ValueError("network endpoint cells lie outside the supplied terrain")
    if np.any(world_cells < np.iinfo(np.int32).min) or np.any(world_cells > np.iinfo(np.int32).max):
        raise ValueError("world coordinates must fit int32 block paths")
    width = terrain.shape[1]
    node_keys = cells[:, 1] * width + cells[:, 0]
    anchors, chains = _source_chains(edges,cells)
    protected = {int(node_keys[node]) for node in anchors}
    # An actual subcell source segment represents connectivity even when both
    # of its nodes occupy one cell. It may share that cell with either incident
    # edge. Merely nearby nodes without such a segment are never aliased.
    aliases = list(range(len(nodes)))

    def alias(node):
        node = int(node)
        while aliases[node] != node:
            aliases[node] = aliases[aliases[node]]
            node = aliases[node]
        return node

    for a, b in edges:
        if node_keys[a] == node_keys[b]:
            first, second = alias(a), alias(b)
            aliases[max(first, second)] = min(first, second)
    incident = {}
    for index, (vertices, _) in enumerate(chains):
        for node in (vertices[0], vertices[-1]):
            incident.setdefault(alias(node), set()).add(index)

    # Reserve distinct mouths before the first tributary is routed. Reserving
    # only completed paths lets an early branch occupy the exit needed by a
    # later branch, even when a valid junction fan exists inside the corridor.
    portals = {}
    placed = set()
    for anchor in sorted(anchors):
        identity = alias(anchor)
        if identity in placed:
            continue
        placed.add(identity)
        branch_ids = [index for index in sorted(incident[identity])
                      if not np.array_equal(cells[chains[index][0][0]],cells[chains[index][0][-1]])]
        choices = [(dx,dz) for dx,dz in ((-1,0),(0,-1),(0,1),(1,0))
                   if 0<=cells[anchor,0]+dx<width and 0<=cells[anchor,1]+dz<terrain.shape[0]]
        if len(branch_ids)>len(choices):
            continue  # Unresolved high-degree embeddings stay diagnostic.
        directions = []
        for index in branch_ids:
            vertices = chains[index][0]
            sequence = vertices if alias(vertices[0]) == identity else vertices[::-1]
            reference = cells[sequence[-1]]
            for vertex in sequence[1:]:
                if np.linalg.norm(cells[vertex]-cells[anchor]) >= corridor+1:
                    reference = cells[vertex]
                    break
            delta = reference-cells[anchor]
            directions.append(delta/max(1.,float(np.linalg.norm(delta))))
        def fan_cost(fan):
            cost = 0.
            for index,direction,(dx,dz) in zip(branch_ids,directions,fan):
                x,z = cells[anchor]+(dx,dz)
                key = int(z)*width+int(x)
                ends = {int(node_keys[chains[index][0][0]]),int(node_keys[chains[index][0][-1]])}
                if key in protected and key not in ends:
                    cost += 1000.
                cost += 1.-float(direction @ (dx,dz))
                cost += .03*abs(float(terrain[z,x])-float(terrain[cells[anchor,1],cells[anchor,0]]))
            return cost
        best = min(permutations(choices,len(branch_ids)),key=lambda fan:(fan_cost(fan),fan))
        for index,(dx,dz) in zip(branch_ids,best):
            x,z = cells[anchor]+(dx,dz)
            portals.setdefault(int(z)*width+int(x),set()).add(index)

    occupied = {}
    paths = [None]*len(edges)
    order = list(range(len(chains)))
    if chain_order == 'reverse':
        order.reverse()
    elif chain_order == 'longest':
        order.sort(key=lambda i:(-sum(float(np.linalg.norm(cells[a]-cells[b]))
                                      for a,b in zip(chains[i][0][:-1],chains[i][0][1:])),i))
    for index in order:
        vertices,records = chains[index]
        a,b = vertices[0],vertices[-1]
        endpoints = {int(node_keys[a]), int(node_keys[b])}
        permitted_contacts = {}
        for node in (a, b):
            permitted_contacts.setdefault(int(node_keys[node]), set()).update(incident[alias(node)])

        def can_visit(x, z, reserve_fan=reserve_portals):
            key = z * width + x
            if key in endpoints:
                # Distinct source endpoints may round onto an occupied cell.
                # Keep both anchors; the compiler must diagnose their identity
                # conflict rather than silently moving or welding them.
                return True
            if key in protected or key in occupied:
                return False
            if reserve_fan and key in portals and index not in portals[key]:
                return False
            for dx, dz in ((-1, 0), (0, -1), (0, 1), (1, 0)):
                nx, nz = x + dx, z + dz
                if not (0 <= nx < width and 0 <= nz < terrain.shape[0]):
                    continue
                neighbour = nz * width + nx
                reserved = portals.get(neighbour)
                if reserve_fan and reserved and index not in reserved and neighbour not in endpoints:
                    return False
                prior = occupied.get(neighbour)
                if prior and not prior.issubset(permitted_contacts.get(neighbour, ())):
                    return False
            return True

        available, distance2 = _chain_corridor(vertices,cells,float(corridor),terrain.shape)
        try:
            if np.array_equal(cells[a],cells[b]):
                local = cells[a][None].copy()
            else:
                local = _route_cells(cells[a],cells[b],available,distance2,terrain,can_visit,bank_weight)
            pieces = _split_chain(local,vertices,records,cells,edges,float(corridor))
        except _NoRoute:
            try:
                # A preferred junction fan is a routing hint. Relax that hint
                # before giving up a valid whole-chain embedding, retaining
                # reservations against every already occupied water route.
                local = _route_cells(cells[a],cells[b],available,distance2,terrain,
                                     lambda x,z:can_visit(x,z,False),bank_weight)
                pieces = _split_chain(local,vertices,records,cells,edges,float(corridor))
            except _NoRoute:
                # Preserve original segment anchors when whole-chain routing
                # fails. The compiler still blocks any resulting contact.
                pieces = [(edge,_route_edge(cells[edges[edge,0]],cells[edges[edge,1]],
                                           terrain,float(corridor),bank_weight=bank_weight)) for edge in records]
                local = np.concatenate([piece for _,piece in pieces])
        for x, z in local:
            occupied.setdefault(int(z) * width + int(x), set()).add(index)
        for edge,piece in pieces:
            paths[edge] = (piece + origin).astype(np.int32)
    return paths


def _embedding_conflicts(network, paths):
    """Count actual new contacts, without relaxing source node identities."""
    parent = list(range(len(network.nodes)))
    def root(node):
        node = int(node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node
    for path,(a,b) in zip(paths,network.edges):
        if len(path) == 1:
            a,b = root(a),root(b)
            parent[max(a,b)] = min(a,b)
    ownership, planned = {},set()
    for index,(path,edge) in enumerate(zip(paths,network.edges)):
        points = [tuple(map(int,p)) for p in path]
        for station,point in enumerate(points):
            endpoint = station in (0,len(points)-1)
            identity = root(edge[0 if station==0 else 1]) if endpoint else None
            ownership.setdefault(point,[]).append((index,endpoint,identity))
        planned.update(tuple(sorted(pair)) for pair in zip(points[:-1],points[1:]))
    collisions = 0
    for entries in ownership.values():
        if len(entries)>1 and not (all(e[1] for e in entries) and len({e[2] for e in entries})==1):
            collisions += 1
    for x,z in ownership:
        for neighbour in ((x+1,z),(x,z+1)):
            if neighbour in ownership and tuple(sorted(((x,z),neighbour))) not in planned:
                collisions += 1
    return collisions


def candidate_routes(network, terrain, *, origin=(0,0), corridor=3):
    """Yield distinct bounded embeddings for the joint terrain/water solver."""
    seen = set()
    # Bank exposure and a prescribed junction fan are soft routing priors.
    # A valley-led alternative lets the hydraulic solver decide whether a
    # nearby steep edge is a valid descent instead of assuming it is a bank.
    strategies = [('forward','forward',True,2.),('reverse','reverse',True,2.),
                  ('longest','longest',True,2.),('valley_forward','forward',False,0.)]
    for strategy,order,portals,bank_weight in strategies:
        paths = _route_network(network,terrain,origin=origin,corridor=corridor,chain_order=order,
                               reserve_portals=portals,bank_weight=bank_weight)
        fingerprint = tuple(path.tobytes() for path in paths)
        if fingerprint not in seen:
            seen.add(fingerprint)
            yield strategy,paths


def route_network(network, terrain, *, origin=(0,0), corridor=3):
    """Route shared chains, retrying reservation order when contacts remain.

    Junctions/endpoints stay anchored, shape points stay within the source
    corridor, and each original edge retains its identity. Try a bounded set
    of reservation orders instead of accepting the first greedy dead end.
    Any remaining contact is preserved for the compiler to reject.
    """
    best = None
    best_score = None
    for _,paths in candidate_routes(network,terrain,origin=origin,corridor=corridor):
        conflicts = _embedding_conflicts(network,paths)
        score = (conflicts,sum(len(path) for path in paths))
        if best_score is None or score < best_score:
            best,best_score = paths,score
        if conflicts == 0:
            break
    return best
