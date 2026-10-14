"""D&D 5e creature stat-block payload and NPC sheet access control.

Player characters keep the PC sheet. NPCs render as a monster-style block.
Players see the full block only for player-controlled NPCs (sidekicks,
familiars, assigned controllers). Other NPCs show portrait, name, and
description (when the campaign allows).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Optional

from flask import jsonify, render_template, session

# SRD 5.1 trait text (CC BY 4.0). Used when YAML only stores an attribute id.
_SRD_TRAIT_TEXT = {
    'undead_fortitude': (
        'If damage reduces the {name} to 0 hit points, it must make a '
        'Constitution saving throw with a DC of 5 + the damage taken, unless '
        'the damage is radiant or from a critical hit. On a success, the '
        '{name} drops to 1 hit point instead.'
    ),
    'nimble_escape': (
        'The {name} can take the Disengage or Hide action as a bonus action '
        'on each of its turns.'
    ),
    'pack_tactics': (
        "The {name} has advantage on an attack roll against a creature if at "
        "least one of the {name}'s allies is within 5 feet of the creature "
        "and the ally isn't incapacitated."
    ),
    'incorporeal_movement': (
        'The {name} can move through other creatures and objects as if they '
        'were difficult terrain. It takes 5 (1d10) force damage if it ends '
        'its turn inside an object.'
    ),
    'sunlight_sensitivity': (
        'While in sunlight, the {name} has disadvantage on attack rolls, as '
        'well as on Wisdom (Perception) checks that rely on sight.'
    ),
    'lightning_absorption': (
        'Whenever the {name} is subjected to lightning damage, it takes no '
        'damage and regains a number of hit points equal to the lightning '
        'damage dealt.'
    ),
    'second_wind': (
        'The {name} can use a bonus action to regain hit points equal to '
        '1d10 + its sidekick level.'
    ),
    'extra_attack': (
        'The {name} can attack twice, instead of once, whenever it takes '
        'the Attack action on its turn.'
    ),
    'helpful': (
        'The {name} can take the Help action as a bonus action.'
    ),
}

_ABILITY_ROWS = (
    ('STR', 'str', 'strength'),
    ('DEX', 'dex', 'dexterity'),
    ('CON', 'con', 'constitution'),
    ('INT', 'int', 'intelligence'),
    ('WIS', 'wis', 'wisdom'),
    ('CHA', 'cha', 'charisma'),
)


def signed(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or '')
    if number >= 0:
        return f'+{number}'
    return str(number)


def pretty_label(value) -> str:
    return str(value or '').replace('_', ' ').strip()


def title_label(value) -> str:
    label = pretty_label(value)
    return label.title() if label else ''


def format_challenge_rating(cr) -> str:
    if cr is None or cr == '':
        return ''
    try:
        number = float(cr)
    except (TypeError, ValueError):
        return str(cr)
    if number <= 0:
        return '0'
    fraction = Fraction(number).limit_denominator(8)
    if fraction.numerator == 0:
        return '0'
    if fraction.denominator == 1:
        return str(fraction.numerator)
    return f'{fraction.numerator}/{fraction.denominator}'


def format_speed_line(entity) -> str:
    props = getattr(entity, 'properties', None) or {}
    parts = []
    walk = props.get('speed')
    if walk is not None and walk != '':
        parts.append(f'{walk} ft.')
    for key, label in (
        ('speed_fly', 'fly'),
        ('speed_swim', 'swim'),
        ('speed_climb', 'climb'),
        ('speed_burrow', 'burrow'),
    ):
        value = props.get(key)
        if value:
            parts.append(f'{label} {value} ft.')
    return ', '.join(parts) if parts else '—'


def format_size_type_alignment(entity) -> str:
    props = getattr(entity, 'properties', None) or {}
    size = str(props.get('size') or '').strip().lower()
    size_display = size.capitalize() if size else ''

    race = props.get('race') or []
    if isinstance(race, str):
        race = [part.strip() for part in race.replace(',', ' ').split() if part.strip()]
    types = [str(item).replace('_', ' ').strip().lower() for item in race if item]
    creature_type = types[0] if types else str(props.get('kind') or props.get('creature_type') or '').replace('_', ' ').strip().lower()
    tags = types[1:]
    type_line = creature_type
    if tags:
        type_line = f'{creature_type} ({", ".join(tags)})'

    alignment = str(getattr(entity, 'alignment', lambda: '')() or props.get('alignment') or '').replace('_', ' ').strip().lower()
    bits = [bit for bit in (size_display, type_line) if bit]
    headed = ' '.join(bits)
    if alignment:
        return f'{headed}, {alignment}' if headed else alignment
    return headed or '—'


def format_senses_line(entity) -> str:
    props = getattr(entity, 'properties', None) or {}
    explicit = props.get('senses')
    parts = []
    if isinstance(explicit, str) and explicit.strip():
        parts.append(explicit.strip().rstrip('.'))
    elif isinstance(explicit, (list, tuple)):
        parts.extend(str(item).strip() for item in explicit if item)
    else:
        for key, label in (
            ('blindsight', 'blindsight'),
            ('darkvision', 'darkvision'),
            ('tremorsense', 'tremorsense'),
            ('truesight', 'truesight'),
        ):
            value = props.get(key)
            if value:
                parts.append(f'{label} {value} ft.')
    try:
        passive = entity.passive_perception()
    except Exception:
        passive = props.get('passive_perception')
    if passive is not None and passive != '':
        parts.append(f'passive Perception {passive}')
    return ', '.join(parts) if parts else '—'


def _signed_skill_mod(bonus) -> str:
    if bonus is None or bonus == '':
        return ''
    if isinstance(bonus, str):
        text = bonus.strip()
        if text.startswith('+') or text.startswith('-'):
            return text
        return signed(text)
    return signed(bonus)


def format_skill_entries(entity) -> list[dict[str, str]]:
    """Listed skills for the creature stat block, with signed modifiers for rolls."""
    props = getattr(entity, 'properties', None) or {}
    skills = props.get('skills') or {}
    entries = []

    def _append(skill_key, bonus=None):
        key = str(skill_key or '').strip()
        if not key:
            return
        mod = _signed_skill_mod(bonus)
        if not mod:
            fn = getattr(entity, f'{key}_mod', None)
            if callable(fn):
                try:
                    mod = signed(fn())
                except Exception:
                    mod = ''
        if not mod:
            return
        entries.append({
            'name': key,
            'label': title_label(key),
            'mod': mod,
        })

    if isinstance(skills, dict):
        for skill, bonus in skills.items():
            _append(skill, bonus)
    elif isinstance(skills, (list, tuple)):
        for skill in skills:
            if isinstance(skill, dict):
                _append(skill.get('name') or skill.get('id'), skill.get('bonus') or skill.get('mod'))
            else:
                _append(skill)
    return entries


def format_skills_line(entity) -> str:
    return ', '.join(f"{item['label']} {item['mod']}" for item in format_skill_entries(entity))


def format_saves_line(entity) -> str:
    props = getattr(entity, 'properties', None) or {}
    saves = props.get('saving_throws') or props.get('saves') or {}
    parts = []
    if isinstance(saves, dict) and saves:
        for ability, bonus in saves.items():
            if bonus is None or bonus == '':
                continue
            abbr = str(ability).replace('strength', 'str').replace('dexterity', 'dex')
            abbr = abbr.replace('constitution', 'con').replace('intelligence', 'int')
            abbr = abbr.replace('wisdom', 'wis').replace('charisma', 'cha')
            parts.append(f'{abbr[:3].title()} {signed(bonus) if not str(bonus).startswith(("+", "-")) else bonus}')
        return ', '.join(parts)

    proficient = props.get('saving_throw_proficiencies') or []
    if isinstance(proficient, str):
        proficient = [proficient]
    for ability in proficient:
        key = str(ability).strip().lower()
        if not key:
            continue
        try:
            mod = entity.saving_throw_mod(key if key in ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma') else key)
        except Exception:
            mod = None
        abbr = {
            'strength': 'Str', 'str': 'Str',
            'dexterity': 'Dex', 'dex': 'Dex',
            'constitution': 'Con', 'con': 'Con',
            'intelligence': 'Int', 'int': 'Int',
            'wisdom': 'Wis', 'wis': 'Wis',
            'charisma': 'Cha', 'cha': 'Cha',
        }.get(key, title_label(key)[:3])
        parts.append(f'{abbr} {signed(mod) if mod is not None else ""}'.strip())
    return ', '.join(p for p in parts if p)


def _action_target_text(action: dict) -> str:
    targets = action.get('targets', 1)
    try:
        count = int(targets)
    except (TypeError, ValueError):
        return str(targets or 'one target')
    if count <= 1:
        return 'one target'
    return f'{count} targets'


def format_npc_action(action: dict) -> dict[str, str]:
    if not isinstance(action, dict):
        return {'name': '', 'text': ''}
    name = pretty_label(action.get('name') or action.get('id') or '')
    action_type = str(action.get('type') or '').lower()
    extra_bits = []
    for hit in action.get('on_hit') or []:
        if isinstance(hit, dict) and hit.get('description'):
            extra_bits.append(str(hit['description']).strip())
    if action.get('description') and action_type not in ('melee_attack', 'ranged_attack'):
        extra_bits.insert(0, str(action['description']).strip())
    if action.get('teleport_after_attack'):
        extra_bits.append(
            f"Immediately after the attack hits or misses, it can teleport up to "
            f"{action['teleport_after_attack']} feet to an unoccupied space it can see."
        )

    if action_type in ('melee_attack', 'ranged_attack'):
        kind = 'Melee Weapon Attack' if action_type == 'melee_attack' else 'Ranged Weapon Attack'
        attack = action.get('attack')
        to_hit = f'{signed(attack)} to hit' if attack is not None and attack != '' else 'to hit'
        if action_type == 'melee_attack':
            reach = action.get('range') or 5
            range_text = f'reach {reach} ft.'
        else:
            near = action.get('range') or 0
            far = action.get('range_max')
            range_text = f'range {near}/{far} ft.' if far else f'range {near} ft.'
        damage = action.get('damage')
        damage_die = action.get('damage_die')
        damage_type = pretty_label(action.get('damage_type') or '').lower()
        hit_bits = []
        if damage is not None and damage != '':
            if damage_die:
                hit_bits.append(f'{damage} ({damage_die})')
            else:
                hit_bits.append(str(damage))
        elif damage_die:
            hit_bits.append(str(damage_die))
        if damage_type:
            hit_bits.append(f'{damage_type} damage')
        hit_text = ' '.join(hit_bits) if hit_bits else 'damage'
        sentence = (
            f'{kind}: {to_hit}, {range_text}, {_action_target_text(action)}. '
            f'Hit: {hit_text}.'
        )
        if extra_bits:
            sentence = f'{sentence} {" ".join(extra_bits)}'
        return {'name': title_label(name) or name, 'text': sentence}

    text = ' '.join(extra_bits) if extra_bits else str(action.get('description') or '').strip()
    label = title_label(name) or name
    if action.get('recharge'):
        label = f"{label} (Recharge {action['recharge']})"
    return {'name': label, 'text': text}


def _trait_entries(entity) -> list[dict[str, str]]:
    props = getattr(entity, 'properties', None) or {}
    creature = getattr(entity, 'label', lambda: props.get('kind') or 'creature')()
    seen = set()
    traits: list[dict[str, str]] = []

    def add(name, text=''):
        key = pretty_label(name).lower()
        if not key or key in seen:
            return
        seen.add(key)
        traits.append({'name': title_label(name), 'text': (text or '').strip()})

    descriptions = props.get('trait_descriptions') or {}
    if not isinstance(descriptions, dict):
        descriptions = {}

    raw_traits = props.get('traits') or []
    if isinstance(raw_traits, list):
        for item in raw_traits:
            if isinstance(item, dict):
                add(item.get('name') or item.get('id'), item.get('description') or item.get('text') or '')
            elif item:
                add(item)

    for source_key in ('attributes', 'class_features'):
        for item in props.get(source_key) or []:
            if isinstance(item, dict):
                add(item.get('name') or item.get('id'), item.get('description') or item.get('text') or '')
                continue
            ident = str(item or '').strip()
            if not ident:
                continue
            text = descriptions.get(ident) or descriptions.get(ident.lower())
            if not text:
                template = _SRD_TRAIT_TEXT.get(ident.lower())
                text = template.format(name=creature) if template else ''
            add(ident, text)

    multi = props.get('multiattack')
    if multi:
        text = _multiattack_text(multi, creature)
        if text:
            traits.insert(0, {'name': 'Multiattack', 'text': text})
    return traits


def _multiattack_text(multi, creature) -> str:
    entries = multi if isinstance(multi, list) else [multi]
    phrases = []
    for entry in entries:
        if isinstance(entry, dict):
            options = [title_label(n) for n in entry.get('any_of') or [] if str(n).strip()]
            count = int(entry.get('count', 2) or 0)
            if options and count:
                phrases.append(f'{count} attacks, any combination of {" or ".join(options)}')
        elif isinstance(entry, str):
            names = [title_label(part) for part in entry.split(',') if part.strip()]
            if names:
                phrases.append(f'{len(names)} attacks: {", ".join(names)}')
    if not phrases:
        return ''
    return f'The {creature} makes {" — or — ".join(phrases)}.'


def _ability_rows(entity) -> list[dict[str, Any]]:
    rows = []
    scores = getattr(entity, 'ability_scores', None) or {}
    props_ability = (getattr(entity, 'properties', None) or {}).get('ability') or {}
    for label, abbr, full in _ABILITY_ROWS:
        score = scores.get(abbr)
        if score is None:
            score = props_ability.get(abbr)
        mod = None
        try:
            mod = entity.ability_mod(full)
        except Exception:
            mod = None
        if mod is None and score is not None:
            try:
                mod = (int(score) - 10) // 2
            except (TypeError, ValueError):
                mod = 0
        rows.append({
            'label': label,
            'score': score if score is not None else '—',
            'mod': signed(mod) if mod is not None else '',
        })
    return rows


def _defense_line(values) -> str:
    labels = [title_label(item) for item in (values or []) if item]
    return ', '.join(labels)


_ABILITY_FULL = {
    'str': 'strength', 'strength': 'strength',
    'dex': 'dexterity', 'dexterity': 'dexterity',
    'con': 'constitution', 'constitution': 'constitution',
    'int': 'intelligence', 'intelligence': 'intelligence',
    'wis': 'wisdom', 'wisdom': 'wisdom',
    'cha': 'charisma', 'charisma': 'charisma',
}

_CLASS_SPELL_ABILITY = {
    'cleric': 'wisdom',
    'druid': 'wisdom',
    'ranger': 'wisdom',
    'wizard': 'intelligence',
    'fighter': 'intelligence',
    'sorcerer': 'charisma',
    'warlock': 'charisma',
    'bard': 'charisma',
    'paladin': 'charisma',
}


def ordinal(n: int) -> str:
    try:
        number = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 10 <= number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix}'


def _slot_count(slots, level) -> int:
    if not isinstance(slots, dict):
        return 0
    raw = slots.get(str(level), slots.get(level, 0))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_ability(value) -> str:
    return _ABILITY_FULL.get(str(value or '').strip().lower(), '')


def _infer_spellcasting_ability(entity, prepared) -> str:
    """Resolve the 5e spellcasting ability (full name, e.g. ``wisdom``)."""
    props = getattr(entity, 'properties', None) or {}
    explicit = _normalize_ability(
        props.get('spell_ability') or props.get('spellcasting_ability')
    )
    if explicit:
        return explicit
    session = getattr(entity, 'session', None)
    votes: dict[str, int] = {}
    if session is not None:
        for slug in prepared or []:
            try:
                details = session.load_spell(slug) or {}
            except Exception:
                continue
            for klass in details.get('spell_list_classes') or []:
                ability = _CLASS_SPELL_ABILITY.get(str(klass).lower())
                if ability:
                    votes[ability] = votes.get(ability, 0) + 1
    if votes:
        return max(votes, key=votes.get)
    return 'intelligence'


def _caster_level(entity, slots) -> Optional[int]:
    props = getattr(entity, 'properties', None) or {}
    for key in ('spellcaster_level', 'caster_level', 'sidekick_level'):
        raw = props.get(key)
        if raw is None or raw == '':
            continue
        try:
            level = int(raw)
        except (TypeError, ValueError):
            continue
        if level > 0:
            return level
    highest = 0
    if isinstance(slots, dict):
        for key, count in slots.items():
            try:
                if int(count or 0) <= 0:
                    continue
                highest = max(highest, int(key))
            except (TypeError, ValueError):
                continue
    if highest <= 0:
        return None
    return highest * 2 - 1


def _spell_display_name(session, slug) -> tuple[int, str]:
    name = title_label(slug)
    level = 0
    if session is None or not slug:
        return level, name
    try:
        details = session.load_spell(slug) or {}
    except Exception:
        return level, name
    try:
        level = int(details.get('level') or 0)
    except (TypeError, ValueError):
        level = 0
    name = str(details.get('name') or details.get('label') or name)
    return level, name


def _spell_lists(entity, prepared, known, slots) -> list[dict[str, Any]]:
    session = getattr(entity, 'session', None)
    slugs = list(prepared or []) or [s for s in (known or []) if s]
    by_level: dict[int, list[str]] = {}
    for slug in slugs:
        level, name = _spell_display_name(session, slug)
        by_level.setdefault(level, []).append(name)
    lists = []
    for level in sorted(by_level):
        names = by_level[level]
        if level <= 0:
            label = 'Cantrips (at will)'
        else:
            count = _slot_count(slots, level)
            slot_bit = ''
            if count > 0:
                slot_bit = f' ({count} slot{"s" if count != 1 else ""})'
            label = f'{ordinal(level)} level{slot_bit}'
        lists.append({'level': level, 'label': label, 'spells': names})
    return lists


def _innate_spellcasting_block(entity) -> Optional[dict[str, Any]]:
    from natural20.utils.innate_spellcasting import (
        innate_ability, innate_attack_bonus, innate_config,
        innate_save_dc, innate_spell_groups, pool_name,
    )
    if not innate_config(entity):
        return None
    session = getattr(entity, 'session', None)
    resources = getattr(entity, 'resources', None) or {}
    lists = []
    for kind, count, slugs in innate_spell_groups(entity):
        names = []
        for slug in slugs:
            name = _spell_display_name(session, slug)[1] if session is not None else title_label(slug)
            pool = resources.get(pool_name(slug)) if kind == 'per_day' else None
            if pool is not None and pool.current < pool.max_value:
                name = f'{name} ({pool.current} left)'
            names.append(name)
        label = 'At will' if kind == 'at_will' else f'{count}/day' + (' each' if len(slugs) > 1 else '')
        lists.append({'label': label, 'spells': names})
    ability = innate_ability(entity)
    attack_bonus = innate_attack_bonus(entity)
    return {
        'innate': True,
        'ability': title_label(ability),
        'ability_key': ability,
        'save_dc': innate_save_dc(entity),
        'attack_bonus': attack_bonus,
        'attack_bonus_signed': signed(attack_bonus),
        'no_material': bool(innate_config(entity).get('no_material')),
        'caster_level': None,
        'caster_level_ordinal': '',
        'slots': [],
        'lists': lists,
        'prepared': [],
        'known': [],
    }


def _spellcasting_block(entity) -> Optional[dict[str, Any]]:
    innate = _innate_spellcasting_block(entity)
    if innate is not None:
        return innate
    props = getattr(entity, 'properties', None) or {}
    slots = props.get('spell_slots') or {}
    prepared = []
    try:
        prepared = list(entity.prepared_spells() or [])
    except Exception:
        prepared = list(props.get('prepared_spells') or [])
    known = list(props.get('known_spells') or props.get('spells') or [])
    if not slots and not prepared and not known:
        return None
    ability = _infer_spellcasting_ability(entity, prepared or known)
    try:
        pb = int(entity.proficiency_bonus())
    except Exception:
        try:
            pb = int(props.get('proficiency_bonus') or 2)
        except (TypeError, ValueError):
            pb = 2
    try:
        mod = int(entity.ability_mod(ability) or 0)
    except Exception:
        mod = 0
    try:
        save_dc = int(props.get('spell_save_dc')) if props.get('spell_save_dc') not in (None, '') else None
    except (TypeError, ValueError):
        save_dc = None
    if save_dc is None:
        try:
            save_dc = int(entity.spell_save_dc(ability))
        except Exception:
            save_dc = 8 + pb + mod
    try:
        attack_bonus = (
            int(props.get('spell_attack_bonus'))
            if props.get('spell_attack_bonus') not in (None, '')
            else None
        )
    except (TypeError, ValueError):
        attack_bonus = None
    if attack_bonus is None:
        attack_bonus = pb + mod
    caster_level = _caster_level(entity, slots)
    slot_rows = []
    if isinstance(slots, dict):
        for level in sorted(slots.keys(), key=lambda value: int(value) if str(value).isdigit() else 99):
            count_i = _slot_count(slots, level)
            if count_i <= 0:
                continue
            label = 'Cantrips' if str(level) == '0' else f'{ordinal(int(level))}-level'
            slot_rows.append({'level': str(level), 'label': label, 'count': count_i})
    return {
        'ability': title_label(ability),
        'ability_key': ability,
        'save_dc': save_dc,
        'attack_bonus': attack_bonus,
        'attack_bonus_signed': signed(attack_bonus),
        'caster_level': caster_level,
        'caster_level_ordinal': ordinal(caster_level) if caster_level else '',
        'slots': slot_rows,
        'lists': _spell_lists(entity, prepared, known, slots),
        'prepared': [title_label(name) for name in prepared],
        'known': [title_label(name) for name in known if name not in prepared],
    }


def _sidekick_note(entity) -> Optional[dict[str, Any]]:
    props = getattr(entity, 'properties', None) or {}
    class_id = props.get('sidekick_class')
    level = props.get('sidekick_level')
    if not class_id and level is None:
        return None
    label = title_label(str(class_id or '').replace('sidekick_', ''))
    role = title_label(props.get('sidekick_role') or '')
    return {
        'class_label': label or 'Sidekick',
        'level': level,
        'role': role,
    }


def npc_has_spells(entity) -> bool:
    block = _spellcasting_block(entity)
    return bool(block)


def build_npc_stat_block(entity) -> dict[str, Any]:
    """Return a display dict for the 5e creature stat-block template."""
    props = getattr(entity, 'properties', None) or {}
    defenses = {}
    try:
        defenses = entity.sheet_defenses() or {}
    except Exception:
        defenses = {
            'resistances': getattr(entity, 'resistances', None) or [],
            'immunities': getattr(entity, 'damage_immunities', None) or [],
            'vulnerabilities': getattr(entity, 'damage_vulnerabilities', None) or [],
            'condition_immunities': getattr(entity, 'condition_immunities', None) or [],
        }
    conditions = []
    try:
        conditions = entity.sheet_conditions() or []
    except Exception:
        conditions = []

    actions = []
    for raw in props.get('actions') or getattr(entity, 'npc_actions', None) or []:
        formatted = format_npc_action(raw)
        if formatted.get('name'):
            actions.append(formatted)

    legendary = []
    for raw in props.get('legendary_actions') or getattr(entity, 'legendary_actions', None) or []:
        formatted = format_npc_action(raw)
        if formatted.get('name'):
            cost = raw.get('cost') if isinstance(raw, dict) else None
            if isinstance(cost, int) and cost > 1:
                formatted['name'] = f"{formatted['name']} (Costs {cost} Actions)"
            legendary.append(formatted)

    reactions = []
    for raw in props.get('reactions') or []:
        formatted = format_npc_action(raw)
        if formatted.get('name'):
            reactions.append(formatted)

    bonus_actions = []
    for raw in props.get('bonus_actions') or []:
        formatted = format_npc_action(raw)
        if formatted.get('name'):
            bonus_actions.append(formatted)

    cr = props.get('cr')
    xp = props.get('xp')
    cr_label = format_challenge_rating(cr)
    if xp not in (None, ''):
        challenge = f'{cr_label} ({xp} XP)' if cr_label else f'{xp} XP'
    else:
        challenge = cr_label

    hp = None
    max_hp = None
    temp_hp = 0
    try:
        hp = entity.hp()
    except Exception:
        hp = props.get('hp')
    try:
        max_hp = entity.max_hp()
    except Exception:
        max_hp = props.get('max_hp')
    try:
        temp_hp = entity.temp_hp() or 0
    except Exception:
        temp_hp = 0

    gallery = []
    try:
        gallery = entity.sheet_images() or []
    except Exception:
        gallery = []

    languages = ''
    try:
        from webapp.blueprints.helpers.template_globals import format_languages
        languages = format_languages(entity)
    except Exception:
        langs = props.get('languages') or []
        languages = ', '.join(title_label(item) for item in langs)

    return {
        'name': entity.label() if hasattr(entity, 'label') else str(props.get('kind') or entity),
        'size_type_alignment': format_size_type_alignment(entity),
        'ac': entity.armor_class() if hasattr(entity, 'armor_class') else props.get('default_ac'),
        'hp': hp,
        'max_hp': max_hp,
        'temp_hp': temp_hp,
        'hp_die': props.get('hp_die') or '',
        'speed': format_speed_line(entity),
        'abilities': _ability_rows(entity),
        'saves': format_saves_line(entity),
        'skills': format_skill_entries(entity),
        'senses': format_senses_line(entity),
        'languages': languages or '—',
        'challenge': challenge,
        'proficiency_bonus': signed(getattr(entity, 'proficiency_bonus', lambda: props.get('proficiency_bonus', 2))()),
        'resistances': _defense_line(defenses.get('resistances')),
        'immunities': _defense_line(defenses.get('immunities')),
        'vulnerabilities': _defense_line(defenses.get('vulnerabilities')),
        'condition_immunities': _defense_line(defenses.get('condition_immunities')),
        'conditions': conditions,
        'traits': _trait_entries(entity),
        'actions': actions,
        'bonus_actions': bonus_actions,
        'reactions': reactions,
        'legendary_actions': legendary,
        'spellcasting': _spellcasting_block(entity),
        'sidekick': _sidekick_note(entity),
        'gallery': gallery,
        'profile_image': entity.profile_image() if hasattr(entity, 'profile_image') else '',
        'familiar': bool(getattr(entity, 'familiar', lambda: False)()),
        'owner_label': getattr(getattr(entity, 'owner', None), 'label', lambda: '')() if getattr(entity, 'owner', None) else '',
    }


def _entity_is_npc(entity) -> bool:
    checker = getattr(entity, 'is_npc', None)
    if callable(checker):
        return bool(checker())
    return False


def is_player_controlled_npc(entity, *, owners: Optional[Iterable[str]] = None,
                             game_session=None) -> bool:
    """True when the NPC is driven by players (sidekick, familiar, assigned)."""
    if entity is None or not _entity_is_npc(entity):
        return False
    if getattr(entity, 'familiar', lambda: False)():
        return True
    owner_list = list(owners) if owners is not None else None
    if owner_list is None:
        try:
            from webapp.blueprints.helpers.template_globals import entity_owners
            owner_list = list(entity_owners(entity) or [])
        except Exception:
            owner_list = []
    if any(str(name or '').strip() for name in owner_list):
        return True
    try:
        from natural20.sidekick import is_in_party_sidekick
        sess = game_session
        if sess is None:
            from webapp.blueprints.helpers.runtime_state import get_game_session
            sess = get_game_session()
        if is_in_party_sidekick(sess, entity):
            return True
    except Exception:
        pass
    return False


def can_view_npc_stat_block(entity, *, role=None, username=None,
                            owners: Optional[Iterable[str]] = None,
                            game_session=None) -> bool:
    """DM always; players only for player-controlled NPCs."""
    if not _entity_is_npc(entity):
        return True
    if 'dm' in (role or []):
        return True
    return is_player_controlled_npc(entity, owners=owners, game_session=game_session)


def can_manage_npc_sheet(entity, *, role=None, username=None) -> bool:
    """Equipment / rest / spells-tab for DM or this NPC's controllers."""
    if 'dm' in (role or []):
        return True
    if not username:
        return False
    try:
        from webapp.blueprints.helpers.template_globals import controller_of
        uid = getattr(entity, 'entity_uid', None)
        return bool(uid and controller_of(uid, username))
    except Exception:
        return False


def _all_usernames():
    from webapp.blueprints.helpers.runtime_state import get_current_game, get_logins

    configured = [
        login.get('name')
        for login in (get_logins() or [])
        if isinstance(login, dict) and isinstance(login.get('name'), str) and login.get('name').strip()
    ]
    game = get_current_game()
    connected = [
        name
        for name in ((getattr(game, 'username_to_sid', None) or {}).keys() if game else [])
        if isinstance(name, str) and name.strip()
    ]
    return sorted(set(configured + connected))


def render_info_page(entity, battle=None):
    """Render ``info.html.jinja`` with NPC stat-block access flags."""
    from webapp.blueprints.helpers.auth_utils import user_role

    role = user_role()
    username = session.get('username')
    is_npc = _entity_is_npc(entity)
    view_stats = (not is_npc) or can_view_npc_stat_block(entity, role=role, username=username)
    manage = (not is_npc) or can_manage_npc_sheet(entity, role=role, username=username)
    npc_block = build_npc_stat_block(entity) if is_npc and view_stats else None
    return render_template(
        'info.html.jinja',
        entity=entity,
        battle=battle,
        restricted=not view_stats,
        can_manage_sheet=manage,
        is_npc_sheet=is_npc,
        npc_block=npc_block,
        npc_has_spells=bool(npc_block and npc_block.get('spellcasting')),
        role=role,
        all_users=_all_usernames(),
    )


def lookup_info_entity(info_id):
    """Resolve an entity or object on the viewer's current map."""
    from webapp.blueprints.helpers.runtime_state import get_current_game

    if not info_id:
        return None, (jsonify(error='Missing required id'), 400)
    game = get_current_game()
    battle_map = game.get_map_for_user(session.get('username'))
    entity = battle_map.entity_by_uid(info_id) if battle_map else None
    if entity is None and battle_map is not None:
        entity = battle_map.object_by_uid(info_id)
    if entity is None:
        return None, (jsonify(error='Entity not found'), 404)
    battle = game.get_current_battle()
    return (entity, battle), None
