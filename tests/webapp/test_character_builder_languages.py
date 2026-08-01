"""Character builder language pool filtering."""

from webapp.blueprints.helpers.character_builder_utils import (
    collect_granted_languages,
    filter_language_pool,
    validate_background_language_selections,
)


def test_collect_granted_languages_from_race_and_background():
    race_def = {'languages': ['common', 'elvish']}
    background_def = {'languages': ['dwarvish'], 'languages_pool': ['common', 'orcish']}
    granted = collect_granted_languages(race_def=race_def, background_def=background_def)
    assert granted == {'common', 'elvish', 'dwarvish'}


def test_filter_background_pool_excludes_granted():
    pool = ['common', 'elvish', 'dwarvish', 'common']
    granted = {'common'}
    filtered = filter_language_pool(pool, granted)
    assert filtered == ['elvish', 'dwarvish']


def test_validate_background_language_rejects_granted_pick():
    race_def = {'languages': ['common']}
    background_def = {
        'languages': [],
        'languages_pool': ['common', 'elvish', 'dwarvish'],
        'language_choice_count': 2,
    }
    ok, err = validate_background_language_selections(
        background_def,
        ['common', 'elvish'],
        race_def=race_def,
    )
    assert not ok
    assert err == 'Invalid background language choices'


def test_validate_background_language_accepts_filtered_pool():
    race_def = {'languages': ['common']}
    background_def = {
        'languages': [],
        'languages_pool': ['common', 'elvish', 'dwarvish', 'gnomish'],
        'language_choice_count': 2,
    }
    ok, err = validate_background_language_selections(
        background_def,
        ['elvish', 'dwarvish'],
        race_def=race_def,
    )
    assert ok
    assert err is None
