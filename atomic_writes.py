"""Small dependency-free helpers for all-or-nothing block placement.

The painter keeps Amulet and numpy concerns at its boundary.  Keeping this
operation here makes the collision/region-ownership contract independently
testable even when the optional world runtime is unavailable.
"""


def write_air_atomic(items, x0, x1, z0, z1, y_min, y_max,
                     get_chunk, is_air):
    """Write an all-air multi-block object, or write nothing on failure.

    ``items`` yields ``(world_x, world_y, world_z, block_id)``.  Cells outside
    the x/z region are intentionally omitted because a neighbour owns them;
    y outside the build range rejects the whole object.  Every owned cell is
    checked before any mutation, so a collision or missing chunk cannot leave
    a partial object behind.
    """
    pending = []
    seen = set()
    for wx, wy, wz, bid in items:
        key = (int(wx), int(wy), int(wz))
        if key in seen:
            continue
        seen.add(key)
        if not (y_min <= key[1] <= y_max):
            return 0
        if not (x0 <= key[0] < x1 and z0 <= key[2] < z1):
            continue
        chunk = get_chunk(key[0] >> 4, key[2] >> 4)
        if chunk is None:
            return 0
        lx, lz = key[0] & 15, key[2] & 15
        if not is_air(int(chunk.blocks[lx, key[1], lz])):
            return 0
        pending.append((chunk, lx, key[1], lz, bid))
    if not pending:
        return 0
    for chunk, lx, wy, lz, bid in pending:
        chunk.blocks[lx, wy, lz] = bid
    return len(pending)
