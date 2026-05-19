from __future__ import annotations

from dataclasses import dataclass
import math
import random
import uuid

import pygame


Vector2 = pygame.math.Vector2

OPPOSITE_DIRECTIONS = {
    "left": "right",
    "right": "left",
    "up": "down",
    "down": "up",
}


PALETTE: tuple[tuple[int, int, int], ...] = (
    (239, 112, 96),
    (255, 177, 87),
    (254, 220, 112),
    (90, 204, 183),
    (102, 173, 255),
    (192, 137, 245),
    (246, 130, 176),
)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_vector(values: list[float] | tuple[float, float], fallback: Vector2) -> Vector2:
    try:
        vector = Vector2(float(values[0]), float(values[1]))
    except (IndexError, TypeError, ValueError):
        vector = fallback.copy()
    if vector.length_squared() == 0:
        return fallback.copy()
    return vector


@dataclass
class Fish:
    fish_id: str
    position: Vector2
    velocity: Vector2
    size: float
    color: tuple[int, int, int]
    depth: float
    phase: float = 0.0
    age: float = 0.0

    @property
    def scale(self) -> float:
        return 0.55 + self.depth * 0.75

    @property
    def body_length(self) -> float:
        return self.size * self.scale

    @property
    def body_height(self) -> float:
        return self.body_length * 0.42

    def update(
        self,
        dt: float,
        fishes: list["Fish"],
        bounds: tuple[int, int],
        rng: random.Random,
        speed_multiplier: float = 1.0,
        open_edges: set[str] | None = None,
    ) -> None:
        if dt <= 0:
            return

        open_edges = open_edges or set()
        width, height = bounds
        desired = Vector2()
        separation = Vector2()
        alignment = Vector2()
        cohesion = Vector2()
        neighbors = 0
        neighbor_radius = 120.0 * self.scale

        for other in fishes:
            if other is self:
                continue
            offset = other.position - self.position
            distance_sq = offset.length_squared()
            if distance_sq <= 0 or distance_sq > neighbor_radius * neighbor_radius:
                continue
            distance = math.sqrt(distance_sq)
            neighbors += 1
            alignment += other.velocity
            cohesion += other.position
            separation -= offset / max(1.0, distance)

        if neighbors:
            alignment.scale_to_length(1.0) if alignment.length_squared() else None
            cohesion = (cohesion / neighbors) - self.position
            if cohesion.length_squared():
                cohesion.scale_to_length(1.0)
            if separation.length_squared():
                separation.scale_to_length(1.0)
            desired += alignment * 22.0
            desired += cohesion * 16.0
            desired += separation * 42.0

        margin = 110.0 * self.scale
        if self.position.x < margin and "left" not in open_edges:
            desired.x += (margin - self.position.x) * 1.7
        elif self.position.x > width - margin and "right" not in open_edges:
            desired.x -= (self.position.x - (width - margin)) * 1.7
        if self.position.y < margin and "up" not in open_edges:
            desired.y += (margin - self.position.y) * 1.7
        elif self.position.y > height - margin and "down" not in open_edges:
            desired.y -= (self.position.y - (height - margin)) * 1.7

        angle = math.atan2(self.velocity.y, self.velocity.x)
        wander_angle = angle + math.sin(self.age * 0.85 + self.phase) * 0.45
        wander_angle += rng.uniform(-0.35, 0.35) * dt
        desired += Vector2(math.cos(wander_angle), math.sin(wander_angle)) * 24.0

        self.velocity += desired * dt
        target_speed = (70.0 + self.depth * 95.0) * speed_multiplier
        current_speed = self.velocity.length()
        if current_speed < 1.0:
            self.velocity = Vector2(target_speed, 0).rotate(rng.uniform(0, 360))
        else:
            self.velocity.scale_to_length(
                clamp(current_speed, target_speed * 0.55, target_speed * 1.55)
            )

        self.position += self.velocity * dt
        self.phase = (self.phase + dt * (5.5 + self.velocity.length() / 32.0)) % (math.tau)
        self.age += dt

    def crossed_edge(
        self,
        bounds: tuple[int, int],
        *,
        margin_scale: float = 0.7,
        only_edges: set[str] | None = None,
    ) -> str | None:
        width, height = bounds
        margin = self.body_length * margin_scale
        if self.position.x < -margin and (only_edges is None or "left" in only_edges):
            return "left"
        if self.position.x > width + margin and (only_edges is None or "right" in only_edges):
            return "right"
        if self.position.y < -margin and (only_edges is None or "up" in only_edges):
            return "up"
        if self.position.y > height + margin and (only_edges is None or "down" in only_edges):
            return "down"
        return None

    def bounce_inside(self, bounds: tuple[int, int]) -> None:
        width, height = bounds
        margin = self.body_length * 0.4
        if self.position.x < margin:
            self.position.x = margin
            self.velocity.x = abs(self.velocity.x)
        elif self.position.x > width - margin:
            self.position.x = width - margin
            self.velocity.x = -abs(self.velocity.x)

        if self.position.y < margin:
            self.position.y = margin
            self.velocity.y = abs(self.velocity.y)
        elif self.position.y > height - margin:
            self.position.y = height - margin
            self.velocity.y = -abs(self.velocity.y)

    def to_transfer_payload(self, direction: str, bounds: tuple[int, int]) -> dict:
        width, height = bounds
        if direction in ("left", "right"):
            edge_position = clamp(self.position.y / max(1, height), 0.0, 1.0)
        else:
            edge_position = clamp(self.position.x / max(1, width), 0.0, 1.0)
        return {
            "fish_id": self.fish_id,
            "direction": direction,
            "edge_position": edge_position,
            "velocity": [self.velocity.x, self.velocity.y],
            "size": self.size,
            "color": list(self.color),
            "depth": self.depth,
            "phase": self.phase,
        }

    @classmethod
    def from_transfer_payload(cls, payload: dict, bounds: tuple[int, int]) -> "Fish":
        width, height = bounds
        direction = str(payload.get("direction", "right"))
        edge_position = clamp(float(payload.get("edge_position", 0.5)), 0.0, 1.0)
        size = clamp(float(payload.get("size", 54.0)), 24.0, 120.0)
        depth = clamp(float(payload.get("depth", 0.6)), 0.0, 1.0)
        body_length = size * (0.55 + depth * 0.75)

        if direction == "right":
            position = Vector2(-body_length * 0.5, edge_position * height)
        elif direction == "left":
            position = Vector2(width + body_length * 0.5, edge_position * height)
        elif direction == "up":
            position = Vector2(edge_position * width, height + body_length * 0.5)
        else:
            position = Vector2(edge_position * width, -body_length * 0.5)

        fallback_angle = {"right": 0, "left": 180, "up": -90, "down": 90}.get(direction, 0)
        fallback_velocity = Vector2(120, 0).rotate(fallback_angle)
        velocity = _safe_vector(payload.get("velocity", [fallback_velocity.x, fallback_velocity.y]), fallback_velocity)
        return cls(
            fish_id=str(payload.get("fish_id") or uuid.uuid4().hex),
            position=position,
            velocity=velocity,
            size=size,
            color=tuple(int(clamp(channel, 0, 255)) for channel in payload.get("color", PALETTE[0]))[:3],
            depth=depth,
            phase=float(payload.get("phase", 0.0)),
        )

    @classmethod
    def from_expired_transfer_payload(cls, payload: dict, bounds: tuple[int, int]) -> "Fish":
        fish = cls.from_transfer_payload(payload, bounds)
        direction = str(payload.get("direction", "right"))
        width, height = bounds
        margin = fish.body_length * 0.55
        if direction == "right":
            fish.position.x = width - margin
            fish.velocity.x = -abs(fish.velocity.x)
        elif direction == "left":
            fish.position.x = margin
            fish.velocity.x = abs(fish.velocity.x)
        elif direction == "up":
            fish.position.y = margin
            fish.velocity.y = abs(fish.velocity.y)
        elif direction == "down":
            fish.position.y = height - margin
            fish.velocity.y = -abs(fish.velocity.y)
        return fish


def create_random_fish(bounds: tuple[int, int], rng: random.Random, speed_multiplier: float = 1.0) -> Fish:
    width, height = bounds
    depth = rng.uniform(0.15, 1.0)
    size = rng.uniform(38.0, 82.0)
    margin = size * (0.55 + depth * 0.75)
    position = Vector2(
        rng.uniform(margin, max(margin + 1, width - margin)),
        rng.uniform(margin, max(margin + 1, height - margin)),
    )
    angle = rng.uniform(0, math.tau)
    speed = rng.uniform(70.0, 155.0) * speed_multiplier
    velocity = Vector2(math.cos(angle), math.sin(angle)) * speed
    return Fish(
        fish_id=uuid.uuid4().hex,
        position=position,
        velocity=velocity,
        size=size,
        color=rng.choice(PALETTE),
        depth=depth,
        phase=rng.uniform(0, math.tau),
    )
