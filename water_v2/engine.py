"""Compile one explicitly bounded water scene from a shared drainage graph.

This first solver reports infeasible terrain/flow rather than repairing banks.
Nothing here loads legacy water products or calls a legacy shaper/painter.
"""
import heapq

import numpy as np
from scipy import ndimage as ndi

from .model import DRY, Settings, validate
from .levels import solve_levels
from .basins import standing_basins
from .fluids import river_pools, surface_supply
from .diagnostics import encode, counts

CARDINAL = ndi.generate_binary_structure(2, 1)
PAIRS = ((np.s_[1:, :], np.s_[:-1, :]), (np.s_[:, 1:], np.s_[:, :-1]))


def _pairs(owner, wet):
    edges = []
    for a, b in PAIRS:
        mask = wet[a] & wet[b] & (owner[a] != owner[b])
        edges.append(np.stack((owner[a][mask], owner[b][mask]), axis=1))
    pairs = np.concatenate(edges) if edges else np.empty((0, 2), dtype=np.int32)
    return np.unique(np.sort(pairs, axis=1), axis=0)


def _section_owners(wet, seeds):
    """Assign sections through connected water, never across a dry gap."""
    owner = seeds.copy()
    distance = np.full(wet.shape,np.iinfo(np.int32).max,dtype=np.int32)
    queue = []
    height,width = wet.shape
    for z,x in np.argwhere(seeds>=0):
        distance[z,x] = 0
        heapq.heappush(queue,(0,int(seeds[z,x]),int(z),int(x)))
    while queue:
        steps,section,z,x = heapq.heappop(queue)
        if steps != distance[z,x] or owner[z,x] != section:
            continue
        for dz,dx in ((1,0),(-1,0),(0,1),(0,-1)):
            nz,nx = z+dz,x+dx
            if not (0<=nz<height and 0<=nx<width) or not wet[nz,nx]:
                continue
            candidate = (steps+1,section)
            if candidate < (int(distance[nz,nx]),int(owner[nz,nx])):
                distance[nz,nx],owner[nz,nx] = candidate
                heapq.heappush(queue,(steps+1,section,nz,nx))
    if np.any(wet & (owner<0)):
        raise ValueError("water component has no connected section seed")
    return owner


def _drainage(target, pairs, outlets):
    """Priority flood on shared sections: parent always drains without rising."""
    adjacent = [[] for _ in target]
    for a, b in pairs:
        adjacent[a].append(int(b))
        adjacent[b].append(int(a))
    parent = np.full(len(target), -1, dtype=np.int32)
    stage = np.full(len(target), np.iinfo(np.int32).max, dtype=np.int32)
    order = []
    visited = np.zeros(len(target), dtype=bool)
    candidates = set(map(int, outlets))
    roots = set()
    # A scene boundary may be an inlet, and a lake may have an outlet. Trace
    # each equal-height plateau before selecting sinks: a lower neighbour
    # means it must drain onward, even if that neighbour is beyond the lake.
    plateau_seen = np.zeros(len(target), dtype=bool)
    for start in sorted(candidates):
        if plateau_seen[start]:
            continue
        plateau, stack, has_lower = [], [start], False
        plateau_seen[start] = True
        while stack:
            at = stack.pop()
            plateau.append(at)
            for nxt in adjacent[at]:
                if target[nxt] < target[start]:
                    has_lower = True
                elif target[nxt] == target[start] and not plateau_seen[nxt]:
                    plateau_seen[nxt] = True
                    stack.append(nxt)
        if not has_lower:
            roots.add(min(plateau))
    # Every disconnected body must have an explicit outlet/sink. This choice
    # belongs to the scene, never to individual output tiles.
    labelled = np.zeros(len(target), dtype=bool)
    for start in range(len(target)):
        if labelled[start]:
            continue
        component, stack = [], [start]
        labelled[start] = True
        while stack:
            at = stack.pop()
            component.append(at)
            for nxt in adjacent[at]:
                if not labelled[nxt]:
                    labelled[nxt] = True
                    stack.append(nxt)
        # A connected lake can have a lower outlet in this scene. Keeping the
        # lake as the sole root would force that whole outlet back uphill.
        roots.add(min(component, key=lambda n: (target[n], n)))
    queue = [(int(target[n]), n, -1) for n in sorted(roots)]
    heapq.heapify(queue)
    while queue:
        level, node, previous = heapq.heappop(queue)
        if visited[node]:
            continue
        visited[node] = True
        stage[node], parent[node] = level, previous
        order.append(node)
        for nxt in adjacent[node]:
            if not visited[nxt] and nxt not in roots:
                heapq.heappush(queue, (max(level, int(target[nxt])), nxt, node))
    return stage, parent, np.asarray(order), adjacent


def _texture(shape, origin):
    """World-anchored interpolated patches, not per-block white noise."""
    z, x = np.indices(shape, dtype=np.float64)
    x, z = (x+origin[0])/12, (z+origin[1])/12
    ix, iz = np.floor(x).astype(np.int64), np.floor(z).astype(np.int64)
    fx, fz = x-ix, z-iz
    fx, fz = fx*fx*(3-2*fx), fz*fz*(3-2*fz)
    def corner(dx, dz):
        return (((ix+dx)*73856093 ^ (iz+dz)*19349663 ^ 83492791) % 65521)/65520
    return ((1-fx)*(1-fz)*corner(0,0)+fx*(1-fz)*corner(1,0)
            +(1-fx)*fz*corner(0,1)+fx*fz*corner(1,1))


def compile_scene(terrain, waterbody, routes, *, origin=(0, 0), radii=None, route_nodes=None,
                  settings=None):
    """Return (explicit product, physical diagnostics) without world writes.

    routes are cardinal paths in world [x,z]. A scene must be solved once and
    cropped for output; independent scene boundaries are not global parity.
    """
    settings = settings or Settings()
    source_terrain = np.asarray(terrain)
    if source_terrain.dtype.kind not in "iu" or source_terrain.ndim != 2:
        raise ValueError("terrain must contain integer block heights")
    origin_array = np.asarray(origin)
    if origin_array.shape != (2,) or origin_array.dtype.kind not in "iu" or np.any(origin_array<0):
        raise ValueError("origin must contain two nonnegative integer coordinates")
    origin = tuple(map(int,origin_array))
    # Check before narrowing: large unsigned/int64 inputs must not wrap into
    # apparently valid Minecraft heights.
    if source_terrain.size and (source_terrain.min() < settings.min_y or
                                source_terrain.max() > settings.max_y):
        raise ValueError("terrain is outside declared dimension bounds")
    base = np.asarray(source_terrain, dtype=np.int32)
    body = np.asarray(waterbody, dtype=bool)
    if base.ndim != 2 or body.shape != base.shape or min(base.shape) < 3:
        raise ValueError("terrain/body must be matching 2D scenes of at least 3x3")
    if base.min() < settings.min_y or base.max() > settings.max_y:
        raise ValueError("terrain is outside declared dimension bounds")
    h, w = base.shape
    route_mask = np.zeros(base.shape, dtype=bool)
    wet = body.copy()
    route_owners = {}
    collisions = set()
    routes = list(routes)
    for route in routes:
        path = np.asarray(route)
        if path.ndim != 2 or path.shape[1] != 2 or not len(path) or path.dtype.kind not in "iu":
            raise ValueError("each route must be a nonempty integer [x,z] path")
    radii = list(radii) if radii is not None else [0]*len(routes)
    if len(radii) != len(routes):
        raise ValueError("radii must match routes")
    if any(not np.isfinite(r) or r < 0 or r != int(r) for r in radii):
        raise ValueError("route radii must be nonnegative integer block counts")
    if route_nodes is not None:
        route_nodes = np.asarray(route_nodes)
        if route_nodes.shape != (len(routes),2) or route_nodes.dtype.kind not in "iu":
            raise ValueError("route_nodes must preserve both source node IDs for each route")
        # Contract a subcell source edge only when that actual edge collapses
        # to one raster cell. Merely nearby disconnected endpoints stay apart.
        node_parent = {int(n):int(n) for n in route_nodes.ravel()}
        def node_root(n):
            while node_parent[n] != n:
                node_parent[n] = node_parent[node_parent[n]]
                n = node_parent[n]
            return n
        for route,(a,b) in zip(routes,route_nodes):
            if len(route) == 1:
                a,b = node_root(int(a)),node_root(int(b))
                node_parent[max(a,b)] = min(a,b)
        route_nodes = np.asarray([[node_root(int(n)) for n in row] for row in route_nodes],dtype=np.int32).reshape(-1,2)
    components = list(range(len(routes)))
    def root(n):
        while components[n] != n:
            components[n] = components[components[n]]
            n = components[n]
        return n
    endpoint_routes = {}
    for i,route in enumerate(routes):
        endpoints = route_nodes[i].tolist() if route_nodes is not None else [tuple(route[0]),tuple(route[-1])]
        for endpoint in endpoints:
            if endpoint in endpoint_routes:
                a,b = root(i),root(endpoint_routes[endpoint])
                components[max(a,b)] = min(a,b)
            endpoint_routes[endpoint] = i
    footprint_owner = np.full(base.shape,-1,dtype=np.int32)
    unplanned_contact = np.zeros(base.shape,dtype=bool)
    for index, (route, radius) in enumerate(zip(routes, radii)):
        path = np.asarray(route, dtype=np.int32)-np.asarray(origin)
        if path.ndim != 2 or path.shape[1] != 2 or not len(path):
            raise ValueError("each route must be nonempty [x,z] coordinates")
        if np.any(np.sum(np.abs(np.diff(path, axis=0)), axis=1) != 1):
            raise ValueError("routes must be cardinal and contain no duplicate stations")
        for i, (x,z) in enumerate(path):
            if not (0 <= x < w and 0 <= z < h):
                raise ValueError("route lies outside the scene")
            endpoint = i in (0, len(path)-1)
            identity = (int(route_nodes[index,0 if i == 0 else 1]) if route_nodes is not None
                        else (int(x),int(z))) if endpoint else None
            for prior, was_endpoint, prior_identity in route_owners.get((int(x),int(z)), []):
                if prior != index and not (endpoint and was_endpoint and identity == prior_identity):
                    collisions.add((int(x),int(z)))
            route_owners.setdefault((int(x),int(z)), []).append((index,endpoint,identity))
            route_mask[z,x] = True
            for dz in range(-int(radius), int(radius)+1):
                for dx in range(-int(radius), int(radius)+1):
                    if dx*dx+dz*dz <= radius*radius and 0 <= x+dx < w and 0 <= z+dz < h:
                        wet[z+dz,x+dx] = True
                        previous = footprint_owner[z+dz,x+dx]
                        if previous >= 0 and previous != root(index) and not body[z+dz,x+dx]:
                            unplanned_contact[z+dz,x+dx] = True
                        footprint_owner[z+dz,x+dx] = root(index)
    for a,b in PAIRS:
        touch = ((footprint_owner[a]>=0)&(footprint_owner[b]>=0)&
                 (footprint_owner[a]!=footprint_owner[b])&~body[a]&~body[b])
        unplanned_contact[a] |= touch
        unplanned_contact[b] |= touch

    lakes, lake_levels = standing_basins(base, body, settings)
    standing = lakes > 0
    # A sloped mapped polygon is represented in the graph too, even when OSM
    # supplied no line. No constant-depth/zero-flow fallback is substituted.
    centers = (route_mask | (body & ~standing)) & ~standing
    owner = np.full(base.shape, -1, dtype=np.int32)
    for lake, _ in enumerate(lake_levels, start=1):
        owner[lakes == lake] = lake-1
    preferred_groups = list(lake_levels)
    # Three or more reaches meeting at an explicit source endpoint share one
    # junction pool, including their immediately adjacent mouth stations.
    # This is solved as one graph node, before any stage or bed is produced.
    junction = np.zeros(base.shape, dtype=bool)
    for (x,z), entries in route_owners.items():
        entries = [entry for entry in entries if len(routes[entry[0]])>1]
        if (len(entries) >= 3 and all(endpoint for _,endpoint,_ in entries)
                and len({identity for _,_,identity in entries}) == 1):
            junction[z,x] = True
    # A shared mouth can include neighbouring stations at the same observed
    # level, but cannot flatten the upstream/downstream slope into a pool.
    # Keep steeper entrance steps as separate sections solved against the
    # common junction stage.
    junction_group = junction.copy()
    for a,b in PAIRS:
        for center,neighbour in ((a,b),(b,a)):
            junction_group[neighbour] |= junction[center] & wet[neighbour] & (
                base[neighbour] == base[center])
    junction = junction_group & wet & ~standing
    junction_labels, junction_count = ndi.label(junction, CARDINAL)
    for ident in range(1,junction_count+1):
        group = junction_labels == ident
        owner[group] = len(preferred_groups)
        preferred_groups.append(int(base[group].min())-settings.river_incision)
    centers &= ~junction
    coordinates = np.argwhere(centers)
    owner[centers] = np.arange(len(preferred_groups), len(preferred_groups)+len(coordinates))
    seed = owner >= 0
    if wet.any() and not seed.any():
        raise ValueError("wet footprint has no graph stations")
    if seed.any():
        owner = _section_owners(wet,owner)
    count = len(preferred_groups)+len(coordinates)
    product = {"bounds": np.asarray([origin[0],origin[0]+w,origin[1],origin[1]+h],dtype=np.int32)}
    # Empty scenes are valid, useful for ensuring old root water cannot leak in.
    if not count:
        from .model import FIELDS
        for key, dtype in FIELDS.items():
            product[key] = np.zeros(base.shape, dtype=dtype)
        product["terrain_y"] = base.astype(np.int16)
        product["surface_y"].fill(DRY)
        product["waterfall_top_y"].fill(DRY)
        product["receiver"].fill(-1)
        validate(product)
        return product, {"ready_for_world_write": True, "violations": {}, "water_cells": 0,
                         "domain": "bounded_scene", "lake_count": 0}

    preferred = np.asarray(preferred_groups + [int(base[z,x])-settings.river_incision
                                        for z,x in coordinates], dtype=np.int32)
    cap = np.full(count, np.iinfo(np.int32).max, dtype=np.int32)
    np.minimum.at(cap, owner[wet], base[wet]+settings.max_stage_raise)
    bank = ndi.binary_dilation(wet, structure=CARDINAL) & ~wet
    for a,b in PAIRS:
        for dry, water in ((a,b),(b,a)):
            touch = ~wet[dry] & wet[water]
            np.minimum.at(cap, owner[water][touch], base[dry][touch])
    target = np.minimum(preferred, cap)
    target[:len(lake_levels)] = lake_levels
    pairs = _pairs(owner,wet)
    planned_edges = set()
    junction_sections = set(range(len(lake_levels),len(preferred_groups)))
    for route,radius in zip(routes,radii):
        path = np.asarray(route,dtype=np.int32)-np.asarray(origin)
        stations = owner[path[:,1],path[:,0]]
        planned_edges.update(tuple(sorted((int(a),int(b)))) for a,b in zip(stations[:-1],stations[1:]))
        # A widened mouth contacts the junction across its width before its
        # centreline reaches it. Plan that bounded contact along the incident
        # reach; unrelated routes and distant meander stations remain invalid.
        for index,node in enumerate(stations):
            if int(node) in junction_sections:
                reach = int(radius)+1
                planned_edges.update(tuple(sorted((int(node),int(other))))
                    for other in stations[max(0,index-reach):index+reach+1])
    body_sections = set(map(int,owner[body]))
    unexpected_edges = {tuple(map(int,pair)) for pair in pairs
                        if tuple(map(int,pair)) not in planned_edges
                        and not any(int(node) in body_sections for node in pair)}
    # A meander may touch its own nonconsecutive stations even though every
    # cell belongs to the same source component. Such a shortcut changes the
    # river topology just as much as welding two unrelated source lines.
    for a,b in PAIRS:
        touching = wet[a]&wet[b]&(owner[a]!=owner[b])
        for z,x in np.argwhere(touching):
            pair = tuple(sorted((int(owner[a][z,x]),int(owner[b][z,x]))))
            if pair in unexpected_edges:
                unplanned_contact[a][z,x] = True
                unplanned_contact[b][z,x] = True
    boundary = np.zeros(base.shape,dtype=bool)
    boundary[[0,-1],:] = True
    boundary[:,[0,-1]] = True
    outlets = set(owner[wet & boundary]) | set(range(len(lake_levels)))
    stage, parents, order, adjacent = _drainage(target,pairs,outlets)
    distance = ndi.distance_transform_edt(np.pad(wet,1))[1:-1,1:-1]
    depth = np.zeros(base.shape,dtype=np.uint8)
    depth[wet] = np.clip(np.ceil(distance[wet]),1,settings.max_river_depth)
    depth[standing] = np.clip(np.rint(settings.max_lake_depth*(1-np.exp(
        -distance[standing]/settings.lake_shelf))),1,settings.max_lake_depth)
    lower = np.full(count,settings.min_y,dtype=np.int32)
    # River depth is a design target, not a mandatory excavation amount.
    # Solve for a positive column within the cutting budget first; choose its
    # final depth below after the shared stage is known.
    np.maximum.at(lower,owner[wet & ~standing],
                  base[wet & ~standing]+1-settings.max_excavation)
    # A DEM water core has one block of observation uncertainty, not an
    # unlimited incision allowance. Its solved level is shared by the basin.
    lower[:len(lake_levels)] = np.asarray(lake_levels)-1
    constraints = []
    for a,b in PAIRS:
        for src,dst in ((a,b),(b,a)):
            touch = wet[src]&wet[dst]&(owner[src]!=owner[dst])
            # Permit a high step only on that precise terrain edge.
            drop = np.maximum(settings.ordinary_drop,base[dst][touch]-base[src][touch])
            constraints.extend(zip(owner[src][touch].tolist(),owner[dst][touch].tolist(),drop.tolist()))
    constraints.extend((node,int(parent),0) for node,parent in enumerate(parents) if parent>=0)
    stage, infeasible = solve_levels(stage,lower,cap,constraints)
    surface = np.full(base.shape,DRY,dtype=np.int16)
    surface[wet] = stage[owner[wet]].astype(np.int16)
    river = wet & ~standing
    depth[river] = np.minimum(depth[river],np.maximum(1,
        surface[river].astype(int)-base[river]+settings.max_excavation)).astype(np.uint8)
    bed = base.copy()
    bed[wet] = surface[wet].astype(np.int32)-depth[wet]
    pool = river_pools(surface,wet,standing,base)
    source_volume = standing | pool
    incoming = np.bincount(parents[parents>=0],minlength=count)
    sources = source_volume.copy()
    for node,(z,x) in enumerate(coordinates,start=len(preferred_groups)):
        if incoming[node] == 0 and route_mask[z,x]:
            sources[z,x] = True
    # A mapped river polygon can be wider than seven blocks. Supply its whole
    # upper cross-section, not arbitrary leaves of a raster spanning tree.
    # Only plateaus with no higher wet neighbour can be headwater sources.
    higher_neighbour = np.zeros(base.shape,dtype=bool)
    for a,b in PAIRS:
        for high,low in ((a,b),(b,a)):
            higher_neighbour[low] |= wet[high] & wet[low] & (surface[high]>surface[low])
    for level in np.unique(surface[body & ~standing]):
        plateau, n = ndi.label(wet & (surface == level),CARDINAL)
        blocked = set(np.unique(plateau[higher_neighbour])) | {0}
        head = ~np.isin(plateau,list(blocked))
        sources |= head & body & ~standing
    levels = surface_supply(surface,wet,sources)
    supplied = levels != 255
    top = np.full(base.shape,DRY,dtype=np.int16)
    unsupported = np.zeros(base.shape,dtype=bool)
    for a,b in PAIRS:
        for high,low in ((a,b),(b,a)):
            drop = surface[high].astype(int)-surface[low].astype(int)
            connected = wet[high] & wet[low]
            fall = connected & (drop > 0)
            top[low] = np.maximum(top[low],np.where(fall & (drop>1),surface[high]-1,DRY))
            unsupported[high] |= connected & (drop > settings.ordinary_drop) & (
                base[high]-base[low] < settings.ordinary_drop+1)
    # A lake surface remains a source under an incoming falling column.
    levels[(top != DRY) & ~source_volume] = 8
    levels[~supplied | ~wet] = 0  # diagnostic placeholders, never write when unsupplied
    climb = np.zeros(base.shape,dtype=np.int32)
    for a,b in PAIRS:
        diff = np.where(wet[a]&wet[b],np.abs(surface[a].astype(int)-surface[b].astype(int)),0)
        climb[a] = np.maximum(climb[a],diff)
        climb[b] = np.maximum(climb[b],diff)
    energy = np.clip(climb*48,0,255).astype(np.uint8)
    texture = _texture(base.shape,origin)
    material = np.ones(base.shape,dtype=np.uint8)
    material[standing & (distance > 4)] = 3
    material[standing & (distance > 9) & (texture>.55)] = 4
    material[wet & (distance <= 2) & (texture>.6)] = 2
    material[wet & (climb>=1) & (texture>.7)] = 5
    material[wet & (climb>=2)] = 0
    plants = np.zeros(base.shape,dtype=np.uint8)
    zgrid,xgrid = np.indices(base.shape)
    sparse = ((xgrid+origin[0])*7+(zgrid+origin[1])*11)%31 == 0
    plants[standing & (depth>=2) & (depth<=4) & (material>=1) &
           (material<=4) & sparse & (top==DRY)] = 1
    # Receiver indices are local flat indices, independent of output tiling.
    representative = np.full(count,-1,dtype=np.int32)
    for z,x in np.argwhere(wet):
        node = owner[z,x]
        if representative[node] < 0:
            representative[node] = z*w+x
    receiver = np.full(base.shape,-1,dtype=np.int32)
    nodes_with_parent = wet.copy()
    nodes_with_parent[wet] = parents[owner[wet]] >= 0
    receiver[nodes_with_parent] = representative[parents[owner[nodes_with_parent]]]
    product.update(terrain_y=bed.astype(np.int16),water_mask=wet,standing_water_mask=source_volume,
                   surface_y=surface,depth=depth,water_level=levels.astype(np.uint8),
                   waterfall_top_y=top,bed_material=material,bank_mask=bank,
                   bank_material=np.where(texture>.7,2,1).astype(np.uint8),
                   aquatic_plant=plants,section_id=np.where(wet,owner+1,0).astype(np.int32),
                   receiver=receiver,flow_index=energy,river_pool_mask=pool)
    masks = {
        "unplanned_water_contact": unplanned_contact,
        "infeasible_section_level": wet & np.isin(owner,infeasible),
        "stage_above_terrain_limit": wet & (surface.astype(int)>base+settings.max_stage_raise),
        "excessive_river_excavation": wet & ~standing & (base-bed>settings.max_excavation),
        "unsupported_drop": unsupported,
        "unsupplied_water": wet & ~supplied,
        "bed_below_dimension": wet & (bed < settings.min_y),
    }
    uncontained = np.zeros(base.shape,dtype=bool)
    for a,b in PAIRS:
        for dry,water in ((a,b),(b,a)):
            uncontained[dry] |= ~wet[dry] & wet[water] & (base[dry] < surface[water])
    masks["uncontained_bank"] = uncontained
    crossing = np.zeros(base.shape,dtype=bool)
    for x,z in collisions:
        crossing[z,x] = True
    masks['ambiguous_route_crossing'] = crossing
    product['diagnostic_flags'] = encode(masks,base.shape)
    violations = counts(product['diagnostic_flags'])
    examples = {name:[{"x":int(x+origin[0]),"z":int(z+origin[1])}
                      for z,x in np.argwhere(mask)[:8]] for name,mask in masks.items() if mask.any()}
    if collisions:
        examples["ambiguous_route_crossing"] = [{"x":x+origin[0],"z":z+origin[1]}
                                                for x,z in sorted(collisions)[:8]]
    report = {"ready_for_world_write": not violations,"violations":violations,"examples":examples,
              "domain":"bounded_scene","water_cells":int(wet.sum()),"lake_count":len(lake_levels),
              "river_pool_cells":int(pool.sum()),
              "river_pool_count":sum(int(ndi.label(pool & (surface==level),CARDINAL)[1])
                                     for level in np.unique(surface[pool])),
              "graph_nodes":count,"graph_edges":len(pairs),"source_cells":int((wet&(levels==0)&supplied).sum()),
              "flowing_cells":int((wet&(levels>0)).sum()),"dry_terrain_modified":int((bed[~wet]!=base[~wet]).sum())}
    validate(product)
    return product, report
