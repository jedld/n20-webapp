from types import SimpleNamespace

from webapp.tts.npc_voice import (
    build_npc_voice_prompt,
    infer_voice_profile,
    normalize_gender,
    voice_character_instruct,
)


class TestNpcVoiceProfile:
    def test_infer_male_barkeeper_from_prompt(self):
        profile = infer_voice_profile('Gravelly dockside barkeeper, dry humor, low voice')
        assert profile['gender'] == 'male'

    def test_infer_female_from_label_and_description(self):
        entity = SimpleNamespace(
            properties={
                'label': 'Sister Agnes',
                'description': 'A nervous young woman in cathedral silk.',
                'race': ['human'],
            }
        )
        profile = infer_voice_profile('', entity)
        assert profile['gender'] == 'female'

    def test_explicit_yaml_gender_overrides_prompt_hints(self):
        entity = SimpleNamespace(
            properties={
                'gender': 'female',
                'voice': {
                    'prompt': 'Gravelly dockside barkeeper, dry humor, low voice',
                },
            }
        )
        profile = infer_voice_profile('Gravelly dockside barkeeper, dry humor, low voice', entity)
        assert profile['gender'] == 'female'

    def test_whisper_infers_female_from_pronouns(self):
        entity = SimpleNamespace(
            properties={
                'kind': 'Whisper',
                'description': 'Nervous informant who sold Marcus Reed\'s last meeting for escape money she never used.',
            }
        )
        profile = infer_voice_profile('', entity)
        assert profile['gender'] == 'female'

    def test_build_npc_voice_prompt_prefers_yaml_voice_prompt(self):
        entity = SimpleNamespace(
            properties={
                'label': 'Tavern Keeper',
                'voice': {
                    'prompt': 'Gravelly dockside barkeeper, dry humor, low voice',
                },
            }
        )
        assert build_npc_voice_prompt(entity) == 'Gravelly dockside barkeeper, dry humor, low voice'

    def test_voice_character_instruct_includes_gender_and_prompt(self):
        profile = infer_voice_profile('Gravelly dockside barkeeper, dry humor, low voice')
        instruct = voice_character_instruct(profile, 'Gravelly dockside barkeeper, dry humor, low voice')
        assert 'barkeeper' in instruct.lower()
        assert 'male' in instruct.lower()

    def test_bartender_infers_mature_age_band(self):
        entity = SimpleNamespace(
            properties={
                'label': 'Mara (Bartender)',
                'description': 'Stout human bartender with rolled sleeves and a no-nonsense smile.',
                'gender': 'female',
            }
        )
        profile = infer_voice_profile('', entity)
        assert profile['age_band'] == 'mature'
        instruct = voice_character_instruct(profile, build_npc_voice_prompt(entity))
        assert 'mature' in instruct.lower() or 'middle-aged' in instruct.lower()

    def test_normalize_gender_aliases(self):
        assert normalize_gender('Female') == 'female'
        assert normalize_gender('m') == 'male'
