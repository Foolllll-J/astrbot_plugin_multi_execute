from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from astrbot.api import logger
from astrbot.core.star.command_management import list_commands


class CommandCatalog:
    """缓存 AstrBot 当前已启用的指令目录，供多个功能模块复用。"""

    def __init__(self):
        self.commands: list[dict[str, Any]] = []
        self.loaded = False

    async def refresh(self) -> bool:
        try:
            commands = await list_commands()
            self.commands = commands if isinstance(commands, list) else []
            self.loaded = True
            return True
        except Exception as exc:
            logger.warning("[指令模拟器] 刷新指令目录失败: %s", exc)
            return False

    def iter_commands(self) -> Iterator[dict[str, Any]]:
        """递归遍历普通指令、指令组及其子指令。"""

        def walk(commands: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
            for command in commands:
                if not isinstance(command, dict):
                    continue
                yield command
                sub_commands = command.get("sub_commands", [])
                if isinstance(sub_commands, list):
                    yield from walk(sub_commands)

        yield from walk(self.commands)
