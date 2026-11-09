"""Catalog-derived legacy costumes; availability and gameplay proof are separate."""
import json
from pathlib import Path


def registry(root, config):
    assets=config.get('pokemon_assets',{})
    if not assets.get('enabled'):return {}
    path=(Path(root)/assets['manifest']).resolve()
    if not path.is_relative_to(Path(root).resolve()):raise ValueError('Costume registry leaves project')
    imported=json.loads(path.read_text(encoding='utf8'))
    if imported.get('format')!=1:raise ValueError('Unsupported costume registry')
    return imported.get('costumes',{})


def validate(game, species, form, costume):
    if not costume:return
    if type(costume) is not int or f'{species}:{costume}' not in game.costumes:
        raise ValueError('Costume has no dependency-complete local catalog mapping')
    # Legacy costume prefab mappings are attached to the ordinary form. Never
    # silently combine one with regional, mega or independent costume forms.
    if form!=game.species[species]['form']:
        raise ValueError('Costume/form combination has not been mapped')
