"""Condition-scoped analysis views without altering original recordings."""
import numpy as np


def condition_dataset(dataset, condition=None):
    """Select one comparable recipe, retaining physical pixel IDs and geometry.

    Source rows remain available to the caller. Each condition supplies its own
    waveform metadata to cycle extraction, map frames, movies and smoothing.
    """
    from .analysis_core import AnalysisDataset, NumericRows, AnalysisError
    recipes = (dataset.metadata.get('parameters') or {}).get('recipes', [])
    if not recipes:
        return dataset
    if condition is None:
        measured = set(dataset.column("scan_pixel")) if "scan_pixel" in dataset.columns else set()
        condition = next((p["condition_id"] for p in (dataset.metadata.get("scan_grid") or {}).get("pixels", [])
                          if p["scan_pixel"] in measured), 0)
    if not isinstance(condition, int) or not 0 <= condition < len(recipes):
        raise AnalysisError('Unknown combinatorial condition.')
    if 'scan_pixel' not in dataset.columns:
        raise AnalysisError('Combinatorial recordings require scan_pixel identifiers.')
    entries = (dataset.metadata.get('scan_grid') or {}).get('pixels', [])
    selected = [p for p in entries if p.get('condition_id') == condition]
    ids = [p['scan_pixel'] for p in selected]
    rows = NumericRows(dataset.columns, dataset.rows.matrix[np.isin(dataset.column('scan_pixel'), ids)])
    if not len(rows):
        raise AnalysisError("No recorded landings for this condition; select another condition.")
    recipe = recipes[condition]
    parameters = {**dataset.metadata['parameters'], **recipe}
    metadata = {**dataset.metadata, 'parameters': parameters,
                'analysis_condition': {'id': condition, 'name': recipe['name']},
                'scan_grid': {**dataset.metadata.get('scan_grid', {}), 'pixels': selected}}
    return AnalysisDataset(dataset.path, dataset.columns, rows, metadata)
