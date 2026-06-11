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

    def test_edge_detection_requires_outward_velocity(self) -> None:
        cases = [
            ("left", pygame.Vector2(-80, 100), pygame.Vector2(-100, 0), pygame.Vector2(100, 0)),
            ("right", pygame.Vector2(880, 100), pygame.Vector2(100, 0), pygame.Vector2(-100, 0)),
            ("up", pygame.Vector2(100, -80), pygame.Vector2(0, -100), pygame.Vector2(0, 100)),
            ("down", pygame.Vector2(100, 680), pygame.Vector2(0, 100), pygame.Vector2(0, -100)),
        ]
        for direction, position, outward, inward in cases:
            with self.subTest(direction=direction):
                fish = Fish(
                    fish_id=f"out-{direction}",
                    position=position.copy(),
                    velocity=outward.copy(),
                    size=60,
                    color=(255, 100, 80),
                    depth=0.5,
                )
                self.assertEqual(fish.crossed_edge((800, 600)), direction)

                fish.velocity = inward.copy()
                self.assertIsNone(fish.crossed_edge((800, 600)))

    def test_transfer_payload_round_trip_enters_opposite_edge(self) -> None:
        fish = Fish(
            fish_id="f1",
            position=pygame.Vector2(850, 300),
            velocity=pygame.Vector2(120, 5),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
            phase=1.5,
            current_node_id="node-a",
        )
        payload = fish.to_transfer_payload("right", (800, 600))
        incoming = Fish.from_transfer_payload(payload, (1024, 768))
        self.assertEqual(payload["fishId"], "f1")
        self.assertEqual(payload["currentNodeId"], "node-a")
        self.assertEqual(payload["animationState"], "transferring")
        self.assertTrue(payload["isTransferring"])
        self.assertEqual(incoming.fish_id, "f1")
        self.assertIsNone(incoming.current_node_id)
        self.assertEqual(incoming.animation_state, "swimming")
        self.assertFalse(incoming.is_transferring)
        self.assertLess(incoming.position.x, 0)
        self.assertAlmostEqual(incoming.position.y, 384, delta=1.0)
        self.assertGreater(incoming.velocity.x, 0)

    def test_full_state_payload_round_trip_preserves_motion_and_animation_fields(self) -> None:
        fish = Fish(
            fish_id="state-fish",
            position=pygame.Vector2(850, -30),
            velocity=pygame.Vector2(123.4567, -8.5),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
            phase=1.23456,
            age=2.0,
            wander_angle=0.4,
            wander_jitter=0.5,
            turn_bias=0.1,
            target_interval=3.0,
            wander_timer=1.2,
            turn_progress=0.2,
            turn_duration=0.55,
            turn_start_angle=0.1,
            turn_end_angle=3.2,
            turn_direction=-1,
            turn_cooldown=0.8,
            current_node_id="node-a",
        )

        payload = fish.to_state_payload((800, 600))
        restored = Fish.from_state_payload(payload, (800, 600))

        self.assertEqual(payload["fishId"], "state-fish")
        self.assertEqual(payload["currentNodeId"], "node-a")
        self.assertEqual(payload["direction"], "right")
        self.assertAlmostEqual(payload["speed"], fish.speed, delta=0.01)
        self.assertEqual(payload["animationState"], "turning")
        self.assertFalse(payload["isTransferring"])
        self.assertGreater(payload["p"][0], 1.0)
        self.assertLess(payload["p"][1], 0.0)
        self.assertEqual(restored.fish_id, "state-fish")
        self.assertEqual(restored.current_node_id, "node-a")
        self.assertEqual(restored.animation_state, "turning")
        self.assertFalse(restored.is_transferring)
        self.assertAlmostEqual(restored.position.x, 850, delta=0.1)
        self.assertAlmostEqual(restored.position.y, -30, delta=0.1)
        self.assertAlmostEqual(restored.velocity.x, 123.4567, delta=0.01)
        self.assertAlmostEqual(restored.velocity.y, -8.5, delta=0.01)
        self.assertEqual(restored.color, (1, 2, 3))
        self.assertAlmostEqual(restored.size, 50)
        self.assertAlmostEqual(restored.depth, 0.7)
        self.assertAlmostEqual(restored.phase, 1.23456, delta=0.01)
        self.assertAlmostEqual(restored.wander_angle, 0.4)
        self.assertAlmostEqual(restored.wander_jitter, 0.5)
        self.assertAlmostEqual(restored.turn_bias, 0.1)
        self.assertAlmostEqual(restored.target_interval, 3.0)
        self.assertAlmostEqual(restored.turn_progress, 0.2)
        self.assertAlmostEqual(restored.turn_duration, 0.55)
        self.assertEqual(restored.turn_direction, -1)

    def test_legacy_state_payload_defaults_new_fields(self) -> None:
        payload = {
            "id": "legacy",
            "p": [0.25, 0.5],
            "v": [-30, 90],
            "s": 44,
            "c": [1, 2, 3],
            "d": 0.4,
        }

        fish = Fish.from_state_payload(payload, (800, 600))

        self.assertEqual(fish.fish_id, "legacy")
        self.assertIsNone(fish.current_node_id)
        self.assertEqual(fish.animation_state, "swimming")
        self.assertFalse(fish.is_transferring)
        self.assertEqual(fish.direction, "down")
        self.assertAlmostEqual(fish.speed, pygame.Vector2(-30, 90).length())

    def test_incoming_transfer_is_not_bounced_back_out(self) -> None:
        outgoing = Fish(
            fish_id="incoming",
            position=pygame.Vector2(850, 300),
            velocity=pygame.Vector2(120, 0),
            size=50,
            color=(1, 2, 3),
            depth=0.7,
            phase=1.5,
        )
        payload = outgoing.to_transfer_payload("right", (800, 600))
        incoming = Fish.from_transfer_payload(payload, (1024, 768))
        start_x = incoming.position.x

        self.assertLess(incoming.position.x, 0)
        self.assertIsNone(
            incoming.crossed_edge((1024, 768), margin_scale=0.12, only_edges={"left"})
        )

        incoming.update(
            0.1,
            [incoming],
            (1024, 768),
            random.Random(3),
            open_edges={"left"},
        )

        self.assertGreater(incoming.position.x, start_x)
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

    def test_fish_performs_turn_when_target_is_behind(self) -> None:
        rng = random.Random(11)
        fish = Fish(
            fish_id="turner",
            position=pygame.Vector2(400, 300),
            velocity=pygame.Vector2(120, 0),
            size=60,
            color=(255, 100, 80),
            depth=0.5,
            wander_angle=0.0,
            wander_jitter=0.4,
            target_interval=4.0,
        )
        # 触发掉头：在朝右游的鱼正后方放一个目标点。
        fish._maybe_start_turn_to(pygame.Vector2(50, 300), pygame.Vector2(0, 0))
        self.assertTrue(fish.is_turning, "应当进入掉头动画")
        self.assertNotEqual(fish.turn_direction, 0)

        # 推进直到掉头结束。
        steps = 0
        while fish.is_turning and steps < 200:
            fish.update(1 / 60.0, [fish], (800, 600), rng)
            steps += 1
        self.assertFalse(fish.is_turning)
        self.assertGreater(fish.turn_cooldown, 0.0)
        # 掉头完成后，速度方向应明显朝左（x 分量为负）。
        self.assertLess(fish.velocity.x, 0)

    def test_turn_skipped_when_pressed_against_wall(self) -> None:
        fish = Fish(
            fish_id="press",
            position=pygame.Vector2(770, 300),
            velocity=pygame.Vector2(120, 0),
            size=60,
            color=(255, 100, 80),
            depth=0.5,
            wander_angle=0.0,
        )
        # 模拟正在贴墙的边界推力。
        fish._maybe_start_turn_to(pygame.Vector2(50, 300), pygame.Vector2(-0.8, 0))
        self.assertFalse(fish.is_turning, "贴墙转向时不应叠加掉头动画")


if __name__ == "__main__":
    unittest.main()
