from __future__ import annotations

from array import array
import math
from pathlib import Path
import random

import pygame


DEFAULT_BACKGROUND_SOUND = (
    Path(__file__).resolve().parent
    / "assets"
    / "audio"
    / "background_fish_in_river.mp3"
)


class AudioController:
    """背景水声控制器：优先播放资源文件，失败时合成一段循环水流声。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.available = False
        self._sound: pygame.mixer.Sound | None = None
        self._channel: pygame.mixer.Channel | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            # mixer 可能在无声卡或 CI 环境不可用，调用方允许静默降级。
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=1024)
            self._sound = self._load_background_sound() or self._build_water_loop()
            self._channel = self._sound.play(loops=-1, fade_ms=700)
            if self._channel:
                self._channel.set_volume(0.28)
            self.available = True
        except pygame.error:
            self.available = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            if self._channel:
                self._channel.fadeout(350)
            return
        if self.available and self._sound:
            self._channel = self._sound.play(loops=-1, fade_ms=350)
            if self._channel:
                self._channel.set_volume(0.28)
        else:
            self.start()

    def stop(self) -> None:
        if self._channel:
            self._channel.fadeout(300)

    def _load_background_sound(self) -> pygame.mixer.Sound | None:
        if not DEFAULT_BACKGROUND_SOUND.exists():
            return None
        try:
            return pygame.mixer.Sound(str(DEFAULT_BACKGROUND_SOUND))
        except pygame.error:
            return None

    def _build_water_loop(self) -> pygame.mixer.Sound:
        mixer_init = pygame.mixer.get_init()
        sample_rate = mixer_init[0] if mixer_init else 22050
        channels = mixer_init[2] if mixer_init else 2
        seconds = 6.0
        rng = random.Random(601)
        total = int(sample_rate * seconds)

        # 多层噪声分别模拟低频水流、细碎气泡和左右声道的空间漂移。
        base_noise = [rng.uniform(-1.0, 1.0) for _ in range(total)]
        fine_noise = [rng.uniform(-1.0, 1.0) for _ in range(total)]
        stereo_noise = [rng.uniform(-1.0, 1.0) for _ in range(total)]

        flowing_body = self._moving_average_circular(base_noise, max(2, sample_rate // 180))
        low_wash = self._moving_average_circular(base_noise, max(8, sample_rate // 38))
        slow_surge = self._moving_average_circular(
            [rng.uniform(-1.0, 1.0) for _ in range(total)],
            max(32, sample_rate // 3),
        )
        spray = self._build_spray_layer(total, sample_rate, rng)
        stereo_spread = self._moving_average_circular(stereo_noise, max(2, sample_rate // 260))

        samples = array("h")
        for index in range(total):
            t = index / sample_rate
            pulse = 0.82 + slow_surge[index] * 0.22 + math.sin(t * math.tau * 0.13) * 0.06
            stream = low_wash[index] * 0.58 + flowing_body[index] * 0.34
            hiss = fine_noise[index] * 0.035
            value = (stream * pulse) + hiss + spray[index]
            side = stereo_spread[index] * 0.05 + spray[(index + total // 7) % total] * 0.25
            if channels == 1:
                samples.append(self._to_sample(value * 0.72))
            else:
                samples.append(self._to_sample((value - side) * 0.72))
                samples.append(self._to_sample((value + side) * 0.72))
        return pygame.mixer.Sound(buffer=samples.tobytes())

    @staticmethod
    def _moving_average_circular(values: list[float], radius: int) -> list[float]:
        """环形移动平均，保证合成音频首尾拼接时没有突兀断点。"""
        if not values:
            return []
        radius = min(max(0, radius), len(values) - 1)
        if radius == 0:
            return values[:]
        extended = values[-radius:] + values + values[:radius]
        prefix = [0.0]
        for value in extended:
            prefix.append(prefix[-1] + value)
        window = radius * 2 + 1
        return [
            (prefix[index + window] - prefix[index]) / window
            for index in range(len(values))
        ]

    @staticmethod
    def _build_spray_layer(total: int, sample_rate: int, rng: random.Random) -> list[float]:
        """生成短促喷溅层，让循环水声不只是平稳白噪声。"""
        spray = [0.0 for _ in range(total)]
        burst_count = max(8, int(total / sample_rate * 9))
        for _ in range(burst_count):
            start = rng.randrange(total)
            length = rng.randint(max(80, sample_rate // 120), max(160, sample_rate // 24))
            gain = rng.uniform(0.018, 0.075)
            for offset in range(length):
                index = (start + offset) % total
                progress = offset / max(1, length - 1)
                envelope = math.sin(progress * math.pi) ** 1.8
                spray[index] += rng.uniform(-1.0, 1.0) * envelope * gain
        return spray

    @staticmethod
    def _to_sample(value: float) -> int:
        return int(max(-1.0, min(1.0, value)) * 32767)
