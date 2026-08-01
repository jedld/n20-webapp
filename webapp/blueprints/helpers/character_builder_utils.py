"""Character builder utility functions extracted from app.py.

These functions support character creation, editing, and import workflows
including YAML resolution, image handling, ability score computation, and
campaign registration.

Design:
    All functions use ``runtime_state`` accessors for global state to avoid
    circular imports. No Flask app context is required at import time.
"""

import json
import os
import re
import base64
import io
import logging

from PIL import Image
import yaml

from natural20.image_gen.tokens import make_circular_token as _make_circular_token

from .runtime_state import (
    get_current_game,
    get_game_session,
    get_index_data,
    get_builder_only_mode,
    get_controllers,
)

logger = logging.getLogger(__name__)

PREBUILT_CHARACTER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'static',
    'assets',
    'prebuild_character',
)


# --------------------------------------------------------------------------- #
# Form parsing helpers
# --------------------------------------------------------------------------- #

def _normalize_language_key(lang):
  """Normalize a language id for comparison (lowercase, stripped)."""
  if lang is None:
    return ''
  return str(lang).strip().lower()


def collect_granted_languages(race_def=None, subrace_def=None, background_def=None):
  """Languages automatically granted by race, subrace, and background (not player picks)."""
  granted = set()
  for src in (race_def or {}, subrace_def or {}, background_def or {}):
    for lang in src.get('languages') or []:
      key = _normalize_language_key(lang)
      if key and key != 'none':
        granted.add(key)
  return granted


def filter_language_pool(pool, granted):
  """Drop languages already granted; preserve YAML casing and order."""
  if not pool:
    return []
  seen = set()
  filtered = []
  for lang in pool:
    key = _normalize_language_key(lang)
    if not key or key == 'none' or key in granted or key in seen:
      continue
    seen.add(key)
    filtered.append(lang)
  return filtered


def validate_background_language_selections(
    background_def,
    selections,
    race_def=None,
    subrace_def=None,
):
  """Validate background language picks against the filtered pool."""
  selections = list(selections or [])
  if not background_def:
    if selections:
      return False, 'Unexpected background language choices'
    return True, None

  granted = collect_granted_languages(race_def, subrace_def, background_def)
  choice_count = int(background_def.get('language_choice_count', 0))
  if choice_count <= 0:
    if selections:
      return False, 'Unexpected background language choices'
    return True, None

  pool = filter_language_pool(background_def.get('languages_pool') or [], granted)
  pool_set = set(pool)
  if len(pool) < choice_count:
    return False, 'Not enough background language options for this race/background combination.'
  if len(selections) != choice_count:
    plural = '' if choice_count == 1 else 's'
    return False, f'Choose {choice_count} background language{plural}.'
  if not all(sel in pool_set for sel in selections):
    return False, 'Invalid background language choices'
  return True, None


def _parse_json_list_form(form, key):
    """Parse a JSON-encoded list from a Flask form field."""
    val = form.get(key)
    if not val:
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return parsed
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_dict_form(form, key):
    """Parse a JSON-encoded dict from a Flask form field."""
    val = form.get(key)
    if not val:
        return {}
    try:
        parsed = json.loads(val)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_spells_by_level_form(form, legacy_level1=None):
    """Parse ``spells_by_level`` JSON from the builder form."""
    raw = _parse_json_dict_form(form, 'spells_by_level')
    spells_by_level = {}
    for key, spells in raw.items():
        try:
            level_num = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(spells, list):
            spells_by_level[level_num] = [str(spell) for spell in spells if spell]
    if legacy_level1:
        spells_by_level.setdefault(1, [])
        for spell in legacy_level1:
            if spell not in spells_by_level[1]:
                spells_by_level[1].append(spell)
    return spells_by_level


# --------------------------------------------------------------------------- #
# Ability score helpers
# --------------------------------------------------------------------------- #

def _ability_mod(score):
    """Return the ability modifier for a given ability score."""
    try:
        score = int(score)
        return (score - 10) // 2
    except (ValueError, TypeError):
        return 0


# --------------------------------------------------------------------------- #
# Spell choice helpers
# --------------------------------------------------------------------------- #

from .character_builder_levels import (
    BUILDER_MAX_LEVEL,
    apply_asi,
    apply_spell_selections,
    apply_subclass,
    asi_required,
    clamp_builder_level,
    collect_progression_features,
    compute_max_hp,
    list_subclass_options,
    spell_choice_caps,
    subclass_required,
    validate_asi_boosts,
)


def _spell_choice_caps(klass, level, ability, class_def):
    """Backward-compatible wrapper around ``spell_choice_caps``."""
    caps = spell_choice_caps(klass, level, ability, class_def)
    return {
        'cantrip_cap': caps.get('cantrip_cap', 0),
        'level1_cap': caps.get('spells_known_cap') or caps.get('spells_prepared_cap') or 0,
        'spellbook_cap': caps.get('spellbook_cap', 0),
        'spells_known_cap': caps.get('spells_known_cap', 0),
        'spells_prepared_cap': caps.get('spells_prepared_cap', 0),
        'max_spell_level': caps.get('max_spell_level', 0),
        'pact_slot_level': caps.get('pact_slot_level', 0),
        'slots_by_level': caps.get('slots_by_level', {}),
    }


# --------------------------------------------------------------------------- #
# Class and feat choices application
# --------------------------------------------------------------------------- #

def _apply_class_and_feat_choices(pc, klass, level, classes_def, selected_skills,
                                  selected_cantrips, selected_level1, selected_feats,
                                  spells_by_level=None, subclass_choice=None,
                                  asi_boosts=None, restrictions=None):
    """Apply class and feat choices to a player character dict.

    This function validates selections against the class definition, applies
    spell choice caps, and handles wizard spellbook construction.

    Parameters
    ----------
    pc : dict
        The player character dictionary to mutate.
    klass : str
        The class name (e.g. 'Wizard').
    level : int
        The character level.
    classes_def : dict
        The full class definitions mapping.
    selected_skills : list[str]
        Skill names chosen by the user.
    selected_cantrips : list[str]
        Cantrip names chosen by the user.
    selected_level1 : list[str]
        Level-1 spell names chosen by the user.
    selected_feats : list[str]
        Feat names chosen by the user.
    """
    cdef = (classes_def or {}).get(klass, {}) or {}
    if restrictions is not None:
        level = restrictions.clamp_level(level)
    else:
        level = clamp_builder_level(level)

    # Skills
    max_skills = int(cdef.get('available_skills_choices', 0) or 0)
    available_skills = cdef.get('available_skills', []) or []
    if max_skills and available_skills:
        valid_skills = [s for s in selected_skills if s in available_skills][:max_skills]
        if valid_skills:
            pc['skills'] = valid_skills

    # Spells (multi-level + legacy level-1 list)
    if spells_by_level is None:
        spells_by_level = {}
    if selected_level1:
        spells_by_level = dict(spells_by_level)
        spells_by_level.setdefault(1, [])
        for spell in selected_level1:
            if spell not in spells_by_level[1]:
                spells_by_level[1].append(spell)
    apply_spell_selections(
        pc,
        klass,
        level,
        cdef,
        selected_cantrips,
        spells_by_level,
    )

    features = collect_progression_features(cdef, level)
    if restrictions is not None:
        features = restrictions.filter_features(features)
    if features:
        pc['class_features'] = features

    if subclass_choice:
        if restrictions is None or restrictions.ability_allowed(subclass_choice):
            apply_subclass(pc, klass, subclass_choice, cdef, level)

    if asi_boosts:
        apply_asi(pc, asi_boosts)

    # Feats
    feat_options = cdef.get('feat_choices') or cdef.get('available_feats') or []
    if restrictions is not None:
        feat_options = [f for f in feat_options if restrictions.feat_allowed(f)]
    feat_count = int(cdef.get('feat_choices_count') or cdef.get('available_feats_choices') or 0)
    if feat_options:
        feats = [f for f in selected_feats if f in feat_options]
        if restrictions is not None:
            feats = [f for f in feats if restrictions.feat_allowed(f)]
        if feat_count > 0:
            feats = feats[:feat_count]
        pc['feats'] = list(dict.fromkeys(feats))
    elif selected_feats:
        pc['feats'] = list(dict.fromkeys(selected_feats))


# --------------------------------------------------------------------------- #
# Character YAML path resolution
# --------------------------------------------------------------------------- #

def _resolve_character_yaml_path(character_name):
    game_session = get_game_session()
    chars_dir = os.path.join(game_session.root_path, 'characters')
    if not os.path.isdir(chars_dir):
        return None

    direct_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(character_name or ''))
    if direct_name:
        direct_path = os.path.join(chars_dir, f"{direct_name}.yml")
        if os.path.exists(direct_path):
            return direct_path

    target = str(character_name or '').lower()
    for file_name in os.listdir(chars_dir):
        if not file_name.endswith('.yml'):
            continue
        file_path = os.path.join(chars_dir, file_name)
        try:
            with open(file_path, 'r', encoding='utf-8') as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            continue

        candidate_uid = str(data.get('entity_uid') or '').lower()
        candidate_name = str(data.get('name') or '').lower()
        if candidate_uid == target or candidate_name == target:
            return file_path

    return None


def _persist_outward_appearance_for_entity(entity, outward_appearance: str):
    """Store outward appearance on a live entity and its PC YAML when applicable."""
    if entity is None:
        return

    cleaned = str(outward_appearance or '').strip()
    props = getattr(entity, 'properties', None)
    if isinstance(props, dict):
        if cleaned:
            props['outward_appearance'] = cleaned
        else:
            props.pop('outward_appearance', None)
            props.pop('appearance', None)

    yml_path = _resolve_character_yaml_path(
        getattr(entity, 'entity_uid', None) or getattr(entity, 'name', None)
    )
    if not yml_path:
        return

    try:
        with open(yml_path, 'r', encoding='utf-8') as fh:
            pc = yaml.safe_load(fh) or {}
        if cleaned:
            pc['outward_appearance'] = cleaned
        else:
            pc.pop('outward_appearance', None)
            pc.pop('appearance', None)
        with open(yml_path, 'w', encoding='utf-8') as fh:
            yaml.safe_dump(pc, fh, sort_keys=False)
    except Exception:
        logger.exception('Failed to persist outward appearance to %s', yml_path)


def _can_edit_character(character_name):
    from flask import session as flask_session
    from .pvp import selectable_character_entry
    from .template_globals import controller_of as _controller_of
    from .auth_utils import roles_for_username

    if get_builder_only_mode():
        return True
    if 'dm' in roles_for_username(flask_session.get('username')):
        return True
    username = flask_session.get('username')
    if not username:
        return False
    if _controller_of(character_name, username):
        return True

    # Selection-flow editing: allow a logged-in player to edit a selectable
    # character before confirming ownership, as long as another user has not
    # already claimed it.
    entry = selectable_character_entry(character_name)
    if entry is not None:
        for controller in get_controllers():
            if controller.get('entity_uid') != character_name:
                continue
            controllers = controller.get('controllers') or []
            if controllers and username not in controllers:
                return False
        return True

    return False


# --------------------------------------------------------------------------- #
# Image processing helpers
# --------------------------------------------------------------------------- #
# Circular token stamping lives in ``natural20.image_gen.tokens`` so campaign
# asset generation and the character builder share one implementation.


def _decode_data_url_image(data_url):
    """Decode a ``data:image/...;base64,...`` URL into a PIL Image, or None.

    Parameters
    ----------
    data_url : str
        A data URL containing base64-encoded image bytes.

    Returns
    -------
    Image.Image or None
    """
    try:
        if not isinstance(data_url, str):
            return None
        match = re.match(r'data:image/[^;]+;base64,(.+)', data_url)
        if not match:
            return None
        img_data = base64.b64decode(match.group(1))
        return Image.open(io.BytesIO(img_data))
    except Exception:
        return None


def _resolve_prebuilt_character_image(name):
    """Return absolute path to a prebuilt portrait under ``PREBUILT_CHARACTER_DIR``.

  Strict allow-list against the gallery directory to avoid path traversal.
    """
    if not name:
        return None
    safe = os.path.basename(str(name))
    if safe != str(name):
        return None
    candidate = os.path.join(PREBUILT_CHARACTER_DIR, safe)
    if not os.path.isfile(candidate):
        return None
    return candidate


def _save_character_images(entity_uid, assets_dir, profile_pil=None, token_pil=None):
    """Save profile and token images for a character to disk.

    Profile: ``assets/characters/<uid>.png``
    Token: ``assets/token_<uid>.png`` (auto-generated from profile when needed).
    """
    if profile_pil is None and token_pil is None:
        return

    profiles_dir = os.path.join(assets_dir, 'characters')
    os.makedirs(profiles_dir, exist_ok=True)

    if profile_pil is not None:
        profile_path = os.path.join(profiles_dir, f'{entity_uid}.png')
        profile_pil.convert('RGBA').save(profile_path, format='PNG')

    if token_pil is None and profile_pil is not None:
        token_pil = _make_circular_token(profile_pil)

    if token_pil is not None:
        if token_pil.size != (256, 256):
            token_pil = _make_circular_token(token_pil)
        token_path = os.path.join(assets_dir, f'token_{entity_uid}.png')
        token_pil.convert('RGBA').save(token_path, format='PNG')


def _load_character_image_from_request(req, file_field, prebuilt_field, data_url_field=None):
    """Extract a character image from an upload request.

    Tries the following sources in order:
    1. Uploaded file (``file_field``)
    2. Prebuilt image reference (``prebuilt_field``)
    3. Data URL (``data_url_field``)

    Parameters
    ----------
    req : flask.Request
        The incoming Flask request.
    file_field : str
        Form field name for the file upload.
    prebuilt_field : str
        Form field name for a prebuilt image reference.
    data_url_field : str or None
        Optional form field name for a data URL.

    Returns
    -------
    Image.Image or None
    """
    if data_url_field:
        data_url = req.form.get(data_url_field)
        img = _decode_data_url_image(data_url)
        if img is not None:
            return img

    if file_field in req.files:
        file_item = req.files[file_field]
        if file_item and file_item.filename:
            try:
                return Image.open(file_item.stream)
            except Exception:
                pass

    prebuilt_name = req.form.get(prebuilt_field)
    if prebuilt_name:
        path = _resolve_prebuilt_character_image(prebuilt_name)
        if path:
            try:
                return Image.open(path)
            except Exception:
                pass

    return None


# --------------------------------------------------------------------------- #
# Campaign registration
# --------------------------------------------------------------------------- #

def _register_new_character_in_campaign(pc, safe_name):
    """Place a new PC on the default map and expose it on the selection screen.

    Parameters
    ----------
    pc : dict
        The player character dictionary.
    safe_name : str
        The sanitized character name.
    """
    from natural20.player_character import PlayerCharacter

    current_game = get_current_game()
    game_session = get_game_session()
    index_data = get_index_data()

    entity_uid = pc.get('entity_uid') or safe_name.lower()
    sheet_path = f'characters/{safe_name}.yml'
    portrait_file = pc.get('profile_image') or f'characters/{entity_uid}.png'

    if get_builder_only_mode():
        return

    # Campaigns with deferred player spawn place PCs at player_spawn_points only
    # after the player confirms selection on /character_selection.
    if not getattr(current_game, 'defer_player_spawn', False):
        try:
            pc_entity = PlayerCharacter.load(game_session, sheet_path)
            target_map = game_session.maps.get('index') or next(iter(game_session.maps.values()))
            width, height = target_map.size
            pos = None
            for y in range(height):
                for x in range(width):
                    if not target_map.entity_at(x, y):
                        pos = (x, y)
                        break
                if pos:
                    break
            if not pos:
                pos = (0, 0)
            target_map.add(pc_entity, pos[0], pos[1], group='a')
        except Exception:
            logger.exception('Failed to place new character on map')

    try:
        index_json_path = os.path.join(game_session.root_path, 'index.json')
        if os.path.exists(index_json_path):
            with open(index_json_path, 'r') as jf:
                idx = json.load(jf)
        else:
            idx = {}
        selectable = idx.get('selectable_characters') or []
        lower = str(entity_uid).lower()
        if not any(c.get('name', '').lower() == lower for c in selectable):
            selectable.append({
                'name': lower,
                'file': portrait_file,
                'description': pc.get('description', lower),
                'sheet': sheet_path,
                'overrides': {'entity_uid': lower},
            })
        idx['selectable_characters'] = selectable
        with open(index_json_path, 'w') as jf:
            json.dump(idx, jf, indent=2)
        try:
            index_data['selectable_characters'] = selectable
        except Exception:
            logger.exception('Failed to update in-memory selectable_characters')
    except Exception:
        logger.exception('Failed to update index.json with new character')
