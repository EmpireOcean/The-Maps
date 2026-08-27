import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MapProfile, Position, parse_coordinate, _format_stat, load_profiles
import islepilot


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


def _assert_same_heading(test, actual, expected, places=6):
    # Circular comparison: 0 and 360 are the same heading.
    diff = abs((actual - expected + 180) % 360 - 180)
    test.assertAlmostEqual(diff, 0.0, places=places)


class HeadingTransformTests(unittest.TestCase):
    def test_no_swap_no_invert_heading(self):
        # Yaw is negated (see transform_yaw's docstring: confirmed live that
        # Unreal's rotation sense is the mirror image of ours), so for the
        # unswapped case heading == (90 - yaw) % 360.
        profile = MapProfile("flat", "Flat", None, -100, 100, -100, 100)
        for yaw in (0, 45, 90, 135, 180, 225, 270, 315):
            with self.subTest(yaw=yaw):
                _assert_same_heading(self, profile.transform_yaw(yaw), (90 - yaw) % 360)

    def test_gateway_swap_axes_heading(self):
        profile = MapProfile(
            "gateway", "Gateway", None,
            -607000, 509000, -505000, 607000,
            swap_axes=True,
        )
        _assert_same_heading(self, profile.transform_yaw(0), 180.0)
        _assert_same_heading(self, profile.transform_yaw(90), 270.0)
        _assert_same_heading(self, profile.transform_yaw(180), 0.0)
        _assert_same_heading(self, profile.transform_yaw(270), 90.0)

    def test_turning_right_rotates_heading_clockwise(self):
        # The concrete bug report: turning right in-game (yaw increasing,
        # per Unreal's own convention) must turn the arrow clockwise on
        # screen, not counter-clockwise.
        profile = MapProfile(
            "gateway", "Gateway", None,
            -607000, 509000, -505000, 607000,
            swap_axes=True,
        )
        start = profile.transform_yaw(10)
        turned_right = profile.transform_yaw(20)
        # Clockwise means the heading should advance forward (mod 360),
        # i.e. a small positive step, not wrap the other way.
        delta = (turned_right - start) % 360
        self.assertLess(delta, 180)

    def test_gateway_profile_carries_the_live_calibrated_offset(self):
        # Regression guard for the real map.json's heading_offset_deg=-90,
        # calibrated against a live in-game report (arrow was 90° clockwise
        # of the true facing direction). If this drifts back to 0, the
        # arrow will visibly point the wrong way again in-game.
        profile = next(p for p in load_profiles() if p.profile_id == "gateway-v0.21.7")
        self.assertAlmostEqual(profile.heading_offset_deg, -90.0, places=6)

    def test_heading_offset_deg_is_applied(self):
        profile = MapProfile(
            "flat", "Flat", None, -100, 100, -100, 100, heading_offset_deg=15,
        )
        self.assertAlmostEqual(profile.transform_yaw(0), 105.0, places=6)


class IslePilotStatusParsingTests(unittest.TestCase):
    # Captured live from GET https://islepilot.eu/api/overlay/me during
    # development (steamId replaced with a placeholder; all other fields
    # verbatim, including the real Prime quest list and vitals shape).
    SAMPLE_ME_JSON = {
        "hasData": True,
        "steamId": "76561198000000001",
        "name": "123",
        "server": "DinoVietNamPremium",
        "online": False,
        "species": "Stegosaurus",
        "female": False,
        "growth": 0.5273,
        "health": 3171.32,
        "maxHealth": 3171.32,
        "hunger": 1175.42,
        "maxHunger": 1585.66,
        "thirst": 555.09,
        "maxThirst": 1000,
        "stamina": 599.15,
        "maxStamina": 599.15,
        "nutrition": {"carb": 118.76, "protein": 0, "lipid": 0},
        "position": {"x": 485532.56, "y": -272995.97, "z": 20541.9, "yaw": -151.41},
        "prime": {
            "eligible": True,
            "elder": False,
            "required": 5,
            "total": 10,
            "done": 4,
            "quests": [
                {"name": "Visit a Sanctuary as a juvenile", "done": True},
                {"name": "Get nested in", "done": False},
                {"name": "Get perfect diet (1% of each)", "done": True},
                {"name": "Visit Mass Migration zone", "done": False},
                {"name": "Visit 2 Migration zones", "done": False},
                {"name": "Visit 4 Patrol zones", "done": True},
                {"name": "Never be Infertile", "done": False},
                {"name": "Never get Muscle spasms", "done": True},
                {"name": "Raise children to Subadult", "done": False},
                {"name": "Be a Hypsi, Troodon, Beipi, Dryo or Deino", "done": False},
            ],
        },
    }

    def test_parses_vitals_and_position(self):
        status = islepilot.IslePilotStatus.from_json(self.SAMPLE_ME_JSON)
        self.assertEqual(status.steam_id, "76561198000000001")
        self.assertEqual(status.health, 3171.32)
        self.assertEqual(status.max_hunger, 1585.66)
        self.assertEqual(status.thirst, 555.09)
        self.assertEqual(status.stamina, 599.15)
        self.assertEqual(status.pos_x, 485532.56)
        self.assertEqual(status.pos_yaw, -151.41)

    def test_parses_prime_quests(self):
        status = islepilot.IslePilotStatus.from_json(self.SAMPLE_ME_JSON)
        self.assertEqual(status.prime_done, 4)
        self.assertEqual(status.prime_required, 5)
        self.assertEqual(status.prime_total, 10)
        self.assertEqual(len(status.quests), 10)
        self.assertTrue(status.quests[0].done)
        self.assertFalse(status.quests[1].done)
        self.assertEqual(status.quests[0].name, "Visit a Sanctuary as a juvenile")

    def test_missing_fields_do_not_raise(self):
        status = islepilot.IslePilotStatus.from_json({"steamId": "1"})
        self.assertIsNone(status.health)
        self.assertIsNone(status.pos_x)
        self.assertEqual(status.quests, ())


class QuestTranslationTests(unittest.TestCase):
    def test_known_quests_are_translated(self):
        for name in islepilot.QUEST_TRANSLATIONS:
            with self.subTest(name=name):
                translated = islepilot.translate_quest(name)
                self.assertNotEqual(translated, name)

    def test_unknown_quest_passes_through(self):
        self.assertEqual(islepilot.translate_quest("Some new quest"), "Some new quest")


class OverlayCallbackParsingTests(unittest.TestCase):
    def test_extracts_sid_and_token_from_callback_html(self):
        html = (
            '<h2>LOGIN COMPLETE</h2><p>Return to the overlay.</p>'
            '<script>location.href="isle-overlay://?sid=76561198000000001'
            '&token=eyJhbGciOiJIUzI1NiJ9.eyJzdGVhbUlkIjoiMTIzIn0.sig"</script>'
        )
        match = islepilot.CALLBACK_PATTERN.search(html)
        self.assertIsNotNone(match)
        self.assertEqual(match.group("sid"), "76561198000000001")
        self.assertEqual(match.group("token"), "eyJhbGciOiJIUzI1NiJ9.eyJzdGVhbUlkIjoiMTIzIn0.sig")

    def test_does_not_match_unrelated_html(self):
        self.assertIsNone(islepilot.CALLBACK_PATTERN.search("<html><body>hello</body></html>"))


class IslePilotAxisOrderTests(unittest.TestCase):
    # Ground truth captured live from GET https://islepilot.eu/api/overlay/map:
    # calibration.a/b give (worldX, worldY) -> (u, v) pairs for IslePilot's
    # own basemap, independent of our world_bounds. Feeding IslePilot's
    # position.x/position.y into our swap_axes=True profile *unswapped*
    # (the original bug) lands nowhere near these; swapping them first
    # (app.py's _apply_islepilot_status) should land within ~0.01 of both.
    PROFILE = MapProfile(
        "gateway", "Gateway", None, -607000, 509000, -505000, 607000, swap_axes=True,
    )
    CALIBRATION_POINTS = (
        (534057.925, -267245.9, 0.9320964895641363, 0.3053043426801558),
        (87931.248, -104086.204, 0.5356221651318197, 0.4496109559223209),
    )

    def test_swapped_axes_match_islepilot_calibration(self):
        for world_x, world_y, expected_u, expected_v in self.CALIBRATION_POINTS:
            with self.subTest(world_x=world_x, world_y=world_y):
                # Mirrors app.py: Position(status.pos_y, status.pos_x, ...).
                nx, ny = self.PROFILE.to_normalized(Position(world_y, world_x, 0.0))
                self.assertAlmostEqual(nx, expected_u, delta=0.01)
                self.assertAlmostEqual(ny, expected_v, delta=0.01)

    def test_unswapped_axes_do_not_match(self):
        # Regression guard: the original (buggy) ordering should clearly
        # miss, so a future edit can't silently reintroduce it.
        world_x, world_y, expected_u, expected_v = self.CALIBRATION_POINTS[0]
        nx, ny = self.PROFILE.to_normalized(Position(world_x, world_y, 0.0))
        self.assertGreater(abs(nx - expected_u) + abs(ny - expected_v), 0.1)


class FormatStatTests(unittest.TestCase):
    def test_small_values_keep_one_decimal(self):
        # A hatchling's max pool can be a small fraction (e.g. 0.4); showing
        # it as "0" would look broken, so values below 10 keep a decimal.
        self.assertEqual(_format_stat(0.4), "0.4")
        self.assertEqual(_format_stat(0.0), "0.0")
        self.assertEqual(_format_stat(9.96), "10.0")

    def test_large_values_round_to_whole_numbers(self):
        self.assertEqual(_format_stat(3171.32), "3171")
        self.assertEqual(_format_stat(1585.66), "1586")
        self.assertEqual(_format_stat(18.4), "18")


if __name__ == "__main__":
    unittest.main()
