"""Character blueprint — builder, editor, import, and journal routes.

Extracted from webapp/app.py.
"""
import os
import re

import yaml
from flask import Blueprint, request, jsonify, session, redirect, url_for, render_template

from natural20.player_character import PlayerCharacter
from natural20.utils.char_class_spells import spell_availability_map

from webapp.dndbeyond_import import (
    import_character_from_dndbeyond,
    parse_character_id_from_url,
    prepare_imported_pc_dict,
)
from .helpers.auth_utils import logged_in, user_role
from .helpers.pvp import ensure_character_entity_loaded
from .helpers.runtime_state import (
    get_current_game,
    get_game_session,
    get_title,
    get_logger,
    get_builder_only_mode,
    get_socketio,
)
from .helpers.character_builder_utils import (
    PREBUILT_CHARACTER_DIR,
    _parse_json_list_form,
    _parse_json_dict_form,
    _parse_spells_by_level_form,
    _apply_class_and_feat_choices,
    _resolve_character_yaml_path,
    _can_edit_character,
    _load_character_image_from_request,
    _save_character_images,
    _register_new_character_in_campaign,
    _persist_outward_appearance_for_entity,
)
from .helpers.character_builder_levels import (
    BUILDER_MAX_LEVEL,
    asi_required,
    clamp_builder_level,
    compute_max_hp,
    extract_subclass_choice,
    list_subclass_options,
    subclass_required,
    validate_asi_boosts,
)
from .helpers.journal_utils import (
    _journal_owner_check,
    _serialize_journal,
    _persist_journal_change,
    _log_journal_entry_to_campaign_db,
    _emit_journal_update,
    unread_journal_summary_for_username,
)

character_bp = Blueprint('character', __name__)


@character_bp.route('/character_builder/prebuilt_images', methods=['GET'])
def list_prebuilt_character_images():
    """Return the gallery of prebuilt character portrait images."""
    if not logged_in():
        return jsonify(error='Not logged in'), 401
    items = []
    if os.path.isdir(PREBUILT_CHARACTER_DIR):
        for name in sorted(os.listdir(PREBUILT_CHARACTER_DIR)):
            if not name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                continue
            items.append({
                'name': name,
                'url': url_for('static', filename=f'assets/prebuild_character/{name}'),
            })
    return jsonify(images=items)


@character_bp.route('/character_builder/items', methods=['GET'])
def character_builder_items():
    """Return available items (weapons + equipment) for the character builder inventory manager."""
    if not logged_in():
        return jsonify(error='Not logged in'), 401

    try:
        weapons = get_game_session().load_weapons() or {}
        equipment = get_game_session().load_all_equipments() or {}
        items = {}

        # Merge weapons and equipment into a single catalog
        for key, data in weapons.items():
            items[key] = {
                'id': key,
                'name': data.get('name', key.replace('_', ' ').title()),
                'type': data.get('type', data.get('subtype', 'weapon')),
                'cost': data.get('cost', ''),
                'weight': data.get('weight', 0),
                'category': 'weapon',
            }

        for key, data in equipment.items():
            if key not in items:  # Don't overwrite weapons
                items[key] = {
                    'id': key,
                    'name': data.get('name', key.replace('_', ' ').title()),
                    'type': data.get('type', data.get('subtype', 'equipment')),
                    'cost': data.get('cost', ''),
                    'weight': data.get('weight', 0),
                    'category': 'equipment',
                }

        # Add campaign-specific custom items if configured in game.yml
        game_props = getattr(get_game_session(), 'game_properties', None) or {}
        custom_items = game_props.get('inventory_items') or {}
        allow_custom = game_props.get('allow_custom_inventory', True)

        if allow_custom and custom_items:
            for key, data in custom_items.items():
                if isinstance(data, dict):
                    items[key] = {
                        'id': key,
                        'name': data.get('name', key.replace('_', ' ').title()),
                        'type': data.get('type', 'custom'),
                        'cost': data.get('cost', ''),
                        'weight': data.get('weight', 0),
                        'category': 'custom',
                    }
                else:
                    # Simple string value treated as item name
                    items[key] = {
                        'id': key,
                        'name': str(data) if not isinstance(data, str) else data,
                        'type': 'custom',
                        'cost': '',
                        'weight': 0,
                        'category': 'custom',
                    }

        return jsonify(items=items, allow_custom_inventory=allow_custom)
    except Exception as e:
        get_logger().exception('Failed to load character builder items')
        return jsonify(error='Failed to load items'), 500


@character_bp.route('/character_builder', methods=['GET'])
def character_builder():
    if not logged_in():
        return redirect(url_for('auth.login'))

    try:
        races = get_game_session().load_races()
        classes = get_game_session().load_classes()
        backgrounds = get_game_session().load_backgrounds()
        equipment_packs = get_game_session().load_equipment_packs()
        spell_availability = spell_availability_map(get_game_session(), classes)
        return render_template('character_builder.html',
                               title=get_title(),
                               races=races,
                               classes=classes,
                               backgrounds=backgrounds,
                               equipment_packs=equipment_packs,
                               spell_availability=spell_availability,
                               edit_mode=False,
                               cancel_url='/')
    except Exception as e:
        get_logger().exception('Failed to load character builder')
        return jsonify(error='Failed to load character builder'), 500


@character_bp.route('/character_editor/<character_name>', methods=['GET'])
def character_editor(character_name):
    if not logged_in():
        return redirect(url_for('auth.login'))
    if not _can_edit_character(character_name):
        return jsonify(error='Forbidden'), 403

    yml_path = _resolve_character_yaml_path(character_name)
    if not yml_path:
        return jsonify(error='Character not found'), 404

    try:
        with open(yml_path, 'r', encoding='utf-8') as fh:
            pc = yaml.safe_load(fh) or {}

        races = get_game_session().load_races() or {}
        classes = get_game_session().load_classes() or {}
        equipment_packs = get_game_session().load_equipment_packs() or {}
        backgrounds = get_game_session().load_backgrounds() or {}

        class_map = pc.get('classes') or {}
        klass = next(iter(class_map.keys()), None)
        level = int(class_map.get(klass, pc.get('level', 1))) if klass else int(pc.get('level', 1) or 1)

        spell_list = ((classes.get(klass or '', {}) or {}).get('spell_list') or {})
        can_list = set(spell_list.get('cantrip') or [])
        lvl1_list = set(spell_list.get('level_1') or [])
        prepared = list(pc.get('prepared_spells') or [])
        spells_by_level = {}
        for key, spells in spell_list.items():
            if not key.startswith('level_'):
                continue
            try:
                spell_level = int(key.split('_', 1)[1])
            except (ValueError, IndexError):
                continue
            pool = set(spells or [])
            picked = [s for s in prepared if s in pool]
            if picked:
                spells_by_level[spell_level] = picked

        edit_character = {
            'name': pc.get('name', ''),
            'pronoun': pc.get('pronoun', ''),
            'race': pc.get('race', ''),
            'subrace': pc.get('subrace', ''),
            'background': pc.get('background', ''),
            'klass': klass or '',
            'level': level,
            'ability': pc.get('ability', {}),
            'skills': list(pc.get('skills') or []),
            'cantrips': [s for s in prepared if s in can_list],
            'level1_spells': [s for s in prepared if s in lvl1_list],
            'spells_by_level': spells_by_level,
            'subclass': extract_subclass_choice(klass, pc),
            'feats': list(pc.get('feats') or []),
            'inventory': list(pc.get('inventory') or []),
            'outward_appearance': pc.get('outward_appearance') or pc.get('appearance') or '',
        }

        cancel_url = request.args.get('next') or '/'
        spell_availability = spell_availability_map(get_game_session(), classes)
        return render_template(
            'character_builder.html',
            title=get_title(),
            races=races,
            classes=classes,
            backgrounds=backgrounds,
            equipment_packs=equipment_packs,
            spell_availability=spell_availability,
            edit_mode=True,
            edit_character=edit_character,
            editing_character=character_name,
            cancel_url=cancel_url,
        )
    except Exception:
        get_logger().exception('Failed to load character editor')
        return jsonify(error='Failed to load character editor'), 500


@character_bp.route('/update_character/<character_name>', methods=['POST'])
def update_character(character_name):
    if not logged_in():
        return jsonify(error='Not logged in'), 401
    if not _can_edit_character(character_name):
        return jsonify(error='Forbidden'), 403

    yml_path = _resolve_character_yaml_path(character_name)
    if not yml_path:
        return jsonify(error='Character not found'), 404

    try:
        with open(yml_path, 'r', encoding='utf-8') as fh:
            pc = yaml.safe_load(fh) or {}

        class_map = pc.get('classes') or {}
        klass = next(iter(class_map.keys()), None)
        if not klass:
            return jsonify(error='Character class is missing'), 400
        level = int(class_map.get(klass, pc.get('level', 1) or 1))

        selected_skills = _parse_json_list_form(request.form, 'skills')
        selected_cantrips = _parse_json_list_form(request.form, 'cantrips')
        selected_level1 = _parse_json_list_form(request.form, 'level1_spells')
        spells_by_level = _parse_spells_by_level_form(request.form, selected_level1)
        selected_feats = _parse_json_list_form(request.form, 'feats')
        subclass_choice = (request.form.get('subclass') or '').strip()
        asi_boosts = _parse_json_dict_form(request.form, 'asi_boosts')
        background_key = (request.form.get('background') or '').strip()
        background_language_selections = _parse_json_list_form(request.form, 'background_languages')
        backgrounds_def = get_game_session().load_backgrounds() or {}
        background_def = backgrounds_def.get(background_key) if background_key else None
        if background_key and background_def is None:
            return jsonify(error='Unknown background selection'), 400

        classes_def = get_game_session().load_classes() or {}
        cdef = classes_def.get(klass, {}) or {}
        subclass_options = list_subclass_options(klass, cdef)
        if subclass_required(level) and subclass_options and not subclass_choice:
            return jsonify(error='Choose a subclass for this level.'), 400
        if subclass_choice and subclass_options and subclass_choice not in subclass_options:
            return jsonify(error='Invalid subclass selection.'), 400
        if asi_required(level):
            ok, asi_error = validate_asi_boosts(asi_boosts)
            if not ok:
                return jsonify(error=asi_error), 400

        class_skill_pool = set(cdef.get('available_skills') or [])
        existing_skills = list(pc.get('skills') or [])
        non_class_skills = [s for s in existing_skills if s not in class_skill_pool]

        _apply_class_and_feat_choices(
            pc,
            klass,
            level,
            classes_def,
            selected_skills,
            selected_cantrips,
            selected_level1,
            selected_feats,
            spells_by_level=spells_by_level,
            subclass_choice=subclass_choice,
            asi_boosts=asi_boosts,
        )

        if non_class_skills:
            pc.setdefault('skills', [])
            for skill in non_class_skills:
                if skill not in pc['skills']:
                    pc['skills'].append(skill)

        pronoun = (request.form.get('pronoun') or '').strip()
        if pronoun:
            pc['pronoun'] = pronoun
        else:
            pc.pop('pronoun', None)

        outward_appearance = (request.form.get('outward_appearance') or '').strip()
        if outward_appearance:
            pc['outward_appearance'] = outward_appearance
        else:
            pc.pop('outward_appearance', None)
            pc.pop('appearance', None)

        if background_key and background_def:
            pc['background'] = background_key
            for skill in background_def.get('skill_proficiencies', []):
                pc.setdefault('skills', [])
                if skill not in pc['skills']:
                    pc['skills'].append(skill)
            for tool in background_def.get('tool_proficiencies', []):
                pc.setdefault('tool_proficiencies', [])
                if tool not in pc['tool_proficiencies']:
                    pc['tool_proficiencies'].append(tool)
            for lang in background_def.get('languages', []):
                if lang and str(lang).lower() != 'none':
                    pc.setdefault('languages', [])
                    if lang not in pc['languages']:
                        pc['languages'].append(lang)
            if background_language_selections:
                pc['languages'] = list(dict.fromkeys(
                    list(pc.get('languages') or []) + background_language_selections
                ))
        elif not background_key:
            pc.pop('background', None)

        # Update inventory from character builder
        inventory_json = (request.form.get('inventory') or '').strip()
        if inventory_json:
            try:
                import json as _json
                custom_inventory = _json.loads(inventory_json)
                if custom_inventory:
                    pc['inventory'] = custom_inventory
            except Exception:
                get_logger().exception('Failed to parse custom inventory for update')

        with open(yml_path, 'w', encoding='utf-8') as fh:
            yaml.safe_dump(pc, fh, sort_keys=False)

        entity_uid = str(pc.get('entity_uid') or character_name)
        try:
            assets_dir = os.path.join(get_game_session().root_path, 'assets')
            os.makedirs(assets_dir, exist_ok=True)
            profile_pil = _load_character_image_from_request(
                request,
                file_field='profile_image',
                prebuilt_field='profile_prebuilt',
            )
            token_pil = _load_character_image_from_request(
                request,
                file_field='token_image',
                prebuilt_field='token_prebuilt',
                data_url_field='token_image_data',
            )
            if profile_pil is not None or token_pil is not None:
                _save_character_images(entity_uid, assets_dir, profile_pil=profile_pil, token_pil=token_pil)
                pc['token_image'] = f'token_{entity_uid}.png'
                pc['profile_image'] = f'characters/{entity_uid}.png'
                with open(yml_path, 'w', encoding='utf-8') as fh:
                    yaml.safe_dump(pc, fh, sort_keys=False)
        except Exception:
            get_logger().exception('Failed to save character images during update')

        entity = get_current_game().get_entity_by_uid(entity_uid)
        if entity is not None and isinstance(getattr(entity, 'properties', None), dict):
            for key in ('skills', 'prepared_spells', 'spellbook', 'feats', 'pronoun', 'outward_appearance', 'token_image', 'profile_image'):
                if key in pc:
                    entity.properties[key] = pc.get(key)
                elif key in entity.properties:
                    entity.properties.pop(key, None)
            if 'outward_appearance' not in pc:
                entity.properties.pop('outward_appearance', None)
                entity.properties.pop('appearance', None)
        # Refresh entity inventory if loaded in session
        if entity is not None and hasattr(entity, 'inventory') and 'inventory' in pc:
            try:
                entity.load_inventory()
            except Exception:
                pass

        redirect_to = request.form.get('next') or '/'
        return jsonify(status='ok', redirect=redirect_to)
    except Exception:
        get_logger().exception('Failed to update character')
        return jsonify(error='Failed to update character'), 500


@character_bp.route('/update_entity_outward_appearance', methods=['POST'])
def update_entity_outward_appearance():
    """Update optional outward appearance text for a PC (YAML) or NPC (live entity)."""
    if not logged_in():
        return jsonify(error='Not logged in'), 401

    payload = request.get_json(silent=True) or {}
    entity_id = (payload.get('entity_id') or '').strip()
    outward_appearance = payload.get('outward_appearance')
    if outward_appearance is None:
        return jsonify(error='outward_appearance is required'), 400
    if not entity_id:
        return jsonify(error='entity_id is required'), 400

    entity = get_current_game().get_entity_by_uid(entity_id)
    if entity is None:
        return jsonify(error='Entity not found'), 404

    username = session.get('username')
    is_dm = 'dm' in user_role()
    owners = []
    try:
        from .helpers.template_globals import entity_owners as _entity_owners
        owners = list(_entity_owners(entity) or [])
    except Exception:
        owners = []

    if not is_dm and username not in owners:
        return jsonify(error='Forbidden'), 403

    try:
        if getattr(entity, 'is_npc', lambda: False)() and not is_dm:
            return jsonify(error='Only DMs can edit NPC appearance here'), 403

        cleaned = str(outward_appearance or '').strip()
        if getattr(entity, 'is_npc', lambda: False)():
            if cleaned:
                entity.properties['outward_appearance'] = cleaned
            else:
                entity.properties.pop('outward_appearance', None)
                entity.properties.pop('appearance', None)
        else:
            _persist_outward_appearance_for_entity(entity, cleaned)

        get_socketio().emit('message', {'type': 'refresh_map'})
        return jsonify(success=True, outward_appearance=cleaned)
    except Exception:
        get_logger().exception('Failed to update outward appearance for %s', entity_id)
        return jsonify(error='Failed to update outward appearance'), 500


# _register_new_character_in_campaign is imported from webapp.blueprints.helpers.character_builder_utils


@character_bp.route('/character_builder/import_dndbeyond', methods=['POST'])
def import_dndbeyond_character():
  """Import a character sheet from a D&D Beyond URL and save it as campaign YAML."""
  if not logged_in():
    return jsonify(error='Not logged in'), 401

  payload = request.get_json(silent=True) or {}
  url = (payload.get('url') or request.form.get('url') or '').strip()
  cobalt_token = (
      (payload.get('cobalt_token') or request.form.get('cobalt_token') or '').strip()
      or os.environ.get('DND_BEYOND_COBALT_TOKEN')
      or os.environ.get('COBALT_SESSION')
      or None
  )

  character_id = parse_character_id_from_url(url)
  if character_id is None:
    return jsonify(
        error='Paste a D&D Beyond character sheet URL like '
              'https://www.dndbeyond.com/characters/12345678'
    ), 400

  try:
    pc, import_warnings = import_character_from_dndbeyond(
      character_id,
      cobalt_token=cobalt_token,
    )
  except RuntimeError as exc:
    return jsonify(error=str(exc)), 500
  except Exception as exc:
    get_logger().exception('D&D Beyond import failed for character %s', character_id)
    message = str(exc).strip() or 'Failed to import character from D&D Beyond'
    if '401' in message or '403' in message or 'Unauthorized' in message:
      message = (
          'Could not access that character. For private sheets, paste your '
          'CobaltSession cookie value (browser devtools → Application → Cookies '
          '→ dndbeyond.com → CobaltSession) or set DND_BEYOND_COBALT_TOKEN on the server.'
      )
    return jsonify(error=message), 502

  pc, safe_name = prepare_imported_pc_dict(pc)
  if not pc.get('name'):
    return jsonify(error='Imported character has no name'), 400

  races_def = get_game_session().load_races() or {}
  if pc.get('race') and pc['race'] not in races_def:
    return jsonify(
        error=f"Race '{pc.get('race')}' is not available in this campaign"
    ), 400

  classes_def = get_game_session().load_classes() or {}
  for klass in (pc.get('classes') or {}):
    if klass not in classes_def:
      return jsonify(
          error=f"Class '{klass}' is not available in this campaign"
      ), 400

  backgrounds_def = get_game_session().load_backgrounds() or {}
  bg = pc.get('background')
  if bg and bg not in backgrounds_def:
    return jsonify(
        error=f"Background '{bg}' is not available in this campaign"
    ), 400

  chars_dir = os.path.join(get_game_session().root_path, 'characters')
  os.makedirs(chars_dir, exist_ok=True)
  yml_path = os.path.join(chars_dir, f'{safe_name}.yml')
  if os.path.exists(yml_path):
    return jsonify(error='A character with that name already exists'), 400

  with open(yml_path, 'w', encoding='utf-8') as fh:
    yaml.safe_dump(pc, fh, sort_keys=False, allow_unicode=True)

  _register_new_character_in_campaign(pc, safe_name)

  if get_builder_only_mode():
    redirect_to = url_for('character.character_builder')
  else:
    redirect_to = '/character_selection' if 'dm' not in user_role() else '/'

  return jsonify(
      status='ok',
      redirect=redirect_to,
      character_name=pc.get('name'),
      character_file=f'characters/{safe_name}.yml',
      warnings=import_warnings,
  )


@character_bp.route('/create_character', methods=['POST'])
def create_character():
    if not logged_in():
        return jsonify(error="Not logged in"), 401

    try:
        name = (request.form.get('name') or '').strip()
        pronoun = (request.form.get('pronoun') or '').strip()
        outward_appearance = (request.form.get('outward_appearance') or '').strip()
        race = (request.form.get('race') or '').strip()
        subrace = (request.form.get('subrace') or '').strip()
        klass = (request.form.get('klass') or '').strip()
        try:
            level = clamp_builder_level(request.form.get('level') or 1)
        except Exception:
            level = 1

        subclass_choice = (request.form.get('subclass') or '').strip()
        asi_boosts = _parse_json_dict_form(request.form, 'asi_boosts')

        try:
            ability = {
                'str': int(request.form.get('str') or 8),
                'dex': int(request.form.get('dex') or 8),
                'con': int(request.form.get('con') or 8),
                'int': int(request.form.get('int') or 8),
                'wis': int(request.form.get('wis') or 8),
                'cha': int(request.form.get('cha') or 8),
            }
        except ValueError:
            return jsonify(error='Invalid ability values'), 400

        if not name or not race or not klass:
            return jsonify(error='Name, race, and class are required'), 400

        classes_def = get_game_session().load_classes() or {}
        cdef = classes_def.get(klass, {}) or {}
        subclass_options = list_subclass_options(klass, cdef)
        if subclass_required(level) and subclass_options and not subclass_choice:
            return jsonify(error='Choose a subclass for this level.'), 400
        if subclass_choice and subclass_options and subclass_choice not in subclass_options:
            return jsonify(error='Invalid subclass selection.'), 400
        if asi_required(level):
            ok, asi_error = validate_asi_boosts(asi_boosts)
            if not ok:
                return jsonify(error=asi_error), 400

        races_def = get_game_session().load_races() or {}
        race_def = races_def.get(race)
        if race_def is None:
            return jsonify(error='Unknown race selection'), 400
        subrace_def = {}
        if subrace:
            subrace_def = (race_def.get('subrace') or {}).get(subrace, {})

        selected_skills = _parse_json_list_form(request.form, 'skills')
        selected_cantrips = _parse_json_list_form(request.form, 'cantrips')
        selected_level1 = _parse_json_list_form(request.form, 'level1_spells')
        spells_by_level = _parse_spells_by_level_form(request.form, selected_level1)
        selected_feats = _parse_json_list_form(request.form, 'feats')
        race_bonus_map = _parse_json_dict_form(request.form, 'race_ability_bonuses')
        race_skill_selections = _parse_json_list_form(request.form, 'race_skills')
        race_language_selections = _parse_json_list_form(request.form, 'race_languages')

        # Background handling
        background_key = (request.form.get('background') or '').strip()
        background_language_selections = _parse_json_list_form(request.form, 'background_languages')
        backgrounds_def = get_game_session().load_backgrounds() or {}
        background_def = backgrounds_def.get(background_key) if background_key else None
        if background_key and background_def is None:
            return jsonify(error='Unknown background selection'), 400

        flexible_cfg = subrace_def.get('flexible_ability') or race_def.get('flexible_ability') or {}
        expected_picks = flexible_cfg.get('picks') or []
        if expected_picks:
            if not race_bonus_map:
                return jsonify(error='Select all racial ability bonuses.'), 400
            expected_amounts = [int(pick.get('amount', 1)) for pick in expected_picks]
            try:
                actual_amounts = sorted([int(v) for v in race_bonus_map.values()])
            except Exception:
                return jsonify(error='Invalid racial ability bonuses'), 400
            expected_sorted = sorted(expected_amounts)
            if flexible_cfg.get('unique', True):
                if actual_amounts != expected_sorted:
                    return jsonify(error='Invalid racial ability bonuses'), 400
            else:
                if sum(actual_amounts) != sum(expected_amounts):
                    return jsonify(error='Invalid racial ability bonuses'), 400
            if any(ab not in ability for ab in race_bonus_map.keys()):
                return jsonify(error='Invalid racial ability bonuses'), 400
        else:
            if race_bonus_map:
                return jsonify(error='Unexpected racial ability bonuses'), 400
            race_bonus_map = {}

        skill_choice_cfg = subrace_def.get('skill_choices') or race_def.get('skill_choices') or {}
        if skill_choice_cfg.get('count'):
            expected_count = int(skill_choice_cfg['count'])
            options = set(skill_choice_cfg.get('options') or [])
            if len(race_skill_selections) != expected_count:
                plural = '' if expected_count == 1 else 's'
                return jsonify(error=f'Choose {expected_count} racial skill{plural}.'), 400
            if not all(choice in options for choice in race_skill_selections):
                return jsonify(error='Invalid racial skill choices'), 400
        else:
            if race_skill_selections:
                return jsonify(error='Unexpected racial skill choices'), 400
            race_skill_selections = []

        language_choice_cfg = subrace_def.get('language_choices') or race_def.get('language_choices') or {}
        if language_choice_cfg.get('count'):
            expected_language_count = int(language_choice_cfg['count'])
            options = set(language_choice_cfg.get('options') or [])
            if len(race_language_selections) != expected_language_count:
                plural = '' if expected_language_count == 1 else 's'
                return jsonify(error=f'Choose {expected_language_count} bonus language{plural}.'), 400
            if not all(choice in options for choice in race_language_selections):
                return jsonify(error='Invalid racial language choices'), 400
        else:
            if race_language_selections:
                return jsonify(error='Unexpected racial language choices'), 400
            race_language_selections = []

        def _apply_racial_bonus(bonus_map):
            if not isinstance(bonus_map, dict):
                return
            for key, value in bonus_map.items():
                if key in ability:
                    try:
                        ability[key] = min(20, ability[key] + int(value))
                    except Exception:
                        continue

        _apply_racial_bonus(race_def.get('attribute_bonus'))
        if subrace_def:
            _apply_racial_bonus(subrace_def.get('attribute_bonus'))
        if race_bonus_map:
            for key, value in race_bonus_map.items():
                ability[key] = min(20, ability[key] + int(value))

        base_languages = []
        for lang_src in (race_def.get('languages', []), subrace_def.get('languages', [])):
            if lang_src:
                base_languages.extend([str(l) for l in lang_src])

        # Build PC YAML compatible with existing templates
        # Basic defaults: level 1, hit_die inherit, simple equipment empty
        entity_uid = re.sub(r'[^a-zA-Z0-9_\-]', '_', name).lower()
        max_hp = compute_max_hp(klass, level, ability.get('con', 10), cdef)
        pc = {
            'name': name,
            'race': race,
            'classes': { klass: level },
            'level': level,
            'hit_die': 'inherit',
            'max_hp': max_hp,
            'ability': ability,
            'equipped': [],
            'inventory': [],
            'token': [ name[:1].upper() ],
            'description': f"A newly forged {race} {klass}.",
            'entity_uid': entity_uid,
            'token_image': f"token_{entity_uid}.png",
            'profile_image': f"characters/{entity_uid}.png",
        }
        if pronoun:
            pc['pronoun'] = pronoun
        if outward_appearance:
            pc['outward_appearance'] = outward_appearance
        if subrace:
            pc['subrace'] = subrace

        # Background assignment
        if background_key and background_def:
            pc['background'] = background_key
            # Background skill proficiencies are auto-added to skills list
            for skill in background_def.get('skill_proficiencies', []):
                pc.setdefault('skills', [])
                if skill not in pc['skills']:
                    pc['skills'].append(skill)
            # Background tool proficiencies
            for tool in background_def.get('tool_proficiencies', []):
                pc.setdefault('tool_proficiencies', [])
                if tool not in pc['tool_proficiencies']:
                    pc['tool_proficiencies'].append(tool)
            # Background fixed languages
            for lang in background_def.get('languages', []):
                pc.setdefault('languages', [])
                if lang not in pc['languages']:
                    pc['languages'].append(lang)

        languages = list(dict.fromkeys(base_languages + race_language_selections))
        # Add background language choices
        if background_language_selections:
            languages = list(dict.fromkeys(languages + background_language_selections))
        if languages:
            pc['languages'] = languages

        # Validate and apply class choices from templates
        try:
            _apply_class_and_feat_choices(
                pc,
                klass,
                level,
                classes_def,
                selected_skills,
                selected_cantrips,
                selected_level1,
                selected_feats,
                spells_by_level=spells_by_level,
                subclass_choice=subclass_choice,
                asi_boosts=asi_boosts,
            )

            # Apply equipment pack if selected
            equipment_pack_id = (request.form.get('equipment_pack') or '').strip()
            if equipment_pack_id:
                equipment_packs = get_game_session().load_equipment_packs() or {}
                pack = equipment_packs.get(equipment_pack_id)
                if pack and 'items' in pack:
                    for item_id, qty in pack['items'].items():
                        pc.setdefault('inventory', []).append({
                            'item': item_id,
                            'qty': int(qty)
                        })

            # Apply custom inventory from character builder
            inventory_json = (request.form.get('inventory') or '').strip()
            if inventory_json:
                try:
                    import json as _json
                    custom_inventory = _json.loads(inventory_json)
                    if custom_inventory:
                        pc['inventory'] = custom_inventory
                except Exception:
                    get_logger().exception('Failed to parse custom inventory')

            if race_skill_selections:
                pc.setdefault('skills', [])
                for skill in race_skill_selections:
                    if skill not in pc['skills']:
                        pc['skills'].append(skill)
        except Exception:
            get_logger().exception('Failed to apply class choices')

    # Save to templates/characters
        chars_dir = os.path.join(get_game_session().root_path, 'characters')
        os.makedirs(chars_dir, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
        yml_path = os.path.join(chars_dir, f"{safe_name}.yml")
        if os.path.exists(yml_path):
            return jsonify(error='A character with that name already exists'), 400

        import yaml as _yaml
        with open(yml_path, 'w') as f:
            _yaml.safe_dump(pc, f, sort_keys=False)

        # Save provided images (uploaded, prebuilt, or canvas-cropped data URL).
        # If a portrait was provided but no token, auto-generate a circular
        # token from the portrait so the on-map look stays consistent.
        try:
            assets_dir = os.path.join(get_game_session().root_path, 'assets')
            os.makedirs(assets_dir, exist_ok=True)
            profile_pil = _load_character_image_from_request(
                request,
                file_field='profile_image',
                prebuilt_field='profile_prebuilt',
            )
            token_pil = _load_character_image_from_request(
                request,
                file_field='token_image',
                prebuilt_field='token_prebuilt',
                data_url_field='token_image_data',
            )
            _save_character_images(entity_uid, assets_dir, profile_pil=profile_pil, token_pil=token_pil)
        except Exception:
            get_logger().exception('Failed to save character images')

        _register_new_character_in_campaign(pc, safe_name)

        # Optionally redirect to selection if a player
        if get_builder_only_mode():
            redirect_to = url_for('character.character_builder')
        else:
            redirect_to = '/character_selection' if 'dm' not in user_role() else '/'
        return jsonify(status='ok', redirect=redirect_to)
    except Exception as e:
        get_logger().exception('Failed to create character')
        return jsonify(error='Failed to create character'), 500


@character_bp.route('/character_details/<character_name>', methods=['GET'])
def character_details(character_name):
    """Get detailed information about a character for preview"""
    try:
        # Load the character from the game session (may need to materialize from sheet)
        character = ensure_character_entity_loaded(character_name)
        
        if not character:
            return jsonify(error="Character not found"), 404
        
        # Extract important character information
        details = {
            'name': character.name.title(),
            'display_name': character.display_name,
            'race': character.race(),
            'subrace': character.subrace() or 'None',
            'classes': character.c_class(),
            'level': character.level(),
            'hit_points': {
                'current': character.hp(),
                'maximum': character.max_hp()
            },
            'armor_class': character.armor_class(),
            'speed': character.speed(),
            'ability_scores': {
                'str': character.ability_score_str(),
                'dex': character.ability_score_dex(), 
                'con': character.ability_score_con(),
                'int': character.ability_score_int(),
                'wis': character.ability_score_wis(),
                'cha': character.ability_score_cha()
            },
            'ability_modifiers': {
                'str': character.str_mod(),
                'dex': character.dex_mod(),
                'con': character.con_mod(), 
                'int': character.int_mod(),
                'wis': character.wis_mod(),
                'cha': character.cha_mod()
            },
            'proficiency_bonus': character.proficiency_bonus(),
            'passive_perception': character.passive_perception(),
            'languages': character.languages(),
            'equipment': {
                'weapons': [],
                'armor': [],
                'other': []
            },
            'spells': {
                'has_spells': character.has_spells() if hasattr(character, 'has_spells') else False,
                'spell_slots': {},
                'known_spells': []
            },
            'resources': {},
            'maneuvers': character.properties.get('maneuvers', []),
            'class_features': [],
            'racial_features': []
        }
        
        # Get equipped items
        equipped_items = character.equipped_items()
        for item in equipped_items:
            item_type = item.get('type', 'other')
            item_info = {
                'name': item.get('label', item.get('name', 'Unknown')),
                'damage': item.get('damage'),
                'range': item.get('range'),
                'properties': item.get('properties', [])
            }
            
            if item_type in ['melee_attack', 'ranged_attack']:
                details['equipment']['weapons'].append(item_info)
            elif item_type in ['armor', 'shield']:
                item_info['ac'] = item.get('ac')
                item_info['bonus_ac'] = item.get('bonus_ac')
                details['equipment']['armor'].append(item_info)
            else:
                details['equipment']['other'].append(item_info)
        
        # Get spell information if character has spells
        if details['spells']['has_spells']:
            try:
                # Get available spells
                available_spells = character.available_spells(None)
                details['spells']['known_spells'] = available_spells
                
                # Get spell slots for each class
                for class_name in character.c_class().keys():
                    class_slots = {}
                    for level in range(1, 10):
                        slots = character.spell_slots_count(level, class_name)
                        if slots > 0:
                            class_slots[f'level_{level}'] = slots
                    if class_slots:
                        details['spells']['spell_slots'][class_name] = class_slots
            except:
                # If spell info fails, just mark as having spells but no details
                pass
        
        # Get some key class features
        important_features = [
            'action_surge', 'second_wind', 'sneak_attack', 'rage', 'bardic_inspiration',
            'channel_divinity', 'lay_on_hands', 'fighting_style', 'spellcasting',
            'cunning_action', 'uncanny_dodge', 'evasion', 'reliable_talent',
            'fancy_footwork', 'rakish_audacity', 'panache', 'sentinel',
            'martial_adept', 'arcane_recovery', 'abjuration_savant',
            'arcane_ward', 'projected_ward', 'improved_abjuration',
            'spell_resistance'
        ]
        for feature in important_features:
            if character.class_feature(feature):
                details['class_features'].append(feature.replace('_', ' ').title())

        for name, pool in (getattr(character, 'resources', None) or {}).items():
            details['resources'][name] = {
                'current': pool.current,
                'max': pool.max_value,
                'restore_on': pool.restore_on,
            }
        
        # Get racial features
        racial_features = character.race_properties.get('race_features', [])
        details['racial_features'] = [f.replace('_', ' ').title() for f in racial_features[:5]]  # Limit to first 5

        # Journal entries (per-PC quest log). Returned in chronological order
        # so the UI can render newest-first via simple slicing.
        details['journal'] = list(getattr(character, 'journal', None) or [])

        return jsonify(details)
        
    except Exception as e:
        get_logger().error(f"Error getting character details for {character_name}: {e}")
        return jsonify(error="Failed to load character details"), 500


@character_bp.route('/character/<character_name>/journal', methods=['GET'])
def character_journal_list(character_name):
    """Return a PC's journal entries. Supports ``?q=`` substring search,
    ``?kind=`` filter, and ``?limit=`` truncation.
    """
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error="Character not found"), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error="Journals are only available for player characters"), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    query = request.args.get('q') or request.args.get('query')
    kind = request.args.get('kind')
    limit_raw = request.args.get('limit')
    limit = None
    if limit_raw:
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = None
    entries = _serialize_journal(character, query=query, kind=kind, limit=limit)
    unread = character.unread_journal_count() if hasattr(character, 'unread_journal_count') else 0
    return jsonify({
        'entity_uid': character.entity_uid,
        'count': len(entries),
        'unread': unread,
        'entries': entries,
    })


@character_bp.route('/journal/unread_summary', methods=['GET'])
def journal_unread_summary():
    """Unread journal counts for the current user's controlled PCs."""
    if not logged_in():
        return jsonify(error='Not authenticated'), 401
    summary = unread_journal_summary_for_username(session.get('username'))
    return jsonify(summary)


@character_bp.route('/character/<character_name>/journal/mark_read', methods=['POST'])
def character_journal_mark_read(character_name):
    """Mark journal entries read for ``character_name``."""
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error='Character not found'), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error='Journals are only available for player characters'), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    payload = request.get_json(silent=True) or {}
    entry_ids = payload.get('entry_ids')
    if entry_ids is not None and not isinstance(entry_ids, list):
        return jsonify(error='entry_ids must be a list'), 400
    marked = character.mark_journal_read(entry_ids=entry_ids)
    _persist_journal_change(character)
    _emit_journal_update([character.entity_uid], reason='read')
    return jsonify({
        'marked': marked,
        'unread': character.unread_journal_count(),
    })


@character_bp.route('/character/<character_name>/level_up/preview', methods=['GET'])
def character_level_up_preview(character_name):
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error="Character not found"), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error="Level-up is only available for player characters"), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    class_name = request.args.get('class_name') or request.args.get('class')
    hp_mode = request.args.get('hp_mode') or 'average'
    hp_roll = request.args.get('hp_roll')
    try:
        hp_roll = int(hp_roll) if hp_roll not in (None, '') else None
        return jsonify(success=True, preview=character.level_up_preview(
            class_name=class_name,
            hp_mode=hp_mode,
            hp_roll=hp_roll,
        ))
    except Exception as exc:
        return jsonify(success=False, error=str(exc), progress=character.xp_progress()), 400


@character_bp.route('/character/<character_name>/level_up/apply', methods=['POST'])
def character_level_up_apply(character_name):
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error="Character not found"), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error="Level-up is only available for player characters"), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    if get_current_game().get_current_battle() and 'dm' not in user_role():
        return jsonify(success=False, error='Level-up is unavailable during active combat'), 409

    payload = request.get_json(silent=True) or {}
    class_name = payload.get('class_name') or payload.get('class')
    hp_mode = payload.get('hp_mode') or 'average'
    hp_roll = payload.get('hp_roll')
    try:
        hp_roll = int(hp_roll) if hp_roll not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify(success=False, error='hp_roll must be a number'), 400

    try:
        with get_current_game().game_state_lock:
            summary = character.apply_level_up(
                class_name=class_name,
                hp_mode=hp_mode,
                hp_roll=hp_roll,
                choices=payload.get('choices') or {},
                force='dm' in user_role() and bool(payload.get('force')),
            )
            get_current_game().save_game_async()
        get_socketio().emit('message', {
            'type': 'level_up_applied',
            'message': {
                'entity_uid': character.entity_uid,
                'name': character.label(),
                'level': character.level(),
                'summary': summary,
            },
        })
        get_socketio().emit('message', {'type': 'refresh_map'})
        return jsonify(success=True, summary=summary, progress=character.xp_progress())
    except Exception as exc:
        return jsonify(success=False, error=str(exc), progress=character.xp_progress()), 400


@character_bp.route('/character/<character_name>/journal', methods=['POST'])
def character_journal_add(character_name):
    """Append a manual journal entry for ``character_name``."""
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error="Character not found"), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error="Journals are only available for player characters"), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    payload = request.get_json(silent=True) or {}
    text = (payload.get('text') or '').strip()
    if not text:
        return jsonify(error='Entry text is required'), 400
    title = payload.get('title')
    tags = payload.get('tags') or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    requester_role = 'dm' if 'dm' in user_role() else 'player'
    kind = payload.get('kind') or ('dm' if requester_role == 'dm' else 'note')
    entry = character.add_journal_entry(
        text,
        kind=kind,
        title=title,
        source=session.get('username'),
        tags=tags,
        read=True,
    )
    if entry:
        _log_journal_entry_to_campaign_db(character, entry)
    _persist_journal_change(character)
    _emit_journal_update([character.entity_uid], reason=kind)
    return jsonify({'entry': entry, 'count': len(character.journal), 'unread': character.unread_journal_count()})


@character_bp.route('/character/<character_name>/journal/<entry_id>', methods=['DELETE'])
def character_journal_delete(character_name, entry_id):
    character = get_current_game().get_entity_by_uid(character_name)
    if not character:
        return jsonify(error="Character not found"), 404
    if not isinstance(character, PlayerCharacter):
        return jsonify(error="Journals are only available for player characters"), 400
    allowed, err = _journal_owner_check(character)
    if not allowed:
        return err
    removed = character.remove_journal_entry(entry_id)
    if not removed:
        return jsonify(error='Entry not found'), 404
    _persist_journal_change(character)
    _emit_journal_update([character.entity_uid], reason='delete')
    return jsonify({'status': 'ok', 'count': len(character.journal), 'unread': character.unread_journal_count()})


@character_bp.route('/inventory/container/status', methods=['GET'])
def inventory_container_status():
    if not logged_in():
        return jsonify(success=False, error='Login required'), 401
    from webapp.blueprints.helpers.inventory_container import (
        can_manage_inventory_entity,
        resolve_inventory_entity,
    )

    entity_id = (request.args.get('entity_id') or '').strip()
    container_name = (request.args.get('container_name') or '').strip()
    if not entity_id or not container_name:
        return jsonify(success=False, error='entity_id and container_name are required'), 400

    entity = resolve_inventory_entity(entity_id)
    if entity is None:
        return jsonify(success=False, error='Entity not found'), 404
    if not can_manage_inventory_entity(entity):
        return jsonify(success=False, error='Not authorized to manage this inventory'), 403
    if not hasattr(entity, 'is_container') or not entity.is_container(container_name, getattr(entity, 'session', None)):
        return jsonify(success=False, error='Item is not a container'), 404

    game_session = getattr(entity, 'session', None) or get_game_session()
    status = entity.container_status(container_name, game_session)
    status['stowable_items'] = entity.stowable_inventory_items(container_name, game_session)
    return jsonify(success=True, status=status)


@character_bp.route('/inventory/container/stow', methods=['POST'])
def inventory_container_stow():
    if not logged_in():
        return jsonify(success=False, error='Login required'), 401
    from webapp.blueprints.helpers.inventory_container import (
        can_manage_inventory_entity,
        resolve_inventory_entity,
    )

    data = request.get_json(silent=True) or request.form
    entity_id = (data.get('entity_id') or '').strip()
    container_name = (data.get('container_name') or '').strip()
    item_name = (data.get('item_name') or '').strip()
    try:
        qty = int(data.get('qty', 1))
    except (TypeError, ValueError):
        qty = 1

    if not entity_id or not container_name or not item_name:
        return jsonify(success=False, error='entity_id, container_name, and item_name are required'), 400

    entity = resolve_inventory_entity(entity_id)
    if entity is None:
        return jsonify(success=False, error='Entity not found'), 404
    if not can_manage_inventory_entity(entity):
        return jsonify(success=False, error='Not authorized to manage this inventory'), 403

    game_session = getattr(entity, 'session', None) or get_game_session()
    ok, reason = entity.stow_item(container_name, item_name, qty, game_session)
    if not ok:
        return jsonify(success=False, error=reason or 'Failed to stow item'), 400

    get_socketio().emit('message', {'type': 'refresh_map'})
    status = entity.container_status(container_name, game_session)
    status['stowable_items'] = entity.stowable_inventory_items(container_name, game_session)
    return jsonify(success=True, status=status)


@character_bp.route('/inventory/container/retrieve', methods=['POST'])
def inventory_container_retrieve():
    if not logged_in():
        return jsonify(success=False, error='Login required'), 401
    from webapp.blueprints.helpers.inventory_container import (
        can_manage_inventory_entity,
        resolve_inventory_entity,
    )

    data = request.get_json(silent=True) or request.form
    entity_id = (data.get('entity_id') or '').strip()
    container_name = (data.get('container_name') or '').strip()
    item_name = (data.get('item_name') or '').strip()
    try:
        qty = int(data.get('qty', 1))
    except (TypeError, ValueError):
        qty = 1

    if not entity_id or not container_name or not item_name:
        return jsonify(success=False, error='entity_id, container_name, and item_name are required'), 400

    entity = resolve_inventory_entity(entity_id)
    if entity is None:
        return jsonify(success=False, error='Entity not found'), 404
    if not can_manage_inventory_entity(entity):
        return jsonify(success=False, error='Not authorized to manage this inventory'), 403

    game_session = getattr(entity, 'session', None) or get_game_session()
    ok, reason = entity.retrieve_item(container_name, item_name, qty, game_session)
    if not ok:
        return jsonify(success=False, error=reason or 'Failed to retrieve item'), 400

    get_socketio().emit('message', {'type': 'refresh_map'})
    status = entity.container_status(container_name, game_session)
    status['stowable_items'] = entity.stowable_inventory_items(container_name, game_session)
    return jsonify(success=True, status=status)

