"""Campaign-level character builder limits (level range and blacklists)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from .character_builder_levels import BUILDER_MAX_LEVEL, BUILDER_MIN_LEVEL


def _normalize_key(value: Any) -> str:
    return str(value or '').strip().lower().replace(' ', '_')


def _normalize_slug_set(values: Iterable[Any] | None) -> Set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    return {_normalize_key(v) for v in values if str(v or '').strip()}


@dataclass(frozen=True)
class CharacterBuilderRestrictions:
    """Resolved builder limits for the active campaign."""

    min_level: int = BUILDER_MIN_LEVEL
    max_level: int = BUILDER_MAX_LEVEL
    blacklisted_classes: Set[str] = field(default_factory=set)
    blacklisted_spells: Set[str] = field(default_factory=set)
    blacklisted_feats: Set[str] = field(default_factory=set)
    blacklisted_abilities: Set[str] = field(default_factory=set)
    allowed_backgrounds: Set[str] = field(default_factory=set)
    blacklisted_backgrounds: Set[str] = field(default_factory=set)

    def clamp_level(self, level: Any) -> int:
        try:
            lvl = int(level or self.min_level)
        except (TypeError, ValueError):
            lvl = self.min_level
        return max(self.min_level, min(self.max_level, lvl))

    def class_allowed(self, klass: str | None) -> bool:
        key = _normalize_key(klass)
        return bool(key) and key not in self.blacklisted_classes

    def spell_allowed(self, spell_slug: str | None) -> bool:
        return _normalize_key(spell_slug) not in self.blacklisted_spells

    def feat_allowed(self, feat: str | None) -> bool:
        key = _normalize_key(feat)
        return key not in self.blacklisted_feats and key not in self.blacklisted_abilities

    def ability_allowed(self, ability: str | None) -> bool:
        return _normalize_key(ability) not in self.blacklisted_abilities

    def background_allowed(self, background: str | None) -> bool:
        key = _normalize_key(background)
        if not key:
            return False
        if key in self.blacklisted_backgrounds:
            return False
        if self.allowed_backgrounds and key not in self.allowed_backgrounds:
            return False
        return True

    def filter_backgrounds(self, backgrounds: Dict[str, Any] | None) -> Dict[str, Any]:
        if not backgrounds:
            return {}
        return {
            name: data for name, data in backgrounds.items()
            if self.background_allowed(name)
        }

    def filter_classes(self, classes_def: Dict[str, Any] | None) -> Dict[str, Any]:
        if not classes_def:
            return {}
        filtered: Dict[str, Any] = {}
        for klass, class_def in classes_def.items():
            if not self.class_allowed(klass):
                continue
            entry = copy.deepcopy(class_def or {})
            spell_list = entry.get('spell_list')
            if isinstance(spell_list, dict):
                entry['spell_list'] = self.filter_spell_list(spell_list)
            feat_options = entry.get('feat_choices') or entry.get('available_feats')
            if isinstance(feat_options, list):
                feats = [f for f in feat_options if self.feat_allowed(f)]
                if entry.get('feat_choices') is not None:
                    entry['feat_choices'] = feats
                if entry.get('available_feats') is not None:
                    entry['available_feats'] = feats
            filtered[klass] = entry
        return filtered

    def filter_spell_list(self, spell_list: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(spell_list, dict):
            return {}
        filtered: Dict[str, Any] = {}
        for key, spells in spell_list.items():
            if not isinstance(spells, list):
                filtered[key] = spells
                continue
            kept = [s for s in spells if self.spell_allowed(s)]
            if kept:
                filtered[key] = kept
        return filtered

    def filter_features(self, features: Iterable[str] | None) -> List[str]:
        return [f for f in (features or []) if self.ability_allowed(f)]

    def to_client_dict(self) -> Dict[str, Any]:
        return {
            'min_level': self.min_level,
            'max_level': self.max_level,
            'blacklist': {
                'classes': sorted(self.blacklisted_classes),
                'spells': sorted(self.blacklisted_spells),
                'feats': sorted(self.blacklisted_feats),
                'abilities': sorted(self.blacklisted_abilities),
                'backgrounds': sorted(self.blacklisted_backgrounds),
            },
            'allowed_backgrounds': sorted(self.allowed_backgrounds),
        }

    def validate_selection(
        self,
        *,
        klass: str,
        level: Any,
        cantrips: Iterable[str] | None = None,
        spells_by_level: Dict[int, Iterable[str]] | None = None,
        feats: Iterable[str] | None = None,
        subclass: str | None = None,
    ) -> List[str]:
        errors: List[str] = []
        if not self.class_allowed(klass):
            errors.append(f'Class "{klass}" is not allowed in this campaign.')

        try:
            raw_level = int(level or self.min_level)
        except (TypeError, ValueError):
            raw_level = self.min_level
        if raw_level < self.min_level or raw_level > self.max_level:
            errors.append(
                f'Level must be between {self.min_level} and {self.max_level} for this campaign.'
            )

        for spell in cantrips or []:
            if not self.spell_allowed(spell):
                errors.append(f'Spell "{spell}" is not allowed in this campaign.')

        for spell_level, spells in (spells_by_level or {}).items():
            for spell in spells or []:
                if not self.spell_allowed(spell):
                    errors.append(
                        f'Spell "{spell}" (level {spell_level}) is not allowed in this campaign.'
                    )

        for feat in feats or []:
            if not self.feat_allowed(feat):
                errors.append(f'Feat "{feat}" is not allowed in this campaign.')

        if subclass and not self.ability_allowed(subclass):
            errors.append(f'Subclass "{subclass}" is not allowed in this campaign.')

        return errors


def _merge_character_builder_config(
    game_properties: Dict[str, Any] | None,
    index_data: Dict[str, Any] | None,
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for source in (index_data or {}, (game_properties or {})):
        block = source.get('character_builder')
        if isinstance(block, dict):
            merged.update(block)
    return merged


def load_character_builder_restrictions(
    game_session=None,
    index_data: Optional[Dict[str, Any]] = None,
) -> CharacterBuilderRestrictions:
    """Load builder limits from ``game.yml`` (preferred) and ``index.json``."""
    if game_session is None:
        from .runtime_state import get_game_session, get_index_data

        game_session = get_game_session()
        if index_data is None:
            try:
                index_data = get_index_data()
            except KeyError:
                index_data = {}

    game_properties = getattr(game_session, 'game_properties', None) or {}
    cfg = _merge_character_builder_config(game_properties, index_data)

    min_level = cfg.get('min_level', BUILDER_MIN_LEVEL)
    max_level = cfg.get('max_level', BUILDER_MAX_LEVEL)
    try:
        min_level = int(min_level)
    except (TypeError, ValueError):
        min_level = BUILDER_MIN_LEVEL
    try:
        max_level = int(max_level)
    except (TypeError, ValueError):
        max_level = BUILDER_MAX_LEVEL
    if min_level < 1:
        min_level = 1
    if max_level < min_level:
        max_level = min_level

    blacklist = cfg.get('blacklist') or {}
    if not isinstance(blacklist, dict):
        blacklist = {}

    return CharacterBuilderRestrictions(
        min_level=min_level,
        max_level=max_level,
        blacklisted_classes=_normalize_slug_set(blacklist.get('classes')),
        blacklisted_spells=_normalize_slug_set(blacklist.get('spells')),
        blacklisted_feats=_normalize_slug_set(blacklist.get('feats')),
        blacklisted_abilities=_normalize_slug_set(blacklist.get('abilities')),
        allowed_backgrounds=_normalize_slug_set(cfg.get('allowed_backgrounds')),
        blacklisted_backgrounds=_normalize_slug_set(blacklist.get('backgrounds')),
    )
