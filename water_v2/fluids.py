"""Explicit source pools and preliminary surface supply, separate from lakes.

This is a bounded design model, not a replacement for Minecraft tick tests.
"""
import heapq

import numpy as np
from scipy import ndimage as ndi

CARDINAL = ndi.generate_binary_structure(2,1)
PAIRS = ((np.s_[1:,:],np.s_[:-1,:]),(np.s_[:,1:],np.s_[:,:-1]))
HORIZONTAL_REACH = 7


def river_pools(surface, wet, lakes, base):
    """Identify whole contained level reaches longer than horizontal supply.

    A level reach wider/longer than the flow range is an intentional source
    pool, with source water through its entire depth. This never flattens the
    stage, deepens a river into a lake, or inserts periodic source patches.
    Classification uses plateau geometry, not a failed supply report.
    """
    pools = np.zeros(wet.shape,dtype=bool)
    unsafe_shore = np.zeros(wet.shape,dtype=bool)
    for a,b in PAIRS:
        for water,dry in ((a,b),(b,a)):
            unsafe_shore[water] |= wet[water] & ~wet[dry] & (base[dry] < surface[water])
    inlet, outlet = np.zeros(wet.shape,dtype=bool), np.zeros(wet.shape,dtype=bool)
    for a,b in PAIRS:
        for high,low in ((a,b),(b,a)):
            touch = wet[high] & wet[low] & (surface[high] > surface[low])
            inlet[low] |= touch
            outlet[high] |= touch
    for level in np.unique(surface[wet & ~lakes]):
        labels, _ = ndi.label(wet & (surface == level),CARDINAL)
        for ident,region in enumerate(ndi.find_objects(labels),start=1):
            if region is None:
                continue
            mask = labels[region] == ident
            if lakes[region][mask].all() or unsafe_shore[region][mask].any():
                continue
            entries = mask & (inlet[region] | lakes[region])
            if entries.any():
                # Incoming falling water starts at flow level one; a lake
                # source starts at zero. Their remaining reaches differ.
                reached = ndi.binary_dilation(mask & inlet[region], structure=CARDINAL,
                            iterations=HORIZONTAL_REACH-1, mask=mask)
                reached |= ndi.binary_dilation(mask & lakes[region], structure=CARDINAL,
                            iterations=HORIZONTAL_REACH, mask=mask)
                long_reach = np.any(mask & ~reached)
            else:
                # At a headwater, measure the length back from its outlet;
                # a broad but short cross-section is not a long pool.
                entries = mask & outlet[region]
                if entries.any():
                    reached = ndi.binary_dilation(entries, structure=CARDINAL,
                                iterations=HORIZONTAL_REACH, mask=mask)
                    long_reach = np.any(mask & ~reached)
                else:
                    # A contained level body with no higher/lower wet neighbour
                    # is a closed pool, even when smaller than a mapped lake.
                    long_reach = True
            if long_reach:
                pools[region] |= mask & ~lakes[region]
    return pools


def surface_supply(stage, wet, sources):
    """Propagate surface supply; return 255 for unresolved wet columns."""
    levels = np.full(wet.shape,255,dtype=np.uint8)
    queue = []
    for z,x in np.argwhere(sources & wet):
        levels[z,x] = 0
        heapq.heappush(queue,(0,int(z),int(x)))
    h,w = wet.shape
    while queue:
        old,z,x = heapq.heappop(queue)
        if levels[z,x] != old:
            continue
        for dz,dx in ((1,0),(-1,0),(0,1),(0,-1)):
            nz,nx = z+dz,x+dx
            if not (0<=nz<h and 0<=nx<w) or not wet[nz,nx]:
                continue
            drop = int(stage[z,x])-int(stage[nz,nx])
            if drop < 0:
                continue
            nxt = 1 if drop > 0 else old+1
            if nxt <= HORIZONTAL_REACH and nxt < levels[nz,nx]:
                levels[nz,nx] = nxt
                heapq.heappush(queue,(nxt,nz,nx))
    return levels
