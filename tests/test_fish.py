from __future__ import annotations

import random
import unittest

import pygame

from cyberfish.fish import Fish, create_random_fish


class FishTests(unittest.TestCase):
    def test_update_moves_fish(self) -> None:
        rng = random.Random(1)
        fish = create_random_fish((800, 600), rng)
        start = fish.position.copy()
        fish.update(0.2, [fish], (800, 600), rng)
        self.assertNotEqual(start, fish.position)

    def test_open_edge_allows_fish_to_swim_outward(self) -> None:
        closed_rng = random.Random(4)
        open_rng = random.Random(4)
        closed_fish = Fish(
            fish_id="closed",
            position=pygame.Vector2(780, 300),
            velocity=pygame.Vector2(100, 0),
            size=60,
            color=(255, 100, 80),
            depth=0.5,
        )
        open_fish = Fish(
            fish_id="open",
            position=pygame.Vector2(780, 300),
            velocity=pygame.Vector2(100, 0),
            size=60,
            color=(255, 100, 80),
            depth=0.5,
        )

        closed_fish.update(1.0, [closed_fish], (800, 600), closed_rng)
        open_fish.update(1.0, [open_fish], (800, 600), open_rng, open_edges={"right"})

        self.assertGreater(open_fish.position.x, closed_fish.position.x)
        self.assertGreater(open_fish.velocity.x, 0)

    def test_edge_detection_and_bounce(self) -> None:
        fish = Fish(
            fish_id="f1",
            position=pygame.Vector2(-80, 100),
            velocity=pygame.Vector2(-100, 0),
            size=60,
            color=(255, 100, 80),
            depth=0.5,
        )
        self.assertEqual(fish.crossed_edge((800, 600)), "left")
        fish.bounce_inside((800, 600))
        self.assertGreater(fish.velocity.x, 0)
        self.assertGreaterEqual(fish.position.x, fish.body_length * 0.3)

    def test_transfer_payload_round_trip_enters_opposite_edge(self) -> None:
        fish = Fish(
            fish_id="f1",
            position=pygame.Vector2(850, 300),
            velocity=pygame.Vector2(120, 5),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
            phase=1.5,
        )
        payload = fish.to_transfer_payload("right", (800, 600))
        incoming = Fish.from_transfer_payload(payload, (1024, 768))
        self.assertEqual(incoming.fish_id, "f1")
        self.assertLess(incoming.position.x, 0)
        self.assertAlmostEqual(incoming.position.y, 384, delta=1.0)
        self.assertGreater(incoming.velocity.x, 0)

    def test_open_edge_transfer_can_trigger_soon_after_boundary_cross(self) -> None:
        fish = Fish(
            fish_id="f2",
            position=pygame.Vector2(812, 300),
            velocity=pygame.Vector2(120, 0),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
        )

        self.assertIsNone(fish.crossed_edge((800, 600)))
        self.assertEqual(
            fish.crossed_edge((800, 600), margin_scale=0.12, only_edges={"right"}),
            "right",
        )


if __name__ == "__main__":
    unittest.main()
