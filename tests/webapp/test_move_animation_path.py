import unittest
from unittest.mock import Mock

from webapp.utils import build_move_animation_log_entries, resolved_move_animation_path


class TestMoveAnimationPath(unittest.TestCase):
    def test_build_move_animation_log_entries_uses_resolved_path(self):
        actor = Mock()
        actor.entity_uid = 'aldric'
        action = Mock()
        action.source = actor
        action.result = [
            {
                'type': 'move',
                'source': actor,
                'path': [[10, 22], [11, 22]],
            },
        ]
        action.move_path = [[10, 22], [11, 22], [11, 23]]

        entries = build_move_animation_log_entries(
            action,
            fallback_path=action.move_path,
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], [[10, 22], [11, 22]])

    def test_resolved_move_animation_path_falls_back_to_requested_path(self):
        action = Mock()
        action.source = None
        action.result = []
        action.move_path = [[4, 5], [5, 5]]

        self.assertEqual(
            resolved_move_animation_path(action),
            [[4, 5], [5, 5]],
        )


if __name__ == '__main__':
    unittest.main()
