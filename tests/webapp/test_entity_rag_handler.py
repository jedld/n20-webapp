"""
Test file for EntityRAGHandler

This module provides tests to verify the EntityRAGHandler functionality.
"""

import unittest
from unittest.mock import Mock, MagicMock
from webapp.entity_rag_handler import EntityRAGHandler, PERCEPTION_EMPTY_DESCRIPTION
from unittest.mock import patch
from natural20.player_character import PlayerCharacter
from natural20.action import Action


class TestEntityRAGHandler(unittest.TestCase):
    """Test cases for EntityRAGHandler."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_game_session = Mock()
        self.mock_game_session.game_time = 0
        self.mock_game_session.load_state.return_value = {}
        self.mock_game_session.game_properties = {
            'conversation_item_offers': {
                'scroll_speak_animals_modified': {
                    'aliases': ['scroll_speak_animals'],
                    'prefer_player_character': True,
                    'block_when': [
                        'offer_completed',
                        'target_has_item',
                        'target_effect_animal_communication',
                    ],
                },
            },
        }
        self.mock_current_game = Mock()
        self.mock_current_game.output_logger = Mock()
        self.mock_current_game.output_logger.get_visible_entries_for_entity.return_value = []
        self.mock_current_game.get_map_for_entity.return_value = None
        self.mock_current_game.output_logger = Mock()
        self.rag_handler = EntityRAGHandler(self.mock_game_session, self.mock_current_game)
    
    def test_parse_language_from_response_no_language(self):
        """Test parsing response with no language specification."""
        response = "Hello, how are you?"
        language, text = self.rag_handler.parse_language_from_response(response)
        
        self.assertEqual(language, "common")
        self.assertEqual(text, response)
    
    def test_parse_language_from_response_with_language(self):
        """Test parsing response with language specification."""
        response = "Hello [in elvish] how are you?"
        language, text = self.rag_handler.parse_language_from_response(response)
        
        self.assertEqual(language, "elvish")
        self.assertEqual(text, "how are you?")
    
    def test_parse_language_from_response_malformed(self):
        """Test parsing response with malformed language specification."""
        response = "Hello [in elvish how are you?"
        language, text = self.rag_handler.parse_language_from_response(response)
        
        self.assertEqual(language, "common")
        self.assertEqual(text, response)
    
    def test_validate_language_for_entity_valid(self):
        """Test language validation with valid language."""
        mock_entity = Mock()
        mock_entity.languages.return_value = ["common", "elvish", "dwarvish"]
        
        result = self.rag_handler.validate_language_for_entity("elvish", mock_entity)
        self.assertEqual(result, "elvish")
    
    def test_validate_language_for_entity_invalid(self):
        """Test language validation with invalid language."""
        mock_entity = Mock()
        mock_entity.languages.return_value = ["common", "elvish", "dwarvish"]
        
        result = self.rag_handler.validate_language_for_entity("orcish", mock_entity)
        self.assertEqual(result, "common")  # Should fall back to first language
    
    def test_validate_language_for_entity_no_languages(self):
        """Test language validation with entity that has no languages."""
        mock_entity = Mock()
        mock_entity.languages.return_value = []
        
        result = self.rag_handler.validate_language_for_entity("elvish", mock_entity)
        self.assertEqual(result, "common")  # Should fall back to common
    
    def test_get_entity_context_basic(self):
        """Test getting basic entity context."""
        mock_entity = Mock()
        mock_entity.label.return_value = "Test Entity"
        mock_entity.entity_uid = "test-uid"
        mock_entity.description.return_value = "A test entity"
        mock_entity.languages.return_value = ["common"]
        
        # Mock various entity attributes
        mock_entity.hp.return_value = 20
        mock_entity.max_hp.return_value = 25
        mock_entity.armor_class.return_value = 15
        mock_entity.level.return_value = 5
        mock_entity.race.return_value = "Human"
        mock_entity.class_and_level.return_value = [("Fighter", 5)]
        mock_entity.inventory_items.return_value = [{"label": "Sword"}, {"label": "Shield"}]
        
        # Mock class_descriptor to return None so it falls back to class_and_level
        mock_entity.class_descriptor.return_value = None
        
        # Mock battle map
        mock_battle_map = Mock()
        mock_battle_map.entity_or_object_pos.return_value = (10, 15)
        self.mock_current_game.get_map_for_entity.return_value = mock_battle_map
        
        context = self.rag_handler.get_entity_context(mock_entity)
        
        self.assertEqual(context['name'], "Test Entity")
        self.assertEqual(context['entity_uid'], "test-uid")
        self.assertEqual(context['description'], "A test entity")
        self.assertEqual(context['hp'], 20)
        self.assertEqual(context['max_hp'], 25)
        self.assertEqual(context['ac'], 15)
        self.assertEqual(context['level'], 5)
        self.assertEqual(context['race'], "Human")
        self.assertEqual(context['class'], "Fighter 5")
        self.assertEqual(context['inventory'], ["Sword", "Shield"])
        self.assertEqual(context['position'], (10, 15))

    def test_get_entity_context_includes_portrait_fields(self):
        mock_entity = Mock()
        mock_entity.label.return_value = "Portrait NPC"
        mock_entity.entity_uid = "portrait-npc"
        mock_entity.description.return_value = "A painted soul"
        mock_entity.properties = {
            'profile_image': 'portraits/portrait_npc.jpg',
            'token_image': 'token_npc.png',
        }
        mock_entity.profile_image.return_value = 'portraits/portrait_npc.jpg'
        mock_entity.token_image.return_value = 'token_npc.png'
        mock_entity.inventory_items.return_value = []

        context = self.rag_handler.get_entity_context(mock_entity)

        self.assertTrue(context['has_full_portrait'])
        self.assertEqual(context['profile_image'], 'portraits/portrait_npc.jpg')
        self.assertEqual(context['token_image'], 'token_npc.png')
    
    def test_get_nearby_entities_success(self):
        """Test getting nearby entities successfully."""
        mock_entity = Mock()
        mock_entity.entity_uid = "test-entity"
        
        # Mock nearby entities
        mock_nearby_entity1 = Mock()
        mock_nearby_entity1.entity_uid = "nearby-1"
        mock_nearby_entity1.label.return_value = "Nearby Entity 1"
        mock_nearby_entity1.conversable.return_value = True
        
        mock_nearby_entity2 = Mock()
        mock_nearby_entity2.entity_uid = "nearby-2"
        mock_nearby_entity2.label.return_value = "Nearby Entity 2"
        mock_nearby_entity2.conversable.return_value = False
        
        # Mock entity observe method
        mock_entity.observe.return_value = [
            (mock_nearby_entity1, 15),
            (mock_nearby_entity2, 25)
        ]
        
        # Mock battle map
        mock_battle_map = Mock()
        self.mock_current_game.get_map_for_entity.return_value = mock_battle_map
        
        result = self.rag_handler.get_nearby_entities(mock_entity, 30)
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['id'], "nearby-1")
        self.assertEqual(result[0]['name'], "Nearby Entity 1")
        self.assertEqual(result[0]['distance'], 15)
        self.assertTrue(result[0]['conversable'])
        self.assertEqual(result[1]['id'], "nearby-2")
        self.assertEqual(result[1]['name'], "Nearby Entity 2")
        self.assertEqual(result[1]['distance'], 25)
        self.assertFalse(result[1]['conversable'])
    
    def test_get_nearby_entities_error(self):
        """Test getting nearby entities with error."""
        mock_entity = Mock()
        mock_entity.observe.side_effect = Exception("Test error")
        
        result = self.rag_handler.get_nearby_entities(mock_entity, 30)
        
        self.assertEqual(result, [])

    def test_get_nearby_entities_without_map_returns_empty(self):
        """Missing map should not call observe() with an invalid signature."""
        mock_entity = Mock()
        self.mock_current_game.get_map_for_entity.return_value = None

        result = self.rag_handler.get_nearby_entities(mock_entity, 30)

        self.assertEqual(result, [])
        mock_entity.observe.assert_not_called()

    def test_build_observation_summary_lists_entities_and_objects(self):
        observer = Mock()
        observer.entity_uid = 'npc-1'
        observer.memory_buffer = []
        observer.group = None
        self.mock_current_game.entity_owners.return_value = []

        player = Mock()
        player.entity_uid = 'pc-1'
        player.__class__.__name__ = 'PlayerCharacter'
        player.label.return_value = 'Kael'
        player.properties = {'outward_appearance': 'A tall human in travel-worn leathers.'}
        player.description.return_value = 'Should not be used when appearance is set.'
        player.dead.return_value = False
        player.unconscious.return_value = False
        player.object = Mock(return_value=False)
        player.is_npc = Mock(return_value=False)
        player.conversable = Mock(return_value=True)

        wolf = Mock()
        wolf.entity_uid = 'wolf-1'
        wolf.label.return_value = 'Wolf'
        wolf.properties = {'kind': 'wolf', 'description': 'A snarling wolf.'}
        wolf.description.return_value = 'A snarling wolf.'
        wolf.dead.return_value = False
        wolf.unconscious.return_value = False
        wolf.object = Mock(return_value=False)
        wolf.is_npc = Mock(return_value=False)
        wolf.conversable = Mock(return_value=False)
        wolf.has_notes = Mock(return_value=False)
        wolf.class_and_level = Mock(return_value=[])

        chest = Mock()
        chest.entity_uid = 'chest-1'
        chest.label.return_value = 'Treasure Chest'
        chest.properties = {'description': 'An iron-bound wooden chest.'}
        chest.description.return_value = 'An iron-bound wooden chest.'
        chest.available_interactions.return_value = {'open': {}}

        battle_map = Mock()
        battle_map.entities = {observer: [1, 1], player: [2, 2], wolf: [4, 4]}
        battle_map.interactable_objects = {chest: [3, 3]}
        battle_map.feet_per_grid = 5
        battle_map.distance.side_effect = lambda a, b: 2 if b == player else (3 if b == chest else 4)
        battle_map.can_see.return_value = True
        battle_map.entity_or_object_pos.side_effect = lambda ent: {
            observer: (1, 1),
            player: (2, 2),
            wolf: (4, 4),
            chest: (3, 3),
        }[ent]

        self.mock_current_game.get_map_for_entity.return_value = battle_map

        summary = self.rag_handler.build_observation_summary(observer, range_ft=30)

        self.assertIn('Your position: (1, 1)', summary)
        self.assertIn('Nearby people and creatures:', summary)
        self.assertIn('unfamiliar adventurer', summary)
        self.assertIn('appearance: A tall human in travel-worn leathers.', summary)
        self.assertIn('an unknown wolf', summary)
        self.assertIn('appearance: A wolf.', summary)
        self.assertIn('Interactable objects:', summary)
        self.assertIn('name: Treasure Chest', summary)
        self.assertIn('appearance: An iron-bound wooden chest.', summary)

    def test_observer_knows_entity_after_conversation(self):
        observer = Mock()
        observer.entity_uid = 'npc-1'
        observer.memory_buffer = []
        observer.group = None
        target = Mock()
        target.entity_uid = 'pc-1'
        target.properties = {}
        self.mock_current_game.entity_owners.return_value = []

        self.assertFalse(self.rag_handler._observer_knows_entity(observer, target))

        observer.memory_buffer = [{'source': target, 'directed_to': [observer], 'target': observer}]
        self.assertTrue(self.rag_handler._observer_knows_entity(observer, target))

    def test_build_observation_summary_without_map(self):
        observer = Mock()
        observer.entity_uid = 'npc-1'
        self.mock_current_game.get_map_for_entity.return_value = None

        summary = self.rag_handler.build_observation_summary(observer)

        self.assertIn('cannot make out your surroundings', summary)
    
    def test_process_entity_response_empty(self):
        """Test processing empty entity response."""
        mock_receiver = Mock()
        mock_llm_handler = Mock()
        
        language, response = self.rag_handler.process_entity_response("", mock_receiver, mock_llm_handler)
        
        self.assertEqual(language, "common")
        self.assertEqual(response, "")
    
    def test_process_entity_response_with_rag_commands(self):
        """Test processing entity response with RAG commands."""
        mock_receiver = Mock()
        mock_receiver.languages.return_value = ["common", "elvish"]
        mock_receiver.inventory_items.return_value = [{"label": "Sword"}, {"label": "Shield"}]
        
        mock_llm_handler = Mock()
        mock_llm_handler.add_message = Mock()
        mock_llm_handler.generate_response.return_value = "I have a sword and shield"
        
        # Mock battle map for observation
        mock_battle_map = Mock()
        self.mock_current_game.get_map_for_entity.return_value = mock_battle_map
        
        # Test with inventory command
        response = "What do I have? [INVENTORY]"
        language, processed_response = self.rag_handler.process_entity_response(
            response, mock_receiver, mock_llm_handler
        )
        
        # Verify that the LLM handler was called
        mock_llm_handler.add_message.assert_called()
        mock_llm_handler.generate_response.assert_called()

    def test_process_entity_response_passes_speaker_to_plan_builder(self):
        receiver = Mock()
        receiver.languages.return_value = ["common"]
        speaker = Mock()

        with patch.object(self.rag_handler, 'build_conversation_response_plan', return_value={
            'language': 'common',
            'message': 'ok',
        }) as mock_builder:
            self.rag_handler.process_entity_response("hello", receiver, speaker=speaker, llm_conversation_handler=Mock())

        self.assertTrue(mock_builder.called)
        self.assertIs(mock_builder.call_args.kwargs.get('speaker'), speaker)

    def test_keyword_hook_matches_phrase_with_connector_words(self):
        receiver = Mock()
        receiver.conversation_keywords.return_value = [
            {
                'keyword': 'hunters closing in',
                'update_state': [{'target': 'session', 'state': {'wild_sheep_scene2_started': True}}],
            }
        ]

        response = "The hunters are closing in."

        with patch('webapp.entity_rag_handler.GenericEventHandler') as handler_cls:
            handler_instance = Mock()
            handler_cls.return_value = handler_instance
            updated = self.rag_handler._process_rag_commands(response, speaker=Mock(), receiver=receiver, llm_conversation_handler=None)

        self.assertTrue(handler_instance.handle.called)
        self.assertEqual(updated, response)

    def test_keyword_hook_llm_semantic_fallback(self):
        receiver = Mock()
        receiver.conversation_keywords.return_value = [
            {'keyword': 'hunters closing in', 'update_state': [{'target': 'session', 'state': {'wild_sheep_scene2_started': True}}]}
        ]

        llm_conversation_handler = Mock()
        llm_conversation_handler.llm_hander = Mock()
        llm_conversation_handler.llm_hander.send_message.return_value = '{"matched_keywords": ["hunters closing in"]}'

        response = "Those trackers are almost here."

        with patch('webapp.entity_rag_handler.GenericEventHandler') as handler_cls:
            handler_instance = Mock()
            handler_cls.return_value = handler_instance
            self.rag_handler._process_rag_commands(response, speaker=Mock(), receiver=receiver, llm_conversation_handler=llm_conversation_handler)

        self.assertTrue(handler_instance.handle.called)

    def test_go_hostile_preserves_spoken_line(self):
        receiver = Mock()
        receiver.label.return_value = 'Guard'

        with patch.object(self.rag_handler, '_handle_hostile_state_change') as hostile:
            updated = self.rag_handler._process_rag_commands(
                'Stop, thief! [GO_HOSTILE]',
                speaker=Mock(),
                receiver=receiver,
                llm_conversation_handler=None,
            )

        hostile.assert_called_once_with(receiver)
        self.assertEqual(updated, 'Stop, thief!')

    def test_build_conversation_response_plan_supports_no_response(self):
        mock_receiver = Mock()
        mock_receiver.languages.return_value = ["common"]

        plan = self.rag_handler.build_conversation_response_plan(
            "[NO_RESPONSE]",
            mock_receiver,
            speaker=Mock(),
            llm_conversation_handler=Mock(),
        )

        self.assertTrue(plan['skip'])
        self.assertEqual(plan['message'], "")

    def test_no_response_skips_keyword_llm_lookup(self):
        mock_receiver = Mock()
        mock_receiver.languages.return_value = ["common"]
        mock_receiver.conversation_keywords.return_value = [{'keyword': 'monster'}]

        llm_conversation_handler = Mock()
        llm_conversation_handler.llm_hander = Mock()

        plan = self.rag_handler.build_conversation_response_plan(
            "[NO_RESPONSE]",
            mock_receiver,
            speaker=Mock(),
            llm_conversation_handler=llm_conversation_handler,
        )

        self.assertTrue(plan['skip'])
        llm_conversation_handler.llm_hander.send_message.assert_not_called()

    def test_build_conversation_response_plan_falls_back_to_speaker_when_volume_plan_fails(self):
        speaker = Mock()
        speaker.entity_uid = "gomerin"
        speaker.label.return_value = "Gomerin"

        receiver = Mock()
        receiver.entity_uid = "rose_durst2"
        receiver.languages.return_value = ["common"]

        self.rag_handler.plan_response_volume = Mock(return_value=(None, []))

        plan = self.rag_handler.build_conversation_response_plan(
            "[in common] Yes... it's terrible. We're scared.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], "Yes... it's terrible. We're scared.")
        self.assertEqual(plan['targets'], [speaker])
        self.assertEqual(plan['volume'], 'shout')

    def test_build_conversation_response_plan_skips_semantic_keyword_llm(self):
        speaker = Mock()
        speaker.entity_uid = "gomerin"

        receiver = Mock()
        receiver.entity_uid = "rose_durst2"
        receiver.languages.return_value = ["common"]
        receiver.conversation_keywords.return_value = [{'keyword': 'basement'}]

        llm_conversation_handler = Mock()
        llm_conversation_handler.llm_hander = Mock()

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[in common] In the basement! My parents keep it trapped down there.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=llm_conversation_handler,
        )

        self.assertFalse(plan['skip'])
        llm_conversation_handler.llm_hander.send_message.assert_not_called()

    def test_build_conversation_response_plan_parses_targets_and_volume(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"
        speaker.label.return_value = "Speaker"

        target = Mock()
        target.entity_uid = "rose"
        target.label.return_value = "Rose"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common", "elvish"]

        self.rag_handler.get_conversation_targets = Mock(return_value=[speaker, target])
        self.rag_handler.plan_response_volume = Mock(return_value=('shout', [target]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[TO: @rose] [VOLUME: shout] [in elvish] Stay back.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['language'], 'elvish')
        self.assertEqual(plan['message'], 'Stay back.')
        self.assertEqual(plan['targets'], [target])
        self.assertEqual(plan['volume'], 'shout')
        self.assertEqual(plan['tts_emotion'], 'shouting')

    def test_build_conversation_response_plan_parses_emotion_and_tts_instruct(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"
        speaker.label.return_value = "Speaker"

        receiver = Mock()
        receiver.entity_uid = "rose"
        receiver.languages.return_value = ["common"]

        self.rag_handler.get_conversation_targets = Mock(return_value=[speaker])
        self.rag_handler.plan_response_volume = Mock(return_value=('whisper', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[VOLUME: whisper] [EMOTION: fearful] [TTS: trembling, barely holding back tears] "
            "Please! You have to be quiet!",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Please! You have to be quiet!')
        self.assertEqual(plan['volume'], 'whisper')
        self.assertEqual(plan['tts_emotion'], 'fearful')
        self.assertEqual(plan['tts_instruct'], 'trembling, barely holding back tears')

    def test_parse_response_controls_treats_long_emotion_as_instruct(self):
        controls = self.rag_handler.parse_response_controls(
            "[EMOTION: soft and urgent, almost sobbing] Stay quiet."
        )
        self.assertIsNone(controls['emotion'])
        self.assertEqual(controls['tts_instruct'], 'soft and urgent, almost sobbing')
        self.assertEqual(controls['response'], 'Stay quiet.')

    def test_build_conversation_response_plan_parses_approach_and_goal_directives(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        approach_target = Mock()
        approach_target.entity_uid = "door-1"

        self.rag_handler.resolve_named_target = Mock(return_value=approach_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[APPROACH: target=Front Door, distance=10] [SET_GOAL: Open the door and check inside] On my way.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertEqual(plan['approach']['target_info']['target'], approach_target)
        self.assertEqual(plan['approach']['distance_ft'], 5)
        self.assertEqual(plan['set_goal'], 'Open the door and check inside')
        self.assertEqual(plan['message'], 'On my way.')

    def test_build_conversation_response_plan_clamps_entity_approach_distance(self):
        speaker = Mock()
        speaker.entity_uid = "aldric"

        receiver = Mock()
        receiver.entity_uid = "henrik_patron"
        receiver.languages.return_value = ["common"]

        approach_target = Mock()
        approach_target.entity_uid = "aldric"

        self.rag_handler.resolve_named_target = Mock(return_value=approach_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[APPROACH: target=@aldric, distance=15] On my way.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertEqual(plan['approach']['distance_ft'], 5)

    def test_build_conversation_response_plan_parses_request_check_directive(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        request_target = Mock()
        request_target.entity_uid = "pc-1"

        self.rag_handler.resolve_named_target = Mock(return_value=request_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[REQUEST_CHECK: skill=persuasion, target=speaker, dc=14] Convince me.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertEqual(plan['request_check']['skill'], 'persuasion')
        self.assertEqual(plan['request_check']['target'], request_target)
        self.assertEqual(plan['request_check']['dc'], 14)
        self.assertEqual(plan['message'], 'Convince me.')

    def test_resolve_named_target_matches_speaker_handle_without_map(self):
        speaker = Mock()
        speaker.entity_uid = 'aldric'
        speaker.label.return_value = 'Aldric'

        actor = Mock()
        actor.entity_uid = 'finethir'
        self.mock_game_session.entity_by_uid.return_value = None
        self.mock_current_game.get_map_for_entity.return_value = None

        target = self.rag_handler.resolve_named_target(actor, '@aldric', speaker=speaker)
        self.assertIs(target, speaker)

    def test_build_conversation_response_plan_parses_offer_item_directive(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        offer_target = Mock()
        offer_target.entity_uid = "pc-1"

        self.rag_handler.resolve_named_target = Mock(return_value=offer_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "[OFFER_ITEM: item=scroll_speak_animals, target=speaker] Please take this.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertIsNotNone(plan['offer_item'])
        self.assertEqual(plan['offer_item']['item'], 'scroll_speak_animals_modified')
        self.assertEqual(plan['offer_item']['target'], offer_target)
        self.assertEqual(plan['message'], 'Please take this.')

    def test_build_conversation_response_plan_offer_item_prefers_player_in_chain(self):
        pip = Mock(entity_uid='pip_barmaid')
        pc = Mock(spec=PlayerCharacter, entity_uid='aldric')
        receiver = Mock()
        receiver.entity_uid = 'finethir'
        receiver.languages.return_value = ['common']

        self.rag_handler.resolve_named_target = Mock(return_value=pip)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [pip]))

        plan = self.rag_handler.build_conversation_response_plan(
            '[OFFER_ITEM: item=scroll_speak_animals, target=speaker] Take this scroll.',
            receiver,
            speaker=pip,
            llm_conversation_handler=Mock(),
            player_speaker=pc,
        )

        self.assertEqual(plan['offer_item']['target'], pc)

    def test_build_conversation_response_plan_preserves_retrieve_offer_sequence(self):
        speaker = Mock()
        speaker.entity_uid = 'aldric'
        receiver = Mock()
        receiver.entity_uid = 'pip_barmaid'
        receiver.languages.return_value = ['common']
        bar = Mock()
        bar.entity_uid = 'tavern_bar'
        bar.inventory = {'ale_mug': {'qty': 1}, 'bread_loaf': {'qty': 1}}
        bar.transfer = Mock()
        offer_target = Mock()
        offer_target.entity_uid = 'aldric'

        self.rag_handler.resolve_named_target = Mock(
            side_effect=lambda actor, spec, **kwargs: bar if 'tavern' in str(spec) else offer_target
        )
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            '[RETRIEVE: item=ale_mug, target=@tavern_bar] '
            '[RETRIEVE: item=bread_loaf, target=@tavern_bar] '
            '[OFFER_ITEM: item=ale_mug, target=aldric] '
            '[OFFER_ITEM: item=bread_loaf, target=aldric] '
            'Coming right up!',
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        sequence = plan.get('action_sequence') or []
        self.assertEqual([entry.get('kind') for entry in sequence], [
            'retrieve', 'retrieve', 'offer_item', 'offer_item',
        ])
        self.assertEqual(sequence[0]['item'], 'ale_mug')
        self.assertEqual(sequence[1]['item'], 'bread_loaf')
        self.assertEqual(sequence[2]['item'], 'ale_mug')
        self.assertEqual(sequence[3]['item'], 'bread_loaf')

    def test_build_conversation_response_plan_parses_pickup_and_trade_directives(self):
        speaker = Mock()
        speaker.entity_uid = 'speaker'
        receiver = Mock()
        receiver.entity_uid = 'thorn'
        receiver.languages.return_value = ['common']
        trade_target = Mock()
        trade_target.entity_uid = 'pc-1'
        self.rag_handler.resolve_named_target = Mock(return_value=trade_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            '[PICKUP: item=gold_coin, qty=2] [TRADE: target=speaker] Fair trade?',
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertEqual(plan['pickup']['item'], 'gold_coin')
        self.assertEqual(plan['pickup']['qty'], 2)
        self.assertEqual(plan['trade']['target'], trade_target)
        self.assertEqual(plan['message'], 'Fair trade?')

    def test_build_conversation_response_plan_parses_open_shop_directive(self):
        speaker = Mock()
        speaker.entity_uid = 'pc-1'
        receiver = Mock()
        receiver.entity_uid = 'bram'
        receiver.languages.return_value = ['common']
        shop_target = Mock()
        shop_target.entity_uid = 'pc-1'
        self.rag_handler.resolve_named_target = Mock(return_value=shop_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            '[OPEN_SHOP: target=speaker] See anything you like?',
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertIsNotNone(plan['open_shop'])
        self.assertEqual(plan['open_shop']['target'], shop_target)
        self.assertEqual(plan['message'], 'See anything you like?')

    def test_build_conversation_response_plan_parses_accept_gift_directive(self):
        speaker = Mock()
        speaker.entity_uid = 'pc-1'
        receiver = Mock()
        receiver.entity_uid = 'pip'
        receiver.languages.return_value = ['common']
        gift_target = Mock()
        gift_target.entity_uid = 'pc-1'
        self.rag_handler.resolve_named_target = Mock(return_value=gift_target)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            '[ACCEPT_GIFT: target=speaker, item=healing_potion] Thank you, dear.',
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertIsNotNone(plan['accept_gift'])
        self.assertEqual(plan['accept_gift']['target'], gift_target)
        self.assertEqual(plan['accept_gift']['item'], 'healing_potion')
        self.assertEqual(plan['message'], 'Thank you, dear.')

    def test_apply_accept_gift_directive_opens_transfer_dialog(self):
        npc = Mock()
        npc.entity_uid = 'pip'
        npc.label.return_value = 'Pip'
        player = Mock()
        player.entity_uid = 'pc-1'
        player.label.return_value = 'Aldric'

        self.mock_current_game.entity_owners.return_value = ['player1']
        self.rag_handler._log_witnessed_offer_outcome = Mock()

        with patch('webapp.entity_rag_handler.open_item_transfer_dialog', return_value=True) as open_dialog, \
                patch('webapp.entity_rag_handler.register_npc_gift_transfer_session', return_value='gift-session-1'):
            result = self.rag_handler.apply_response_plan_directives(
                {'accept_gift': {'target': player, 'item': 'letter'}},
                npc,
                speaker=player,
            )

        self.assertIn('accept_gift', result['executed_actions'])
        open_dialog.assert_called_once()
        call_kwargs = open_dialog.call_args.kwargs
        self.assertEqual(call_kwargs['operator_entity'], player)
        self.assertEqual(call_kwargs['counterparty_entity'], npc)
        self.assertEqual(call_kwargs['action_type'], 'give')
        self.assertEqual(call_kwargs['transfer_session_id'], 'gift-session-1')

    def test_handle_insight_request_logs_and_regenerates_response(self):
        speaker = Mock()
        speaker.entity_uid = "pc-1"
        speaker.label.return_value = "Rumblebelly"

        receiver = Mock()
        receiver.entity_uid = "npc-1"
        receiver.label.return_value = "Thorn"
        receiver.memory_buffer = [{'source': speaker, 'message': 'I am telling the truth.'}]

        roll = Mock()
        roll.result.return_value = 17
        roll.__str__ = Mock(return_value='1d20+5')
        receiver.insight_check.return_value = roll

        conversation_handler = Mock()
        conversation_handler.generate_response.return_value = 'I believe you.'

        self.rag_handler.resolve_named_target = Mock(return_value=speaker)
        self.rag_handler._evaluate_insight_assessment = Mock(return_value={
            'assessment': 'truthful',
            'reason': 'Their story matches the party history.',
        })

        response = self.rag_handler._handle_insight_request(
            '[INSIGHT: target=speaker]',
            receiver,
            speaker,
            conversation_handler,
        )

        self.assertEqual(response, 'I believe you.')
        conversation_handler.add_message.assert_called_once()

    def test_build_conversation_response_plan_extracts_aside_tag(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Please keep your voice down. [ASIDE: Rose grips Thorn's hand and glances at the dark doorway.]",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Please keep your voice down.')
        self.assertEqual(plan['narrative'], ["Rose grips Thorn's hand and glances at the dark doorway."])
        self.assertEqual(
            [segment['type'] for segment in plan['segments']],
            ['speech', 'aside'],
        )

    def test_build_conversation_response_plan_truncates_multi_beat_monologue(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "corin_vale_guard"
        receiver.languages.return_value = ["common"]

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        long_reply = (
            '[VOLUME: normal] "If you need healing, see Wren." '
            "[ASIDE: Corin scans the square.] "
            '"Now move along." '
            "[ASIDE: Corin watches the speaker's departure.] "
            '"Stay sharp out there."'
        )
        plan = self.rag_handler.build_conversation_response_plan(
            long_reply,
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertIn('If you need healing, see Wren', plan['message'])
        self.assertEqual(plan['narrative'], ['Corin scans the square.'])
        self.assertEqual(
            [segment['type'] for segment in plan['segments']],
            ['speech', 'aside'],
        )
        self.assertEqual(
            [segment['type'] for segment in plan['pending_segments']],
            ['speech', 'aside', 'speech'],
        )
        self.assertEqual(plan['continuation_beats_remaining'], 2)

    def test_build_conversation_response_plan_rewrites_first_person_aside_to_third_person(self):
        speaker = Mock()
        speaker.entity_uid = "speaker"

        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Keep quiet. [ASIDE: I clutch my doll tighter, my eyes wide with terror.]",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Keep quiet.')
        self.assertEqual(plan['narrative'], ['They clutch their doll tighter, their eyes wide with terror.'])

    def test_build_conversation_response_plan_normalizes_llm_split_narrative_to_third_person(self):
        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        speaker = Mock()
        speaker.entity_uid = "speaker"

        llm_conversation_handler = Mock()
        llm_conversation_handler.llm_hander = Mock()
        # The LLM returns first-person narrative that needs to be normalized to third-person
        llm_conversation_handler.llm_hander.send_message.return_value = (
            '{"spoken":"Shh!","narrative":["I clutch my doll tighter while we watch the house."]}'
        )

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Shh!\n\nShe clutches her doll tighter while they watch the house.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=llm_conversation_handler,
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Shh!')
        self.assertEqual(plan['narrative'], ['She clutches her doll tighter while they watch the house.'])
        llm_conversation_handler.llm_hander.send_message.assert_not_called()

    def test_build_conversation_response_plan_filters_private_trust_judgment_and_requests_check(self):
        receiver = Mock()
        receiver.entity_uid = "rose"
        receiver.languages.return_value = ["common"]

        speaker = Mock()
        speaker.entity_uid = "pc-1"
        speaker.label.return_value = "RumbleBelly"

        self.rag_handler.plan_response_volume = Mock(return_value=('whisper', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Please help us. [ASIDE: They do not trust him.]",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Please help us.')
        self.assertEqual(plan['narrative'], [])
        self.assertIsNotNone(plan['request_check'])
        self.assertEqual(plan['request_check']['skill'], 'persuasion')
        self.assertEqual(plan['request_check']['target'], speaker)
        self.assertIsNotNone(plan['request_check'].get('dc'))

    def test_build_conversation_response_plan_infers_intimidation_for_hostile_attitude(self):
        receiver = Mock()
        receiver.entity_uid = "rose"
        receiver.languages.return_value = ["common"]

        speaker = Mock()
        speaker.entity_uid = "pc-1"

        mock_battle = Mock()
        mock_battle.opposing.return_value = True
        mock_battle.allies.return_value = False
        self.mock_current_game.get_current_battle.return_value = mock_battle

        self.rag_handler.plan_response_volume = Mock(return_value=('whisper', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Stay back. [ASIDE: They do not trust him.]",
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['narrative'], [])
        self.assertIsNotNone(plan['request_check'])
        self.assertEqual(plan['request_check']['skill'], 'intimidation')
        self.assertEqual(plan['request_check']['target'], speaker)
        self.assertEqual(plan['request_check']['dc'], 10)

    def test_build_conversation_response_plan_nonverbal_aside_with_request_check(self):
        receiver = Mock()
        receiver.entity_uid = "pip"
        receiver.languages.return_value = ["common"]

        speaker = Mock()
        speaker.entity_uid = "aldric"
        speaker.label.return_value = "Aldric"

        self.rag_handler.resolve_named_target = Mock(return_value=speaker)
        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            (
                "[REQUEST_CHECK: skill=persuasion, target=Aldric] "
                "[ASIDE: Pip huffs, crossing her arms over her chest.]"
            ),
            receiver,
            speaker=speaker,
            llm_conversation_handler=Mock(),
        )

        self.assertTrue(plan['skip'])
        self.assertEqual(plan['message'], '')
        self.assertEqual(plan['narrative'], ['Pip huffs, crossing her arms over her chest.'])
        self.assertIsNotNone(plan['request_check'])
        self.assertEqual(plan['request_check']['skill'], 'persuasion')
        self.assertEqual(plan['request_check']['target'], speaker)
        self.assertEqual(plan['targets'], [speaker])

    def test_multiline_dialogue_joins_without_llm_split(self):
        spoken, narrative, segments = self.rag_handler.extract_narrative_asides(
            "Shh... shh, Thorn. Don't cry. It's okay.\nThere's a monster in our house!",
            llm_conversation_handler=Mock(),
        )
        self.assertEqual(
            spoken,
            "Shh... shh, Thorn. Don't cry. It's okay. There's a monster in our house!",
        )
        self.assertEqual(narrative, [])
        self.assertEqual(segments, [{'type': 'speech', 'text': spoken}])

    def test_extract_narrative_asides_parses_asterisk_stage_direction(self):
        receiver = Mock()
        receiver.entity_uid = "guz"
        receiver.label.return_value = "Guz"

        spoken, narrative, segments = self.rag_handler.extract_narrative_asides(
            '*I swagger through the crowd.* "Listen up!" *I glare at the faces around me.*',
            llm_conversation_handler=Mock(),
            speaker=receiver,
        )

        self.assertEqual(spoken, 'Listen up!')
        self.assertEqual(len(narrative), 2)
        self.assertEqual(
            [segment['type'] for segment in segments],
            ['aside', 'speech', 'aside'],
        )

    def test_extract_narrative_asides_interleaves_stage_direction_lines(self):
        receiver = Mock()
        receiver.entity_uid = "mara"
        receiver.label.return_value = "Mara (Bartender)"

        response = (
            '"Hello there, stranger."\n'
            "Mara wipes down the counter with a clean rag.\n"
            '"Can I get you anything else?"\n'
            "She leans in slightly, watching his reaction."
        )
        spoken, narrative, segments = self.rag_handler.extract_narrative_asides(
            response,
            llm_conversation_handler=Mock(),
            speaker=receiver,
        )

        self.assertEqual(spoken, 'Hello there, stranger. Can I get you anything else?')
        self.assertEqual(
            narrative,
            [
                'Mara wipes down the counter with a clean rag.',
                'She leans in slightly, watching his reaction.',
            ],
        )
        self.assertEqual(
            [segment['type'] for segment in segments],
            ['speech', 'aside', 'speech', 'aside'],
        )

    def test_extract_narrative_asides_via_llm_fallback(self):
        receiver = Mock()
        receiver.entity_uid = "thorn"
        receiver.languages.return_value = ["common"]

        speaker = Mock()
        speaker.entity_uid = "speaker"

        llm_conversation_handler = Mock()
        # The code looks for 'llm_hander' (typo preserved from webapp code)
        llm_conversation_handler.llm_hander = Mock()
        llm_conversation_handler.llm_hander.send_message.return_value = (
            '{"spoken":"Shh! Please be quiet.","narrative":["Rose clutches Thorn and scans the house."]}'
        )
        # Also set 'llm_handler' (correct spelling) in case it's used
        llm_conversation_handler.llm_handler = llm_conversation_handler.llm_hander

        self.rag_handler.plan_response_volume = Mock(return_value=('normal', [speaker]))

        plan = self.rag_handler.build_conversation_response_plan(
            "Shh! Please be quiet.\n\nShe clutches Thorn and scans the house.",
            receiver,
            speaker=speaker,
            llm_conversation_handler=llm_conversation_handler,
        )

        self.assertFalse(plan['skip'])
        self.assertEqual(plan['message'], 'Shh! Please be quiet.')
        self.assertEqual(plan['narrative'], ['She clutches Thorn and scans the house.'])
        self.assertEqual(
            [segment['type'] for segment in plan['segments']],
            ['speech', 'aside'],
        )
        llm_conversation_handler.llm_hander.send_message.assert_not_called()

    def test_apply_response_plan_directives_logs_requested_checks_to_players(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.label.return_value = "Thorn"

        target = Mock()
        target.entity_uid = "pc-1"
        target.label.return_value = "Rumblebelly"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': {'skill': 'intimidation', 'target': target, 'dc': 13},
        }

        result = self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)

        self.assertEqual(result['executed_actions'], ['request_check'])
        self.mock_current_game.output_logger.log.assert_called_once()
        logged_message = self.mock_current_game.output_logger.log.call_args.args[0]
        logged_visibility = self.mock_current_game.output_logger.log.call_args.kwargs['visibility']
        self.assertIn('intimidation', logged_message)
        self.assertEqual(logged_visibility['kind'], 'entities')
        self.assertEqual(set(logged_visibility['entity_uids']), {'npc-1', 'pc-1'})

    def test_apply_response_plan_directives_schedules_goal_and_executes_actions(self):
        actor = Mock()
        actor.entity_uid = "npc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []
        self.mock_current_game.schedule_short_term_goal.return_value = {'goal': 'Inspect the chest'}

        move_action = Mock()
        interact_action = Mock()
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=move_action)
        self.rag_handler.build_interact_action = Mock(return_value=interact_action)

        approach_target = Mock()
        plan = {
            'set_goal': 'Inspect the chest',
            'goal_complete': False,
            'goal_give_up': False,
            'stop_move': False,
            'stop_follow': False,
            'approach': {
                'target_info': {'kind': 'entity', 'target': approach_target},
                'distance_ft': 5,
            },
            'interact': {'target': Mock(), 'action': 'open'},
        }

        result = self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=True)

        self.mock_current_game.schedule_short_term_goal.assert_called_once_with(actor, 'Inspect the chest', speaker=None)
        self.assertEqual(self.mock_current_game.commit_and_update.call_count, 2)
        self.mock_current_game.advance_world_time.assert_called_once_with(seconds=6, trigger_environment=False)
        self.assertEqual(result['executed_actions'], ['approach', 'interact'])

    def test_apply_response_plan_offers_item_even_during_battle(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = Mock()
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
        }

        result = self.rag_handler.apply_response_plan_directives(plan, actor, speaker=target, advance_time=False)
        self.assertEqual(result['executed_actions'], ['offer_item'])
        self.mock_current_game.prompt.assert_called_once()

    def test_apply_response_plan_directives_offers_item_via_prompt(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
        }

        result = self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)

        self.assertEqual(result['executed_actions'], ['offer_item'])
        self.mock_current_game.prompt.assert_called_once()

    def test_apply_response_plan_skips_offer_item_when_open_shop_targets_same_customer(self):
        actor = Mock()
        actor.entity_uid = "mara"
        actor.inventory = {'ale_mug': {'qty': 1}}

        target = Mock()
        target.entity_uid = "mira"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'ale_mug', 'target': target, 'auto_use': False},
            'open_shop': {'target': target},
        }

        with patch.object(self.rag_handler, '_open_merchant_shop', return_value=True) as open_shop_mock:
            result = self.rag_handler.apply_response_plan_directives(
                plan, actor, speaker=target, advance_time=False,
            )

        self.assertEqual(result['executed_actions'], ['open_shop'])
        open_shop_mock.assert_called_once()
        self.mock_current_game.prompt.assert_not_called()

    def test_offer_item_prompt_callback_accept_transfers_item(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()
        self.mock_current_game.socketio = Mock()

        with patch.object(self.rag_handler, '_notify_item_offer_resolution') as notify_mock:
            plan = {
                'set_goal': None,
                'goal_complete': False,
                'goal_give_up': False,
                'approach': None,
                'interact': None,
                'request_check': None,
                'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
            }

            self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)
            callback = self.mock_current_game.prompt.call_args.kwargs['callback']
            callback({'response': 'Yes'})

        actor.deduct_item.assert_called_once_with('scroll_speak_animals_modified', 1)
        target.add_item.assert_called_once_with('scroll_speak_animals_modified', 1)
        self.mock_game_session.save_state.assert_called()
        self.mock_current_game.socketio.emit.assert_called()
        notify_mock.assert_called_once()

    def test_offer_item_prompt_callback_ok_payload_transfers_item(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()
        self.mock_current_game.socketio = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
        }

        self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)
        callback = self.mock_current_game.prompt.call_args.kwargs['callback']
        callback({'response': 'OK.'})

        actor.deduct_item.assert_called_once_with('scroll_speak_animals_modified', 1)
        target.add_item.assert_called_once_with('scroll_speak_animals_modified', 1)

    def test_offer_item_prompt_callback_boolean_true_payload_transfers_item(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()
        self.mock_current_game.socketio = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
        }

        self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)
        callback = self.mock_current_game.prompt.call_args.kwargs['callback']
        callback({'response': True})

        actor.deduct_item.assert_called_once_with('scroll_speak_animals_modified', 1)
        target.add_item.assert_called_once_with('scroll_speak_animals_modified', 1)

    def test_can_offer_item_blocks_when_target_has_animal_communication(self):
        actor = Mock()
        actor.entity_uid = 'finethir'
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = 'aldric'
        target.inventory = {}

        self.mock_game_session.load_state.return_value = {}
        with patch('natural20.utils.conversation_offers.has_animal_communication', return_value=True):
            allowed, reason = self.rag_handler._can_offer_item(
                actor,
                target,
                'scroll_speak_animals_modified',
            )

        self.assertFalse(allowed)
        self.assertEqual(reason, 'target_effect_animal_communication')

    def test_can_offer_item_blocks_after_completed_offer(self):
        actor = Mock()
        actor.entity_uid = 'finethir'
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = 'aldric'
        target.inventory = {}

        self.mock_game_session.load_state.return_value = {
            'completed': {'finethir:aldric:scroll_speak_animals_modified': 12},
        }
        allowed, reason = self.rag_handler._can_offer_item(
            actor,
            target,
            'scroll_speak_animals_modified',
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, 'offer_completed')

    def test_apply_response_plan_skips_repeat_scroll_offer_when_target_has_effect(self):
        actor = Mock()
        actor.entity_uid = 'finethir'
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = 'aldric'
        target.inventory = {}

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()
        self.mock_game_session.load_state.return_value = {}

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {
                'item': 'scroll_speak_animals_modified',
                'target': target,
                'auto_use': False,
            },
        }

        with patch('natural20.utils.conversation_offers.has_animal_communication', return_value=True):
            result = self.rag_handler.apply_response_plan_directives(
                plan,
                actor,
                speaker=target,
                advance_time=False,
            )

        self.assertEqual(result['executed_actions'], [])
        self.mock_current_game.prompt.assert_not_called()

    def test_offer_item_prompt_callback_decline_does_not_transfer_item(self):
        actor = Mock()
        actor.entity_uid = "npc-1"
        actor.inventory = {'scroll_speak_animals_modified': {'qty': 1}}

        target = Mock()
        target.entity_uid = "pc-1"

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = ['player1']
        self.mock_current_game.prompt = Mock()
        self.mock_current_game.socketio = Mock()

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'request_check': None,
            'offer_item': {'item': 'scroll_speak_animals_modified', 'target': target, 'auto_use': False},
        }

        self.rag_handler.apply_response_plan_directives(plan, actor, speaker=None, advance_time=False)
        callback = self.mock_current_game.prompt.call_args.kwargs['callback']
        callback({'response': 'No'})

        actor.deduct_item.assert_not_called()
        target.add_item.assert_not_called()

    def test_sanitize_insight_reason_strips_dm_only_disclosures(self):
        fallback = 'fallback reason'
        cleaned = self.rag_handler._sanitize_insight_reason(
            "Their gaze flickers when they answer. The target is an illusionary "
            "construct created by the house, so there is no biological tell. "
            "Still, the wording feels rehearsed.",
            fallback,
        )
        # Sentences mentioning illusion/construct/created-by-the-house must be
        # dropped, but the in-character cues should survive.
        self.assertNotIn('illusion', cleaned.lower())
        self.assertNotIn('construct', cleaned.lower())
        self.assertNotIn('created by the house', cleaned.lower())
        self.assertIn('gaze flickers', cleaned)
        self.assertIn('rehearsed', cleaned)

    def test_sanitize_insight_reason_falls_back_when_everything_redacted(self):
        fallback = 'You cannot tell for sure.'
        cleaned = self.rag_handler._sanitize_insight_reason(
            "The target is an illusion. Behind the scenes the DM has marked them as undead.",
            fallback,
        )
        self.assertEqual(cleaned, fallback)

    def test_parse_insight_assessment_strips_fenced_json_and_trailing_prose(self):
        default = {
            'assessment': 'uncertain',
            'reason': 'fallback reason',
        }
        raw = (
            '```json\n'
            '{\n'
            '  "assessment": "lie",\n'
            '  "reason": "Their eyes dart away when asked about the scroll."\n'
            '}\n'
            '```As you stand there, the girl\'s frantic whispering continues.'
        )
        parsed = self.rag_handler._parse_insight_assessment_response(raw, default)
        self.assertEqual(parsed['assessment'], 'lie')
        self.assertIn('eyes dart away', parsed['reason'])
        self.assertNotIn('```', parsed['reason'])
        self.assertNotIn('frantic whispering', parsed['reason'])

    def test_parse_insight_assessment_does_not_leak_raw_json_on_failure(self):
        default = {
            'assessment': 'uncertain',
            'reason': 'fallback reason',
        }
        raw = '{not valid json at all, assessment: maybe}'
        parsed = self.rag_handler._parse_insight_assessment_response(raw, default)
        self.assertEqual(parsed['assessment'], 'uncertain')
        self.assertEqual(parsed['reason'], 'fallback reason')

    def test_perception_fallback_includes_outward_appearance(self):
        observer = Mock()
        observer.entity_uid = 'pc-1'
        observer.label.return_value = 'Aldric'
        observer.memory_buffer = []

        target = Mock()
        target.entity_uid = 'pip'
        target.label.return_value = 'Pip'
        target.properties = {
            'outward_appearance': (
                'Cheerful halfling woman with hazel eyes and a white apron.'
            ),
        }
        target.statuses = []
        target.hp = Mock(return_value=7)
        target.max_hp = Mock(return_value=7)

        self.mock_current_game.get_map_for_entity.return_value = None

        result = self.rag_handler._build_perception_description_fallback(
            observer,
            target,
            focus='her apron',
            roll_total=14,
        )

        self.assertIn('halfling', result['description'].lower())
        self.assertIn('apron', result['description'].lower())

    def test_perception_fallback_includes_visible_injury_and_status(self):
        observer = Mock()
        observer.entity_uid = 'pc-1'
        observer.label.return_value = 'Aldric'
        observer.memory_buffer = []

        target = Mock()
        target.entity_uid = 'pip'
        target.label.return_value = 'Pip'
        target.properties = {'outward_appearance': 'A tired human guard.'}
        target.statuses = ['poisoned']
        target.hp = Mock(return_value=2)
        target.max_hp = Mock(return_value=10)
        target.dead = Mock(return_value=False)
        target.unconscious = Mock(return_value=False)

        self.mock_current_game.get_map_for_entity.return_value = None

        result = self.rag_handler._build_perception_description_fallback(
            observer,
            target,
            roll_total=15,
        )

        self.assertIn('unwell', result['description'].lower())
        self.assertIn('badly hurt', result['description'].lower())

    def test_evaluate_perception_description_uses_llm_hander_and_fallback_on_empty(self):
        observer = Mock()
        observer.entity_uid = 'pc-1'
        observer.label.return_value = 'Aldric'
        observer.memory_buffer = []

        target = Mock()
        target.entity_uid = 'pip'
        target.label.return_value = 'Pip'
        target.properties = {
            'outward_appearance': 'Bright-eyed halfling barmaid in a green dress.',
        }
        target.statuses = []
        target.hp = Mock(return_value=7)
        target.max_hp = Mock(return_value=7)
        target.backstory = Mock(return_value='')
        target.memory_buffer = []

        roll = Mock()
        roll.result.return_value = 14

        llm_handler = Mock()
        llm_handler.send_message.return_value = (
            '{"description": "' + PERCEPTION_EMPTY_DESCRIPTION + '"}'
        )
        llm_conversation = Mock()
        llm_conversation.llm_hander = llm_handler

        self.mock_current_game.get_map_for_entity.return_value = None

        result = self.rag_handler._evaluate_perception_description(
            observer,
            target,
            focus='',
            roll=roll,
            llm_conversation_handler=llm_conversation,
        )

        llm_handler.send_message.assert_called_once()
        self.assertNotEqual(result['description'], PERCEPTION_EMPTY_DESCRIPTION)
        self.assertIn('halfling', result['description'].lower())

    def test_map_landmarks_prompt_includes_cross_map_known_places(self):
        ground_floor = Mock()
        ground_floor.name = 'town_market'
        ground_floor.map_annotations.return_value = [
            {'id': 'taproom', 'label': 'Taproom', 'kind': 'area', 'bounds': {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2}},
        ]
        ground_floor.map_annotation_by_id.side_effect = lambda ann_id: (
            {'id': 'taproom', 'label': 'Taproom', 'kind': 'area', 'bounds': {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2}}
            if ann_id == 'taproom' else None
        )
        ground_floor.map_annotations_at.return_value = []

        upstairs = Mock()
        upstairs.name = 'tavern_2nd_floor'
        upstairs.map_annotations.return_value = []
        upstairs.map_annotation_by_id.side_effect = lambda ann_id: (
            {
                'id': 'landmark_standard_room_1',
                'label': 'Standard Room 1',
                'kind': 'point',
                'pos': [7, 1],
                'description': 'Tavern Standard Room # 1',
            }
            if ann_id == 'landmark_standard_room_1' else None
        )

        self.mock_game_session.maps = {
            'town_market': ground_floor,
            'tavern_2nd_floor': upstairs,
        }

        npc = Mock()
        npc.properties = {
            'known_places': ['taproom', 'landmark_standard_room_1'],
        }
        self.mock_current_game.get_map_for_entity.return_value = ground_floor

        prompt = self.rag_handler.map_landmarks_prompt_for_entity(npc)

        self.assertIn('Taproom', prompt)
        self.assertIn('Standard Room 1', prompt)
        self.assertIn('upstairs (tavern_2nd_floor)', prompt)

    def test_map_landmarks_prompt_includes_current_location(self):
        battle_map = Mock()
        battle_map.name = 'town_market'
        battle_map.properties = {
            'map_annotations': [
                {
                    'id': 'taproom',
                    'label': 'Prancing Flagon taproom',
                    'kind': 'area',
                    'bounds': {'x1': 7, 'y1': 16, 'x2': 14, 'y2': 24},
                    'description': 'Main common room.',
                },
                {
                    'id': 'behind_the_bar',
                    'label': 'Behind the bar',
                    'kind': 'area',
                    'bounds': {'x1': 6, 'y1': 16, 'x2': 10, 'y2': 21},
                    'description': 'Staff side of the counter.',
                },
            ],
        }
        battle_map.map_annotations.return_value = battle_map.properties['map_annotations']
        battle_map.map_annotation_by_id.side_effect = lambda ann_id: next(
            (ann for ann in battle_map.properties['map_annotations'] if ann['id'] == ann_id),
            None,
        )
        battle_map.map_annotations_at.return_value = []
        battle_map.position_of.return_value = (9, 20)

        self.mock_game_session.maps = {'town_market': battle_map}

        npc = Mock()
        npc.properties = {'known_places': ['taproom']}

        self.mock_current_game.get_map_for_entity.return_value = battle_map

        prompt = self.rag_handler.map_landmarks_prompt_for_entity(npc)

        self.assertIn('Current location on town_market at grid 9,20', prompt)
        self.assertIn('Behind the bar', prompt)
        self.assertNotIn('- (here) Behind the bar', prompt)

    def test_campaign_setting_prompt_from_game_properties(self):
        self.mock_game_session.game_properties = {
            'campaign_setting': {
                'world': 'Forgotten Realms',
                'location': 'Amphail',
                'summary': 'A cattle town on the Long Road.',
            },
        }
        prompt = self.rag_handler.campaign_setting_prompt()
        self.assertIn('Amphail', prompt)
        self.assertIn('Forgotten Realms', prompt)

    def test_parse_action_directives_use_item_self_target(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.inventory = {'healing_potion': {'qty': 2}}

        result = self.rag_handler.parse_action_directives(
            '[USE_ITEM: item=healing_potion, target=self]',
            actor,
            speaker=None,
        )

        self.assertEqual(result['use_item']['item'], 'healing_potion')
        self.assertIs(result['use_item']['target'], actor)

    def test_parse_action_directives_interact_resolves_object_target(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        door = Mock()
        door.entity_uid = 'door-1'
        door.type = 'tavern_suite_door'
        door._name = 'Suite Door'

        self.rag_handler.resolve_interact_target = Mock(return_value=door)

        result = self.rag_handler.parse_action_directives(
            '[INTERACT: target=@tavern_suite_door, action=unlock]',
            actor,
            speaker=None,
        )

        self.rag_handler.resolve_interact_target.assert_called_once()
        self.assertIs(result['interact']['target'], door)
        self.assertEqual(result['interact']['action'], 'unlock')

    def test_resolve_interact_target_matches_object_alias(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        door = Mock()
        door.entity_uid = 'door-1'
        door.type = 'tavern_suite_door'
        door._name = 'Suite Door'

        self.rag_handler.get_interactable_objects = Mock(return_value=[{
            'subject': door,
            'mention_handle': 'tavern_suite_door',
            'name': 'Suite Door',
        }])
        self.rag_handler.resolve_named_target = Mock(return_value=None)

        resolved = self.rag_handler.resolve_interact_target(actor, 'suite door', speaker=None)

        self.assertIs(resolved, door)

    def test_normalize_interaction_name_aliases(self):
        self.assertEqual(
            self.rag_handler._normalize_interaction_name('unlock', ['open', 'unlock']),
            'unlock',
        )
        self.assertEqual(
            self.rag_handler._normalize_interaction_name('pick', ['lockpick', 'open']),
            'lockpick',
        )
        self.assertIsNone(
            self.rag_handler._normalize_interaction_name('fly', ['open', 'close']),
        )

    def test_apply_response_plan_directives_executes_use_item(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        use_action = Mock()

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []
        self.rag_handler.build_use_item_action = Mock(return_value=use_action)

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'use_item': {'item': 'healing_potion', 'target': actor},
        }

        result = self.rag_handler.apply_response_plan_directives(
            plan,
            actor,
            speaker=None,
            advance_time=False,
        )

        self.rag_handler.build_use_item_action.assert_called_once_with(
            actor,
            'healing_potion',
            target=actor,
            speaker=None,
        )
        self.mock_current_game.commit_and_update.assert_called_once()
        self.assertEqual(result['executed_actions'], ['use_item'])

    def test_apply_response_plan_directives_interact_during_battle(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        door = Mock()
        interact_action = Mock()
        battle = Mock()

        self.mock_current_game.get_current_battle.return_value = battle
        self.mock_current_game.entity_owners.return_value = []
        self.rag_handler.build_interact_action = Mock(return_value=interact_action)

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': {'target': door, 'action': 'open'},
            'use_item': None,
        }

        result = self.rag_handler.apply_response_plan_directives(
            plan,
            actor,
            speaker=None,
            advance_time=False,
        )

        self.rag_handler.build_interact_action.assert_called_once_with(actor, door, action_name='open')
        self.mock_current_game.commit_and_update.assert_called_once()
        self.assertEqual(result['executed_actions'], ['interact'])

    @patch('webapp.entity_rag_handler.UseItemAction')
    def test_build_use_item_action_resolves_targeted_item(self, mock_use_item_action):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.inventory = {'healing_potion': {'qty': 1}}
        target = Mock()
        target.entity_uid = 'pc-1'

        final_action = Action(self.mock_game_session, actor, 'use_item')
        final_action.target = None

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_game_session.load_equipment.return_value = {
            'usable': True,
            'name': 'healing_potion',
            'item_class': 'HealingPotion',
        }
        mock_use_item_action.can.return_value = True
        mock_use_item_action.build.return_value = {
            'next': lambda item_slug: {
                'param': [{'type': 'select_target'}],
                'next': lambda resolved_target: final_action,
            },
        }

        built = self.rag_handler.build_use_item_action(
            actor,
            'healing_potion',
            target=target,
        )

        self.assertIs(built, final_action)
        self.assertIs(built.target, target)

    def test_resolve_inventory_container_slug_matches_backpack(self):
        actor = Mock()
        actor.inventory = {
            'backpack': {'qty': 1, 'contents': [{'type': 'healing_potion', 'qty': 2}], 'is_container': True},
        }
        actor.is_container = Mock(side_effect=lambda name, session=None: name == 'backpack')
        actor.get_container_contents = Mock(return_value=[{'type': 'healing_potion', 'qty': 2}])
        actor._item_definition = Mock(return_value={'label': 'Backpack', 'type': 'container'})

        resolved = self.rag_handler._resolve_container_target(actor, 'backpack', speaker=None)

        self.assertEqual(resolved, 'backpack')

    def test_apply_response_plan_retrieves_from_backpack(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.inventory = {
            'backpack': {'qty': 1, 'contents': [{'type': 'healing_potion', 'qty': 1}], 'is_container': True},
        }
        actor.is_container = Mock(return_value=True)
        actor.get_container_contents = Mock(return_value=[{'type': 'healing_potion', 'qty': 1}])
        actor.retrieve_item = Mock(return_value=(True, None))
        actor.label = Mock(return_value='NPC')

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []

        plan = {
            'set_goal': None,
            'goal_complete': False,
            'goal_give_up': False,
            'approach': None,
            'interact': None,
            'retrieve': {
                'item': 'healing_potion',
                'container': 'backpack',
                'qty': 1,
            },
        }

        result = self.rag_handler.apply_response_plan_directives(
            plan,
            actor,
            speaker=None,
            advance_time=False,
        )

        actor.retrieve_item.assert_called_once()
        self.assertEqual(result['executed_actions'], ['retrieve'])

    def test_build_use_item_action_pulls_potion_from_backpack(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.inventory = {
            'backpack': {'qty': 1, 'contents': [{'type': 'healing_potion', 'qty': 1}], 'is_container': True},
        }
        actor.is_container = Mock(return_value=True)
        actor.get_container_contents = Mock(return_value=[{'type': 'healing_potion', 'qty': 1}])
        actor.retrieve_item = Mock(return_value=(True, None))

        final_action = Action(self.mock_game_session, actor, 'use_item')
        final_action.target = actor

        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler._inventory_qty = Mock(side_effect=[0, 1])
        self.rag_handler._pull_item_from_inventory_container = Mock(return_value=True)
        self.rag_handler._resolve_use_item_build_step = Mock(return_value=final_action)
        self.mock_game_session.load_equipment.return_value = {
            'usable': True,
            'name': 'healing_potion',
            'item_class': 'HealingPotion',
        }

        with patch('webapp.entity_rag_handler.UseItemAction') as mock_use_item_action:
            mock_use_item_action.can.return_value = True
            mock_use_item_action.build.return_value = {'next': Mock(return_value={'param': []})}
            built = self.rag_handler.build_use_item_action(actor, 'healing_potion', target=actor)

        self.rag_handler._pull_item_from_inventory_container.assert_called_once_with(actor, 'healing_potion')
        self.assertIs(built, final_action)

    def test_locate_perceivable_target_matches_visible_entity(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        target = Mock()
        target.entity_uid = 'pc-1'
        target.name = 'Aldric'
        target.type = 'pc'

        self.rag_handler.collect_perceivable_targets = Mock(return_value=[{
            'subject': target,
            'mention_handle': 'aldric',
            'name': 'Aldric',
            'distance_ft': 10,
            'kind': 'player',
        }])

        located = self.rag_handler.locate_perceivable_target(actor, 'aldric', speaker=None)

        self.assertIs(located, target)

    def test_resolve_action_target_rejects_non_perceivable_match(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        hidden = Mock()
        hidden.entity_uid = 'hidden-1'

        self.rag_handler.resolve_named_target = Mock(return_value=hidden)
        self.rag_handler._subject_is_perceivable = Mock(return_value=False)
        self.rag_handler.locate_perceivable_target = Mock(return_value=None)

        resolved = self.rag_handler.resolve_action_target(actor, '@hidden', speaker=None)

        self.assertIsNone(resolved)
        self.rag_handler.locate_perceivable_target.assert_called_once()

    def test_parse_action_directives_locate_records_target(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        target = Mock()
        self.rag_handler.locate_perceivable_target = Mock(return_value=target)

        result = self.rag_handler.parse_action_directives(
            '[LOCATE: target=aldric]',
            actor,
            speaker=None,
        )

        self.assertIs(result['locate']['target'], target)
        self.assertEqual(result['locate']['query'], 'aldric')

    def test_build_targets_summary_lists_perceivable_entries(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        self.rag_handler.collect_perceivable_targets = Mock(return_value=[{
            'mention_handle': 'pip',
            'name': 'Pip',
            'distance_ft': 15,
            'position': (3, 4),
            'kind': 'npc',
        }])

        summary = self.rag_handler.build_targets_summary(actor)

        self.assertIn('[TARGETS]', summary)
        self.assertIn('@pip', summary)
        self.assertIn('Pip', summary)


if __name__ == '__main__':
    unittest.main() 