"""Per-column diagnostics stored in the product, not just truncated examples."""
import numpy as np

FLAGS = {name:1<<index for index,name in enumerate((
    'unplanned_water_contact','infeasible_section_level','stage_above_terrain_limit',
    'excessive_river_excavation','unsupported_drop','unsupplied_water',
    'bed_below_dimension','uncontained_bank','ambiguous_route_crossing'))}


def encode(masks, shape):
    result = np.zeros(shape,dtype=np.uint16)
    for name,mask in masks.items():
        result[mask] |= FLAGS[name]
    return result


def counts(flags):
    return {name:int(np.count_nonzero(flags & bit)) for name,bit in FLAGS.items()
            if np.any(flags & bit)}


def validate_report(product, report):
    measured = counts(product['diagnostic_flags'])
    if report.get('violations') != measured or report.get('ready_for_world_write') != (not measured):
        raise ValueError('physical report disagrees with stored diagnostic flags')
