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

ANIMATION_SWIMMING = "swimming"
ANIMATION_TURNING = "turning"
ANIMATION_TRANSFERRING = "transferring"
VALID_ANIMATION_STATES = {
    ANIMATION_SWIMMING,
    ANIMATION_TURNING,
    ANIMATION_TRANSFERRING,
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
    """从网络 payload 解析速度向量，非法或零向量时回退到指定方向。"""
    try:
        vector = Vector2(float(values[0]), float(values[1]))
    except (IndexError, TypeError, ValueError):
        vector = fallback.copy()
    if vector.length_squared() == 0:
        return fallback.copy()
    return vector


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return fallback


def _safe_node_id(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _safe_animation_state(value: object, fallback: str = ANIMATION_SWIMMING) -> str:
    if isinstance(value, str) and value in VALID_ANIMATION_STATES:
        return value
    return fallback


def _safe_color(raw: object, fallback: tuple[int, int, int] = PALETTE[0]) -> tuple[int, int, int]:
    if not isinstance(raw, (list, tuple)):
        return fallback
    channels = []
    for index in range(3):
        try:
            channels.append(int(clamp(float(raw[index]), 0, 255)))
        except (IndexError, TypeError, ValueError):
            channels.append(fallback[index])
    return (channels[0], channels[1], channels[2])


def _rounded(value: float) -> float:
    return round(float(value), 4)


def _direction_from_vector(vector: Vector2) -> str:
    if abs(vector.x) >= abs(vector.y):
        return "right" if vector.x >= 0 else "left"
    return "down" if vector.y >= 0 else "up"


def _velocity_from_direction(direction: object, speed: object, fallback: Vector2) -> Vector2:
    direction_text = str(direction or "")
    if direction_text not in OPPOSITE_DIRECTIONS:
        return fallback.copy()
    fallback_speed = fallback.length() if fallback.length_squared() else 120.0
    magnitude = max(1.0, _safe_float(speed, fallback_speed))
    vectors = {
        "right": Vector2(1.0, 0.0),
        "left": Vector2(-1.0, 0.0),
        "up": Vector2(0.0, -1.0),
        "down": Vector2(0.0, 1.0),
    }
    return vectors[direction_text] * magnitude


def _payload_velocity(payload: dict, fallback: Vector2) -> Vector2:
    raw_velocity = payload.get("v")
    if not isinstance(raw_velocity, (list, tuple)):
        raw_velocity = payload.get("velocity")
    if isinstance(raw_velocity, (list, tuple)):
        return _safe_vector(raw_velocity, fallback)
    return _velocity_from_direction(payload.get("direction"), payload.get("speed"), fallback)


@dataclass
class Fish:
    """一条鱼的运动状态，包含本机动画参数和跨屏传输所需的最小状态。"""

    fish_id: str
    position: Vector2
    velocity: Vector2
    size: float
    color: tuple[int, int, int]
    depth: float
    phase: float = 0.0
    age: float = 0.0
    wander_angle: float = 0.0
    wander_jitter: float = 0.55
    turn_bias: float = 0.0
    target_interval: float = 4.0
    wander_timer: float = 0.0
    wander_target: Vector2 | None = None
    turn_progress: float = 0.0
    turn_duration: float = 0.0
    turn_start_angle: float = 0.0
    turn_end_angle: float = 0.0
    turn_direction: int = 0
    turn_cooldown: float = 0.0
    current_node_id: str | None = None
    animation_state: str = ANIMATION_SWIMMING
    is_transferring: bool = False

    @property
    def scale(self) -> float:
        return 0.55 + self.depth * 0.75

    @property
    def body_length(self) -> float:
        return self.size * self.scale

    @property
    def body_height(self) -> float:
        return self.body_length * 0.42

    @property
    def is_turning(self) -> bool:
        return self.turn_duration > 0.0 and self.turn_progress < self.turn_duration

    @property
    def direction(self) -> str:
        return _direction_from_vector(self.velocity)

    @property
    def speed(self) -> float:
        return self.velocity.length()

    @property
    def effective_animation_state(self) -> str:
        if self.is_transferring or self.animation_state == ANIMATION_TRANSFERRING:
            return ANIMATION_TRANSFERRING
        if self.is_turning or self.animation_state == ANIMATION_TURNING:
            return ANIMATION_TURNING
        return ANIMATION_SWIMMING

    @property
    def turn_intensity(self) -> float:
        """0 在掉头开始/结束、1 在中段，用于渲染时的尾鳍弯曲。"""
        if self.turn_duration <= 0.0:
            return 0.0
        t = max(0.0, min(1.0, self.turn_progress / self.turn_duration))
        return math.sin(math.pi * t)

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
        if self.turn_cooldown > 0.0:
            self.turn_cooldown = max(0.0, self.turn_cooldown - dt)

        if self.is_turning:
            self.animation_state = ANIMATION_TURNING
            self._update_turning(dt, bounds, open_edges, speed_multiplier)
            return

        if not self.is_transferring:
            self.animation_state = ANIMATION_SWIMMING

        desired = Vector2()
        separation = Vector2()
        alignment = Vector2()
        cohesion = Vector2()
        neighbors = 0
        neighbor_radius = 120.0 * self.scale

        # 简化 Boids 行为：分离避免重叠，对齐/聚合让鱼群看起来有群游趋势。
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

        margin = max(160.0 * self.scale, 0.18 * min(width, height))
        # 前瞻避墙：根据当前速度预测约 1.4 秒后的位置，对每条 closed 边算"压力"（接近墙时增长，
        # 内部为零）。再用平方衰减曲线，鱼离墙越近转向越急；远处只感受到轻微提示，
        # 不会出现"贴墙才弹"的硬转。
        look_ahead = self.position + self.velocity * 1.4

        def edge_pressure(closed: bool, distance_now: float, distance_future: float) -> float:
            if not closed:
                return 0.0
            distance = min(distance_now, distance_future)
            if distance >= margin:
                return 0.0
            if distance <= 0:
                return 1.0
            t = 1.0 - distance / margin
            return t * t

        press_left = edge_pressure("left" not in open_edges, self.position.x, look_ahead.x)
        press_right = edge_pressure(
            "right" not in open_edges, width - self.position.x, width - look_ahead.x
        )
        press_up = edge_pressure("up" not in open_edges, self.position.y, look_ahead.y)
        press_down = edge_pressure(
            "down" not in open_edges, height - self.position.y, height - look_ahead.y
        )

        steer = Vector2(press_left - press_right, press_up - press_down)
        if steer.length_squared() > 0.0:
            desired += steer * 130.0

        angle = math.atan2(self.velocity.y, self.velocity.x)
        if self.wander_angle == 0.0 and self.age == 0.0:
            self.wander_angle = angle

        # 相关随机游走：在当前航向附近做高斯扰动 + 个体偏置 + 缓慢回归当前航向，
        # 产生平滑但不规则的转向曲线，每条鱼的扰动强度不同，轨迹不会出现明显周期。
        jitter = rng.gauss(0.0, self.wander_jitter)
        regression = (angle - self.wander_angle) * 0.6
        self.wander_angle += (regression + self.turn_bias) * dt + jitter * math.sqrt(dt)

        # 边界预转向：当上面的 steer 不为零（鱼正接近墙），把 wander_angle 平滑地拉向
        # 水箱内部方向，强度与边界压力成正比，避免"位置力把鱼推开但 wander 又把它拉回墙"。
        if steer.length_squared() > 0.0:
            inward_angle = math.atan2(steer.y, steer.x)
            angle_diff = (inward_angle - self.wander_angle + math.pi) % math.tau - math.pi
            pull = min(1.0, steer.length()) * 2.6 * dt
            self.wander_angle += angle_diff * pull

        wander_dir = Vector2(math.cos(self.wander_angle), math.sin(self.wander_angle))
        desired += wander_dir * 26.0

        # 短期随机目标点：每隔一段时间在屏内挑一个新点拉一下，避免长时间沿同一方向直线游。
        self.wander_timer -= dt
        if self.wander_target is None or self.wander_timer <= 0.0:
            target_margin = max(margin * 1.1, 1.0)
            self.wander_target = Vector2(
                rng.uniform(target_margin, max(target_margin + 1, width - target_margin)),
                rng.uniform(target_margin, max(target_margin + 1, height - target_margin)),
            )
            self.wander_timer = rng.uniform(0.55, 1.0) * self.target_interval
            # 目标点选完后看一眼：如果它正好在身后（>130°），且没在贴墙转向、也没正朝着开放边游，
            # 就启动掉头动画。
            if not self._heading_into_open_edge(open_edges, bounds):
                self._maybe_start_turn_to(self.wander_target, steer)
                if self.is_turning:
                    self._update_turning(dt, bounds, open_edges, speed_multiplier)
                    return
        to_target = self.wander_target - self.position
        if to_target.length_squared() > 1.0:
            to_target.scale_to_length(1.0)
            desired += to_target * 14.0

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

    def _heading_into_open_edge(self, open_edges: set[str], bounds: tuple[int, int]) -> bool:
        """鱼是否正朝着一条开放（跨屏）边游？是的话就不该被掉头打断。"""
        if not open_edges:
            return False
        width, height = bounds
        margin = max(160.0 * self.scale, 0.18 * min(width, height))
        if "right" in open_edges and self.velocity.x > 0 and width - self.position.x < margin:
            return True
        if "left" in open_edges and self.velocity.x < 0 and self.position.x < margin:
            return True
        if "down" in open_edges and self.velocity.y > 0 and height - self.position.y < margin:
            return True
        if "up" in open_edges and self.velocity.y < 0 and self.position.y < margin:
            return True
        return False

    def _maybe_start_turn_to(self, target: Vector2, edge_steer: Vector2) -> None:
        if self.turn_cooldown > 0.0 or self.is_turning:
            return
        if edge_steer.length_squared() > 0.05:
            # 正在贴墙避让时不掉头，避免和边界转向冲突。
            return
        to_target = target - self.position
        if to_target.length_squared() < 1.0:
            return
        heading_angle = math.atan2(self.velocity.y, self.velocity.x)
        target_angle = math.atan2(to_target.y, to_target.x)
        delta = (target_angle - heading_angle + math.pi) % math.tau - math.pi
        # 仅当目标基本"在身后"（>130°）才掉头；侧前/侧后用普通 wander 即可。
        if abs(delta) < math.radians(130):
            return
        # 选择转向方向：沿 delta 符号转 180°（保持与目标同侧）。
        direction = 1 if delta >= 0 else -1
        self.turn_direction = direction
        self.turn_start_angle = heading_angle
        self.turn_end_angle = heading_angle + direction * math.pi
        self.turn_progress = 0.0
        self.turn_duration = 0.55
        self.animation_state = ANIMATION_TURNING

    def _update_turning(
        self,
        dt: float,
        bounds: tuple[int, int],
        open_edges: set[str],
        speed_multiplier: float,
    ) -> None:
        self.animation_state = ANIMATION_TURNING
        self.turn_progress = min(self.turn_duration, self.turn_progress + dt)
        t = self.turn_progress / self.turn_duration if self.turn_duration > 0 else 1.0
        # smoothstep：两端慢、中段快，符合身体抡尾的节奏感。
        smooth = t * t * (3.0 - 2.0 * t)
        new_angle = self.turn_start_angle + (self.turn_end_angle - self.turn_start_angle) * smooth
        # 速度做一个 dip：开始/结束接近正常巡游速度，中段降到约 35%。
        target_speed = (70.0 + self.depth * 95.0) * speed_multiplier
        speed_factor = 1.0 - 0.65 * math.sin(math.pi * t)
        speed = target_speed * speed_factor
        self.velocity = Vector2(math.cos(new_angle), math.sin(new_angle)) * speed
        self.wander_angle = new_angle

        # 掉头时位移很小（几乎原地转），但仍按速度推进一点点。
        self.position += self.velocity * dt
        # 边界保护：万一鱼正好在边缘开始掉头，把它锁回水箱内（closed 边）。
        width, height = bounds
        body_margin = self.body_length * 0.4
        if "left" not in open_edges and self.position.x < body_margin:
            self.position.x = body_margin
        if "right" not in open_edges and self.position.x > width - body_margin:
            self.position.x = width - body_margin
        if "up" not in open_edges and self.position.y < body_margin:
            self.position.y = body_margin
        if "down" not in open_edges and self.position.y > height - body_margin:
            self.position.y = height - body_margin

        # 尾鳍快速摆动节奏：让 phase 走得比平时更快。
        self.phase = (self.phase + dt * 11.0) % math.tau
        self.age += dt
        if self.turn_progress >= self.turn_duration:
            # 掉头完成：清状态、进入冷却，让目标点稍后再被普通 wander 拉过去。
            self.turn_duration = 0.0
            self.turn_progress = 0.0
            self.turn_cooldown = 4.0
            if not self.is_transferring:
                self.animation_state = ANIMATION_SWIMMING

    def crossed_edge(
        self,
        bounds: tuple[int, int],
        *,
        margin_scale: float = 0.7,
        only_edges: set[str] | None = None,
    ) -> str | None:
        width, height = bounds
        margin = self.body_length * margin_scale
        if (
            self.position.x < -margin
            and self.velocity.x < 0
            and (only_edges is None or "left" in only_edges)
        ):
            return "left"
        if (
            self.position.x > width + margin
            and self.velocity.x > 0
            and (only_edges is None or "right" in only_edges)
        ):
            return "right"
        if (
            self.position.y < -margin
            and self.velocity.y < 0
            and (only_edges is None or "up" in only_edges)
        ):
            return "up"
        if (
            self.position.y > height + margin
            and self.velocity.y > 0
            and (only_edges is None or "down" in only_edges)
        ):
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
        """把鱼压缩为 UDP 可传输的数据，只保留接续运动需要的状态。"""
        width, height = bounds
        if direction in ("left", "right"):
            edge_position = clamp(self.position.y / max(1, height), 0.0, 1.0)
        else:
            edge_position = clamp(self.position.x / max(1, width), 0.0, 1.0)
        return {
            "fish_id": self.fish_id,
            "fishId": self.fish_id,
            "currentNodeId": self.current_node_id,
            "direction": direction,
            "edge_position": edge_position,
            "velocity": [self.velocity.x, self.velocity.y],
            "speed": self.speed,
            "size": self.size,
            "color": list(self.color),
            "depth": self.depth,
            "z": self.depth,
            "phase": self.phase,
            "wander_jitter": self.wander_jitter,
            "turn_bias": self.turn_bias,
            "target_interval": self.target_interval,
            "animationState": ANIMATION_TRANSFERRING,
            "isTransferring": True,
        }

    def to_state_payload(self, bounds: tuple[int, int]) -> dict:
        """序列化完整实时状态；坐标归一化但不裁剪，保留跨边界连续性。"""
        width, height = bounds
        return {
            "id": self.fish_id,
            "fishId": self.fish_id,
            "currentNodeId": self.current_node_id,
            "x": _rounded(self.position.x),
            "y": _rounded(self.position.y),
            "z": _rounded(self.depth),
            "p": [
                _rounded(self.position.x / max(1, width)),
                _rounded(self.position.y / max(1, height)),
            ],
            "v": [_rounded(self.velocity.x), _rounded(self.velocity.y)],
            "direction": self.direction,
            "speed": _rounded(self.speed),
            "s": _rounded(self.size),
            "c": list(self.color),
            "d": _rounded(self.depth),
            "animationState": self.effective_animation_state,
            "isTransferring": bool(self.is_transferring),
            "ph": _rounded(self.phase),
            "a": _rounded(self.age),
            "wa": _rounded(self.wander_angle),
            "wj": _rounded(self.wander_jitter),
            "tb": _rounded(self.turn_bias),
            "ti": _rounded(self.target_interval),
            "wt": _rounded(self.wander_timer),
            "tp": _rounded(self.turn_progress),
            "td": _rounded(self.turn_duration),
            "tsa": _rounded(self.turn_start_angle),
            "tea": _rounded(self.turn_end_angle),
            "tdr": int(self.turn_direction),
            "tc": _rounded(self.turn_cooldown),
        }

    def copy_for_render(self, position: Vector2) -> "Fish":
        return Fish(
            fish_id=self.fish_id,
            position=position.copy(),
            velocity=self.velocity.copy(),
            size=self.size,
            color=self.color,
            depth=self.depth,
            phase=self.phase,
            age=self.age,
            wander_angle=self.wander_angle,
            wander_jitter=self.wander_jitter,
            turn_bias=self.turn_bias,
            target_interval=self.target_interval,
            wander_timer=self.wander_timer,
            wander_target=self.wander_target.copy() if self.wander_target is not None else None,
            turn_progress=self.turn_progress,
            turn_duration=self.turn_duration,
            turn_start_angle=self.turn_start_angle,
            turn_end_angle=self.turn_end_angle,
            turn_direction=self.turn_direction,
            turn_cooldown=self.turn_cooldown,
            current_node_id=self.current_node_id,
            animation_state=self.animation_state,
            is_transferring=self.is_transferring,
        )

    @classmethod
    def from_transfer_payload(cls, payload: dict, bounds: tuple[int, int]) -> "Fish":
        """从跨屏移交 payload 重建鱼，并放在进入屏幕的对应边缘外侧。"""
        width, height = bounds
        direction = str(payload.get("direction", "right"))
        if direction not in OPPOSITE_DIRECTIONS:
            direction = "right"
        edge_position = clamp(_safe_float(payload.get("edge_position", 0.5), 0.5), 0.0, 1.0)
        size = clamp(_safe_float(payload.get("size", 54.0), 54.0), 24.0, 120.0)
        depth = clamp(_safe_float(payload.get("depth", payload.get("z", 0.6)), 0.6), 0.0, 1.0)
        body_length = size * (0.55 + depth * 0.75)

        if direction == "right":
            # 对端向右游出时，本机从左侧接入；其它方向同理保持运动连续。
            position = Vector2(-body_length * 0.5, edge_position * height)
        elif direction == "left":
            position = Vector2(width + body_length * 0.5, edge_position * height)
        elif direction == "up":
            position = Vector2(edge_position * width, height + body_length * 0.5)
        else:
            position = Vector2(edge_position * width, -body_length * 0.5)

        fallback_angle = {"right": 0, "left": 180, "up": -90, "down": 90}.get(direction, 0)
        fallback_velocity = Vector2(120, 0).rotate(fallback_angle)
        velocity = _payload_velocity(payload, fallback_velocity)
        return cls(
            fish_id=str(payload.get("fish_id") or payload.get("fishId") or uuid.uuid4().hex),
            position=position,
            velocity=velocity,
            size=size,
            color=_safe_color(payload.get("color", PALETTE[0])),
            depth=depth,
            phase=_safe_float(payload.get("phase", 0.0), 0.0),
            wander_angle=math.atan2(velocity.y, velocity.x),
            wander_jitter=clamp(_safe_float(payload.get("wander_jitter", 0.55), 0.55), 0.1, 1.5),
            turn_bias=clamp(_safe_float(payload.get("turn_bias", 0.0), 0.0), -0.6, 0.6),
            target_interval=clamp(_safe_float(payload.get("target_interval", 4.0), 4.0), 1.5, 8.0),
            animation_state=ANIMATION_SWIMMING,
            is_transferring=False,
        )

    @classmethod
    def from_state_payload(cls, payload: dict, bounds: tuple[int, int]) -> "Fish":
        """从完整实时状态重建渲染用鱼，保持远端动画字段。"""
        width, height = bounds
        raw_position = payload.get("p")
        if not isinstance(raw_position, (list, tuple)):
            raw_position = [payload.get("x", 0.5), payload.get("y", 0.5)]
        fallback_velocity = Vector2(120.0, 0.0)
        velocity = _payload_velocity(payload, fallback_velocity)
        fish = cls(
            fish_id=str(
                payload.get("id")
                or payload.get("fish_id")
                or payload.get("fishId")
                or uuid.uuid4().hex
            ),
            position=Vector2(
                _safe_float(raw_position[0] if len(raw_position) > 0 else 0.5, 0.5) * width,
                _safe_float(raw_position[1] if len(raw_position) > 1 else 0.5, 0.5) * height,
            ),
            velocity=velocity,
            size=clamp(_safe_float(payload.get("s", payload.get("size", 54.0)), 54.0), 24.0, 120.0),
            color=_safe_color(payload.get("c", payload.get("color", PALETTE[0]))),
            depth=clamp(_safe_float(payload.get("d", payload.get("depth", 0.6)), 0.6), 0.0, 1.0),
            phase=_safe_float(payload.get("ph", payload.get("phase", 0.0)), 0.0),
            age=max(0.0, _safe_float(payload.get("a", payload.get("age", 0.0)), 0.0)),
            wander_angle=_safe_float(
                payload.get("wa", payload.get("wander_angle", math.atan2(velocity.y, velocity.x))),
                math.atan2(velocity.y, velocity.x),
            ),
            wander_jitter=clamp(
                _safe_float(payload.get("wj", payload.get("wander_jitter", 0.55)), 0.55),
                0.1,
                1.5,
            ),
            turn_bias=clamp(
                _safe_float(payload.get("tb", payload.get("turn_bias", 0.0)), 0.0),
                -0.6,
                0.6,
            ),
            target_interval=clamp(
                _safe_float(payload.get("ti", payload.get("target_interval", 4.0)), 4.0),
                1.5,
                8.0,
            ),
            wander_timer=max(
                0.0,
                _safe_float(payload.get("wt", payload.get("wander_timer", 0.0)), 0.0),
            ),
            turn_progress=max(
                0.0,
                _safe_float(payload.get("tp", payload.get("turn_progress", 0.0)), 0.0),
            ),
            turn_duration=max(
                0.0,
                _safe_float(payload.get("td", payload.get("turn_duration", 0.0)), 0.0),
            ),
            turn_start_angle=_safe_float(
                payload.get("tsa", payload.get("turn_start_angle", 0.0)),
                0.0,
            ),
            turn_end_angle=_safe_float(
                payload.get("tea", payload.get("turn_end_angle", 0.0)),
                0.0,
            ),
            turn_direction=_safe_int(
                payload.get("tdr", payload.get("turn_direction", 0)),
                0,
            ),
            turn_cooldown=max(
                0.0,
                _safe_float(payload.get("tc", payload.get("turn_cooldown", 0.0)), 0.0),
            ),
            current_node_id=_safe_node_id(
                payload.get("currentNodeId", payload.get("current_node_id"))
            ),
            animation_state=_safe_animation_state(
                payload.get("animationState", payload.get("animation_state")),
                ANIMATION_SWIMMING,
            ),
            is_transferring=_safe_bool(
                payload.get("isTransferring", payload.get("is_transferring")),
                False,
            ),
        )
        if "animationState" not in payload and "animation_state" not in payload:
            fish.animation_state = fish.effective_animation_state
        return fish

    @classmethod
    def from_expired_transfer_payload(cls, payload: dict, bounds: tuple[int, int]) -> "Fish":
        """移交未确认时把鱼恢复到发送端边缘，并反向弹回屏内。"""
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


def create_random_fish(
    bounds: tuple[int, int],
    rng: random.Random,
    speed_multiplier: float = 1.0,
    *,
    current_node_id: str | None = None,
) -> Fish:
    """创建一条随机鱼，初始化个体差异让鱼群轨迹不完全同步。"""
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
        wander_angle=angle,
        wander_jitter=rng.uniform(0.35, 0.95),
        turn_bias=rng.gauss(0.0, 0.18),
        target_interval=rng.uniform(2.5, 6.5),
        current_node_id=current_node_id,
    )
