from astrbot.api import logger
from .command_utils import get_wake_prefixes, is_valid_command_match, parse_commands


class NoWakeManager:
    def __init__(self, context, config: dict, section_config: dict, command_catalog):
        self.context = context
        self.command_catalog = command_catalog
        self.all_commands_no_wake = section_config.get("all_commands_no_wake", False)
        self.no_wake_blacklist = set(section_config.get("no_wake_blacklist", []))
        self.no_wake_commands: set[str] = set()
        self._manual_no_wake_commands = parse_commands(
            section_config.get("no_wake_commands", [])
        )
        self.no_wake_commands = set(self._manual_no_wake_commands)
        self._plugin_no_wake_commands: dict[str, set[str]] = {}
        self._last_global_command_snapshot: frozenset[str] | None = None
        self.whitelist_groups = [
            str(sid) for sid in section_config.get("no_wake_whitelist_groups", [])
        ]

    def get_wake_prefixes(self) -> list[str]:
        return get_wake_prefixes(self.context)

    def is_no_wake_text(self, text: str, wake_prefixes: list[str]) -> bool:
        """判断文本（去除唤醒前缀后）是否命中免唤醒指令列表。"""
        if not self.no_wake_commands or not text:
            return False
        normalized_text = text
        for prefix in wake_prefixes:
            if prefix and text.startswith(prefix):
                normalized_text = text[len(prefix) :].lstrip()
                break
        if not normalized_text:
            return False
        for command in self.no_wake_commands:
            if is_valid_command_match(normalized_text, command):
                return True
        return False

    def get_configured_command_names(self) -> set[str]:
        return set(self._manual_no_wake_commands)

    def _plugin_cache_key(
        self,
        plugin_name: str | None = None,
        module_path: str | None = None,
    ) -> str:
        if module_path:
            return f"module:{module_path}"
        if plugin_name:
            return f"name:{plugin_name}"
        return ""

    def _get_activated_plugin_index(self) -> tuple[set[str], set[str]]:
        activated_modules: set[str] = set()
        activated_names: set[str] = set()
        try:
            for metadata in self.context.get_all_stars():
                if not getattr(metadata, "activated", True):
                    continue
                md_path = getattr(metadata, "module_path", None)
                md_name = getattr(metadata, "name", None)
                if md_path:
                    activated_modules.add(md_path)
                if md_name:
                    activated_names.add(md_name)
        except Exception as e:
            logger.warning("[指令模拟器] 获取激活插件索引失败: %s", e)
        return activated_modules, activated_names

    def _is_command_from_activated_plugin(
        self,
        command_info: dict,
        activated_modules: set[str],
        activated_names: set[str],
    ) -> bool:
        cmd_module = command_info.get("module_path")
        cmd_plugin = command_info.get("plugin")
        if cmd_module:
            return cmd_module in activated_modules
        if cmd_plugin:
            return cmd_plugin in activated_names
        return False

    def _extract_enabled_command_names(
        self,
        commands: list[dict],
        plugin_name: str | None = None,
        module_path: str | None = None,
    ) -> set[str]:
        activated_modules, activated_names = self._get_activated_plugin_index()
        command_names: set[str] = set()
        for cmd in self._iter_command_records(commands):
            if not cmd.get("enabled", True):
                continue
            if plugin_name and cmd.get("plugin") != plugin_name:
                continue
            if module_path and cmd.get("module_path") != module_path:
                continue
            if not self._is_command_from_activated_plugin(
                cmd, activated_modules, activated_names
            ):
                continue
            effective_cmd = cmd.get("effective_command", "")
            if effective_cmd:
                command_names.add(effective_cmd)
            aliases = cmd.get("aliases", [])
            for alias in aliases:
                if alias and alias.strip():
                    command_names.add(alias.strip())
        return command_names

    @staticmethod
    def _iter_command_records(commands: list[dict]):
        for command in commands:
            if not isinstance(command, dict):
                continue
            yield command
            sub_commands = command.get("sub_commands", [])
            if isinstance(sub_commands, list):
                yield from NoWakeManager._iter_command_records(sub_commands)

    def _group_enabled_command_names_by_plugin(
        self, commands: list[dict]
    ) -> dict[str, set[str]]:
        grouped: dict[str, set[str]] = {}
        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            cache_key = self._plugin_cache_key(
                plugin_name=cmd.get("plugin"),
                module_path=cmd.get("module_path"),
            )
            if not cache_key:
                continue
            cmd_names = self._extract_enabled_command_names([cmd])
            if not cmd_names:
                continue
            if cache_key not in grouped:
                grouped[cache_key] = set()
            grouped[cache_key].update(cmd_names)
        return grouped

    def _apply_blacklist(self, command_names: set[str]) -> set[str]:
        if not self.no_wake_blacklist:
            return command_names
        return command_names - self.no_wake_blacklist

    def _rebuild(self):
        merged_commands = set(self._manual_no_wake_commands)
        for cmd_names in self._plugin_no_wake_commands.values():
            merged_commands.update(cmd_names)
        self.no_wake_commands = merged_commands

    async def initialize_all_commands(self):
        try:
            commands = self.command_catalog.commands
            plugin_command_map = self._group_enabled_command_names_by_plugin(commands)
            rebuilt_map: dict[str, set[str]] = {}
            for cache_key, cmd_names in plugin_command_map.items():
                rebuilt_map[cache_key] = self._apply_blacklist(cmd_names)
            self._plugin_no_wake_commands = rebuilt_map
            self._rebuild()
            current_snapshot = frozenset(self.no_wake_commands)
            if current_snapshot != self._last_global_command_snapshot:
                action = (
                    "已启用"
                    if self._last_global_command_snapshot is None
                    else "已刷新"
                )
                logger.info(
                    "[指令模拟器] 全局免唤醒指令%s，共 %s 条",
                    action,
                    len(current_snapshot),
                )
                self._last_global_command_snapshot = current_snapshot
        except Exception as e:
            logger.error("[指令模拟器] 获取所有指令失败: %s", e)

    async def refresh_for_plugin(self, metadata):
        if not self.all_commands_no_wake:
            return
        plugin_name = getattr(metadata, "name", None)
        module_path = getattr(metadata, "module_path", None)
        if not plugin_name and not module_path:
            return
        try:
            commands = self.command_catalog.commands
            plugin_cmd_names = self._extract_enabled_command_names(
                commands,
                plugin_name=plugin_name,
                module_path=module_path,
            )
            plugin_cmd_names = self._apply_blacklist(plugin_cmd_names)
            cache_key = self._plugin_cache_key(
                plugin_name=plugin_name, module_path=module_path
            )
            if not cache_key:
                return
            before_count = len(self.no_wake_commands)
            self._plugin_no_wake_commands[cache_key] = plugin_cmd_names
            self._rebuild()
            added_count = len(self.no_wake_commands) - before_count
            if plugin_cmd_names or added_count != 0:
                logger.info(
                    "[指令模拟器] 插件加载后已刷新免唤醒指令: %s，新增 %s 条",
                    plugin_name or module_path,
                    added_count,
                )
        except Exception as e:
            logger.warning("[指令模拟器] 插件加载后刷新免唤醒指令失败: %s", e)

    async def remove_for_plugin(self, metadata):
        if not self.all_commands_no_wake:
            return
        plugin_name = getattr(metadata, "name", None)
        module_path = getattr(metadata, "module_path", None)
        cache_key = self._plugin_cache_key(
            plugin_name=plugin_name, module_path=module_path
        )
        if not cache_key:
            return
        removed = len(self._plugin_no_wake_commands.get(cache_key, set()))
        self._plugin_no_wake_commands.pop(cache_key, None)
        self._rebuild()
        if removed > 0:
            logger.info(
                "[指令模拟器] 插件卸载后已刷新免唤醒指令: %s，移除 %s 条",
                plugin_name or module_path,
                removed,
            )
