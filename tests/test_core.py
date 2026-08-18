import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MapProfile, Position, parse_coordinate


class ParserTests(unittest.TestCase):
    def test_evrima_coordinate(self):
        self.assertEqual(
            parse_coordinate("88,879.526, -288,696.11, 21,112.882"),
            Position(88879.526, -288696.11, 21112.882),
        )

    def test_rejects_unrelated_clipboard_text(self):
        self.assertIsNone(parse_coordinate("hello 1, 2, 3"))

    def test_legacy_coordinate(self):
        self.assertEqual(
            parse_coordinate("Lat: -341,091.384\nLong: 214,066.728\nAlt: 34,671.621"),
            Position(-341091.384, 214066.728, 34671.621),
        )


class ProfileTests(unittest.TestCase):
    def test_world_center_maps_to_center(self):
        profile = MapProfile("test", "Test", None, -100, 100, -200, 200)
        self.assertEqual(profile.to_normalized(Position(0, 0, 0)), (0.5, 0.5))

    def test_gateway_public_bounds(self):
        profile = MapProfile(
            "gateway", "Gateway", None,
            -607000, 509000, -505000, 607000,
            swap_axes=True,
        )
        self.assertEqual(profile.to_normalized(Position(-607000, -505000, 0)), (0.0, 0.0))
        self.assertEqual(profile.to_normalized(Position(509000, 607000, 0)), (1.0, 1.0))

    def test_two_reported_gateway_positions_move_mostly_vertically(self):
        profile = MapProfile(
            "gateway", "Gateway", None,
            -607000, 509000, -505000, 607000,
            swap_axes=True,
        )
        previous = profile.to_normalized(Position(-298318.675, 87911.701, 33167.957))
        current = profile.to_normalized(Position(-244865, 90120.75, 28766.523))
        self.assertAlmostEqual(current[0] - previous[0], 0.00198655, places=6)
        self.assertAlmostEqual(current[1] - previous[1], 0.04789756, places=6)


if __name__ == "__main__":
    unittest.main()
