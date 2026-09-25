"""Serializable measurement recipes and deterministic spatial assignments.

Only electrochemical program fields may vary. Motion, contact detection and
instrument calibration remain shared by all landings in a scan.
"""
from copy import copy
import itertools
import random

CV_FIELDS = ("cv_start_v", "cv_vertex1_v", "cv_vertex2_v", "cv_scan_rate_v_s", "cycles", "waveform")
IT_FIELDS = ("initial_potential_v", "initial_hold_s", "step_potential_v", "step_hold_s", "return_potential_v", "return_hold_s", "cycles")


def program_fields(params):
    """Return the allowlist for this scan's waveform family."""
    return CV_FIELDS if hasattr(params, "cv_start_v") else IT_FIELDS


def resolve(params, point):
    """Resolve a complete recipe without mutating the running scan.

Assignments are stored in physical row-major order, not serpentine order.
The orientation marker repeats the final array landing's recipe.
Runtime retraction/marker dictionaries remain shared with the parent.
"""
    if not params.recipes:
        return params
    row, col = divmod(min(point, params.point_count - 1), params.x_points)
    if params.serpentine and row % 2:
        col = params.x_points - 1 - col
    recipe = params.recipes[params.recipe_assignment[row * params.x_points + col]]
    result = copy(params)
    result.recipes = []
    result.recipe_assignment = []
    for key in program_fields(params):
        setattr(result, key, recipe[key])
    return result


def validate_recipes(params, settings):
    """Validate every complete recipe before any instrument command is sent."""
    if not params.recipes:
        return ["Recipe assignment requires recipes."] if params.recipe_assignment else []
    if len(params.recipes) > 64:
        return ["Use at most 64 recipes per scan."]
    allowed = set(program_fields(params)) | {"name"}
    errors = []
    names = set()
    for index, recipe in enumerate(params.recipes):
        if not isinstance(recipe, dict) or set(recipe) != allowed:
            errors.append(f"Recipe {index + 1}: provide name and all program fields, with no other overrides.")
            continue
        name = recipe["name"]
        if not isinstance(name, str) or not name.strip() or name in names:
            errors.append(f"Recipe {index + 1}: names must be nonempty and unique.")
        names.add(str(name))
        if type(recipe["cycles"]) is not int:
            errors.append(f"Recipe {index + 1}: cycles must be an integer.")
            continue
        candidate = copy(params)
        candidate.recipes, candidate.recipe_assignment = [], []
        for key in program_fields(params):
            setattr(candidate, key, recipe[key])
        if hasattr(candidate, "cv_start_v"):
            values = [candidate.cv_start_v, candidate.cv_vertex1_v]
            if candidate.waveform == "CV": values.append(candidate.cv_vertex2_v)
            if all(isinstance(v, (int, float)) for v in values):
                candidate.map_potential_v = min(values)
        try:
            errors.extend(f"Recipe {index + 1}: {error}" for error in candidate.validate(settings))
        except (TypeError, ValueError, OverflowError):
            errors.append(f"Recipe {index + 1}: invalid numeric values.")
    assignment = params.recipe_assignment
    if len(assignment) != params.point_count or any(type(i) is not int or not 0 <= i < len(params.recipes) for i in assignment):
        errors.append("Assign exactly one valid recipe to each physical grid position.")
    return errors


def assign(nx, ny, count, mode, rows_per_recipe=1, seed=0):
    """Build balanced, reproducible assignments in physical row-major order."""
    if min(nx, ny, count, rows_per_recipe) < 1:
        raise ValueError("Grid, recipes and rows per recipe must be positive.")
    if mode == "Row blocks":
        result = [(row // rows_per_recipe) % count for row in range(ny) for _ in range(nx)]
    elif mode in {"Interleaved", "Randomized"}:
        result = [i % count for i in range(nx * ny)]
        if mode == "Randomized":
            random.Random(seed).shuffle(result)
    else:
        raise ValueError("Unknown assignment mode.")
    return result


def factorial(base, first, values, second, other):
    """Expand two independent program factors into fully specified recipes."""
    if first == second:
        raise ValueError("Choose two different factors.")
    if not values or not other or len(values) * len(other) > 64:
        raise ValueError("Provide between 1 and 64 combinations.")
    return [{**base, first: a, second: b, "name": f"Condition {i + 1}"}
            for i, (a, b) in enumerate(itertools.product(values, other))]


def pixel_parameters(metadata, pixel):
    """Return per-hop waveform metadata for analysis; preserve legacy files."""
    parameters = metadata.get("parameters") or {}
    for entry in (metadata.get("scan_grid") or {}).get("pixels", []):
        if entry.get("scan_pixel") == pixel and "measurement_program" in entry:
            return {**parameters, **entry["measurement_program"]}
    return parameters


def estimated_duration(params):
    """Add per-recipe acquisition times to the common travel-time estimate."""
    base = copy(params)
    base.recipes, base.recipe_assignment = [], []
    def duration(p):
        """Return only the electrochemical program duration."""
        if hasattr(p, 'cv_start_v'):
            distance = abs(p.cv_vertex1_v-p.cv_start_v)
            if p.waveform != 'LSV':
                distance += abs(p.cv_vertex2_v-p.cv_vertex1_v)+abs(p.cv_start_v-p.cv_vertex2_v)
            return distance * p.cycles / p.cv_scan_rate_v_s
        return sum(t for _, t, _ in p.it_steps())
    return (base.estimated_known_duration_s() - params.execution_point_count * duration(base)
            + sum(duration(params.for_point(i)) for i in range(params.execution_point_count)))
