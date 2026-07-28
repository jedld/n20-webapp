"""Level 1–5 character builder rules (HP, spells, subclasses, ASI, progression)."""

from __future__ import annotations

import re

BUILDER_MAX_LEVEL = 5
BUILDER_MIN_LEVEL = 1

# (property name on PC YAML, YAML block listing subclass options/features)
SUBCLASS_CONFIG = {
    'barbarian': ('primal_path', 'primal_paths'),
    'bard': ('bard_college', 'bard_colleges'),
    'cleric': ('divine_domain', 'divine_domain_features'),
    'druid': ('druid_circle', 'druid_circles'),
    'fighter': ('martial_archetype', 'martial_archetypes'),
    'monk': ('monastic_tradition', 'monastic_traditions'),
    'paladin': ('sacred_oath', 'sacred_oaths'),
    'ranger': ('ranger_archetype', 'ranger_archetypes'),
    'rogue': ('roguish_archetype', 'roguish_archetype_features'),
    'sorcerer': ('sorcerous_origin', 'sorcerous_origins'),
    'warlock': ('otherworldly_patron', 'otherworldly_patrons'),
    'wizard': ('arcane_tradition', 'arcane_tradition_features'),
}

KNOWN_SPELL_CLASSES = {'bard', 'sorcerer', 'warlock', 'ranger'}
PREPARED_SPELL_CLASSES = {'wizard', 'cleric', 'druid', 'paladin'}
PACT_SPELL_CLASS = 'warlock'


def _ability_mod(score):
    try:
        return (int(score) - 10) // 2
    except (TypeError, ValueError):
        return 0


def _slot_tables():
    from natural20.entity_class.wizard import WIZARD_SPELL_SLOT_TABLE
    from natural20.entity_class.cleric import CLERIC_SPELL_SLOT_TABLE
    from natural20.entity_class.druid import DRUID_SPELL_SLOT_TABLE
    from natural20.entity_class.bard import BARD_SPELL_SLOT_TABLE
    from natural20.entity_class.warlock import WARLOCK_SPELL_SLOT_TABLE
    from natural20.entity_class.sorcerer import SORCERER_SPELL_SLOT_TABLE
    from natural20.entity_class.paladin import PALADIN_SPELL_SLOT_TABLE
    from natural20.entity_class.ranger import RANGER_SPELL_SLOT_TABLE

    return {
        'wizard': WIZARD_SPELL_SLOT_TABLE,
        'cleric': CLERIC_SPELL_SLOT_TABLE,
        'druid': DRUID_SPELL_SLOT_TABLE,
        'bard': BARD_SPELL_SLOT_TABLE,
        'warlock': WARLOCK_SPELL_SLOT_TABLE,
        'sorcerer': SORCERER_SPELL_SLOT_TABLE,
        'paladin': PALADIN_SPELL_SLOT_TABLE,
        'ranger': RANGER_SPELL_SLOT_TABLE,
    }


def _known_spell_table(klass_lower):
    tables = {
        'bard': [0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14,
                 15, 15, 16, 18, 19, 19, 20, 22, 22, 22],
        'warlock': [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10,
                    11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
        'sorcerer': [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
                     12, 12, 13, 13, 14, 14, 15, 15, 15, 15],
        'ranger': [0, 0, 2, 3, 3, 4, 4, 5, 5, 6, 6,
                   7, 7, 8, 8, 9, 9, 10, 10, 11, 11],
    }
    return tables.get(klass_lower, [])


def clamp_builder_level(level):
    try:
        lvl = int(level or 1)
    except (TypeError, ValueError):
        lvl = 1
    return max(BUILDER_MIN_LEVEL, min(BUILDER_MAX_LEVEL, lvl))


def subclass_property_name(klass):
    """Return the PC YAML property name for a class subclass, if any."""
    config = SUBCLASS_CONFIG.get(str(klass or '').lower())
    if not config:
        return None
    return config[0]


def extract_subclass_choice(klass, pc):
    """Read the stored subclass choice from a character dict."""
    prop = subclass_property_name(klass)
    if not prop:
        return ''
    return str((pc or {}).get(prop) or '').strip()


def list_subclass_options(klass, class_def):
    klass_lower = str(klass or '').lower()
    config = SUBCLASS_CONFIG.get(klass_lower)
    if not config:
        return []
    _prop, block_key = config
    block = (class_def or {}).get(block_key) or {}
    if isinstance(block, dict):
        return sorted(str(key) for key in block.keys())
    return []


def subclass_required(level):
    return clamp_builder_level(level) >= 3


def asi_required(level):
    return clamp_builder_level(level) >= 4


def max_castable_spell_level(klass, level):
    klass_lower = str(klass or '').lower()
    lvl = clamp_builder_level(level)
    tables = _slot_tables()
    row = tables.get(klass_lower, [])
    if not row or lvl > len(row):
        return 0
    slot_row = row[lvl - 1]
    max_level = 0
    for spell_level, count in enumerate(slot_row[1:], start=1):
        if count and count > 0:
            max_level = spell_level
    return max_level


def pact_slot_level(level):
    lvl = clamp_builder_level(level)
    if lvl <= 1:
        return 1
    if lvl <= 4:
        return 2
    return 3


def spell_choice_caps(klass, level, ability, class_def):
    """Return spell picker limits for the character builder."""
    klass_lower = str(klass or '').lower()
    lvl = clamp_builder_level(level)
    caps = {
        'cantrip_cap': 0,
        'spells_known_cap': 0,
        'spells_prepared_cap': 0,
        'spellbook_cap': 0,
        'max_spell_level': 0,
        'pact_slot_level': 0,
        'slots_by_level': {},
    }

    tables = _slot_tables()
    row = []
    table = tables.get(klass_lower) or []
    if table and lvl <= len(table):
        row = table[lvl - 1]

    if row:
        caps['cantrip_cap'] = max(0, int(row[0] or 0))
        for spell_level, count in enumerate(row[1:], start=1):
            if count and int(count) > 0:
                caps['slots_by_level'][spell_level] = int(count)

    caps['max_spell_level'] = max_castable_spell_level(klass_lower, lvl)
    if klass_lower == PACT_SPELL_CLASS:
        caps['pact_slot_level'] = pact_slot_level(lvl)
        caps['max_spell_level'] = caps['pact_slot_level']

    if klass_lower == 'wizard':
        caps['spellbook_cap'] = 6 + (lvl - 1) * 2
        key = 'int'
        caps['spells_prepared_cap'] = max(
            caps['slots_by_level'].get(1, 0),
            lvl + _ability_mod((ability or {}).get(key, 10)),
        )
    elif klass_lower in ('cleric', 'druid'):
        key = 'wis'
        caps['spells_prepared_cap'] = max(1, lvl + _ability_mod((ability or {}).get(key, 10)))
    elif klass_lower == 'paladin':
        key = 'cha'
        caps['spells_prepared_cap'] = max(1, lvl + _ability_mod((ability or {}).get(key, 10)))
    elif klass_lower in KNOWN_SPELL_CLASSES:
        known = _known_spell_table(klass_lower)
        if known:
            caps['spells_known_cap'] = known[min(lvl, len(known) - 1)]

    return caps


def compute_max_hp(klass, level, con_score, class_def):
    lvl = clamp_builder_level(level)
    hit_die = str((class_def or {}).get('hit_die') or '1d8')
    match = re.match(r'1d(\d+)', hit_die)
    die_size = int(match.group(1)) if match else 8
    con_mod = _ability_mod(con_score)
    first_level_hp = die_size + con_mod
    per_level_hp = max(1, (die_size // 2 + 1) + con_mod)
    if lvl <= 1:
        return max(1, first_level_hp)
    return max(1, first_level_hp + per_level_hp * (lvl - 1))


def collect_progression_features(class_def, level):
    features = list((class_def or {}).get('class_features') or [])
    progression = (class_def or {}).get('progression') or {}
    lvl = clamp_builder_level(level)
    for n in range(1, lvl + 1):
        block = progression.get(f'level_{n}') or {}
        features.extend(block.get('class_features') or [])
    return list(dict.fromkeys(features))


def apply_subclass(pc, klass, subclass, class_def, level):
    if not subclass:
        return
    klass_lower = str(klass or '').lower()
    config = SUBCLASS_CONFIG.get(klass_lower)
    if not config:
        return
    prop_name, block_key = config
    options = list_subclass_options(klass, class_def)
    if options and subclass not in options:
        return
    pc[prop_name] = subclass

    if klass_lower == 'cleric':
        domain_spells = ((class_def or {}).get('divine_domain_spell_list') or {}).get(subclass) or {}
        granted = []
        for n in range(1, clamp_builder_level(level) + 1):
            granted.extend(domain_spells.get(f'level_{n}') or [])
        if granted:
            pc.setdefault('domain_spells', list(dict.fromkeys(granted)))


def apply_asi(pc, asi_boosts):
    ability = pc.setdefault('ability', {})
    for key, bonus in (asi_boosts or {}).items():
        if key not in ability:
            continue
        try:
            ability[key] = min(20, int(ability[key]) + int(bonus))
        except (TypeError, ValueError):
            continue


def validate_asi_boosts(asi_boosts):
    boosts = asi_boosts or {}
    if not boosts:
        return False, 'Choose an ability score improvement (+2 to one ability or +1 to two).'
    total = sum(int(v) for v in boosts.values())
    if total != 2:
        return False, 'Ability score improvements must total +2.'
    if any(int(v) not in (1, 2) for v in boosts.values()):
        return False, 'Each ability can receive +1 or +2 only.'
    if sum(1 for v in boosts.values() if int(v) == 2) > 1:
        return False, 'Only one ability can receive +2.'
    return True, ''


def apply_spell_selections(pc, klass, level, class_def, selected_cantrips, spells_by_level):
    spell_list = (class_def or {}).get('spell_list') or {}
    caps = spell_choice_caps(klass, level, pc.get('ability') or {}, class_def)
    klass_lower = str(klass or '').lower()

    allowed_by_level = {}
    for key, spells in spell_list.items():
        if key == 'cantrip':
            allowed_by_level[0] = set(spells or [])
            continue
        if key.startswith('level_'):
            try:
                spell_level = int(key.split('_', 1)[1])
            except (ValueError, IndexError):
                continue
            allowed_by_level[spell_level] = set(spells or [])

    cantrips = [s for s in (selected_cantrips or []) if s in allowed_by_level.get(0, set())]
    cantrips = cantrips[: caps['cantrip_cap']]

    normalized = {int(k): list(v or []) for k, v in (spells_by_level or {}).items()}
    prepared = []
    for spell_level in sorted(normalized.keys()):
        pool = allowed_by_level.get(spell_level, set())
        if spell_level > caps['max_spell_level']:
            continue
        if klass_lower == PACT_SPELL_CLASS and spell_level > caps['pact_slot_level']:
            continue
        for spell in normalized[spell_level]:
            if spell in pool:
                prepared.append(spell)

    prepared = list(dict.fromkeys(prepared))
    if caps['spells_known_cap']:
        prepared = prepared[: caps['spells_known_cap']]
    if caps['spells_prepared_cap']:
        prepared = prepared[: caps['spells_prepared_cap']]

    all_spells = list(dict.fromkeys(cantrips + prepared))
    if all_spells:
        pc['prepared_spells'] = all_spells
    elif 'prepared_spells' in pc:
        pc.pop('prepared_spells', None)

    if klass_lower == 'wizard' and caps['spellbook_cap'] > 0:
        book = list(prepared)
        for spell_level in sorted(allowed_by_level.keys()):
            if spell_level == 0:
                continue
            for spell in sorted(allowed_by_level[spell_level]):
                if len(book) >= caps['spellbook_cap']:
                    break
                if spell not in book:
                    book.append(spell)
        pc['spellbook'] = book[: caps['spellbook_cap']]
