"""Solve section levels as one bounded system of difference constraints.

For a constraint (a,b,drop), level[b] <= level[a]+drop. All costs are
nonnegative. Lower bounds propagate upstream first, then a shortest-path
closure adjusts the preferred levels within the physical ceilings. Preferred
levels are a soft target, not an extra bank constraint. An empty feasible
interval is returned explicitly; callers must not reshape banks to conceal it.
"""
import heapq

import numpy as np


def solve_levels(preferred, lower, upper, constraints):
    preferred, lower, upper = [np.asarray(a,dtype=np.int32) for a in (preferred,lower,upper)]
    if preferred.ndim != 1 or preferred.shape != lower.shape or preferred.shape != upper.shape:
        raise ValueError("section level bounds must be matching vectors")
    count = len(preferred)
    adjacent = [[] for _ in range(count)]
    upstream = [[] for _ in range(count)]
    for source,target,drop in constraints:
        if not (0 <= source < count and 0 <= target < count and drop >= 0):
            raise ValueError("invalid level constraint")
        adjacent[int(source)].append((int(target),int(drop)))
        upstream[int(target)].append((int(source),int(drop)))
    # A deeper downstream section can require a slightly higher upstream
    # stage. Propagate that requirement before lowering the preferred stages;
    # otherwise a feasible scene would be rejected merely for missing a soft
    # preferred elevation by one block.
    required = lower.copy()
    pending = [(-int(y),n) for n,y in enumerate(required)]
    heapq.heapify(pending)
    while pending:
        negative,node = heapq.heappop(pending)
        value = -negative
        if required[node] != value:
            continue
        for predecessor,drop in upstream[node]:
            bound = value-drop
            if bound > required[predecessor]:
                required[predecessor] = bound
                heapq.heappush(pending,(-bound,predecessor))
    level = np.minimum(np.maximum(preferred,required),upper)
    queue = [(int(y),n) for n,y in enumerate(level)]
    heapq.heapify(queue)
    while queue:
        value,node = heapq.heappop(queue)
        if level[node] != value:
            continue
        for neighbour,drop in adjacent[node]:
            bound = value+drop
            if bound < level[neighbour]:
                level[neighbour] = bound
                heapq.heappush(queue,(bound,neighbour))
    return level, np.flatnonzero(level < lower)
