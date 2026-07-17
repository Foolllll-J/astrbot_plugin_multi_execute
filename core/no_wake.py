from astrbot.api import logger
from astrbot.core.star.command_management import list_commands
from .command_utils import get_wake_prefixes, parse_commands


class NoWakeManager:
    def __init__(self, context, config: dict, section_config: dict):
        self.context = context
        self.all_commands_no_wake = section_config.get("all_commands_no_wake", False)
        self.no_wake_blacklist = set(section_config.get("no_wake_blacklist", []))
        self.no_wake_commands: set[str] = set()
        self._manual_no_wake_commands = parse_commands(
            section_config.get("no_wake_commands", [])
        )
        self.no_wake_commands = set(self._manual_no_wake_commands)
        self._plugin_no_wake_commands: dict[str, set[str]] = {}
        self.whitelist_groups = [
            str(sid) for sid in section_config.get("no_wake_whitelist_groups", [])
        ]

    def get_wake_prefixes(self) -> list[str]:
        return get_wake_prefixes(self.context)

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
            logger.warning(f"[指令模拟器] 获取激活插件索引失败: {e}")
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
        for cmd in commands:
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
            commands = await list_commands()
            plugin_command_map = self._group_enabled_command_names_by_plugin(commands)
            rebuilt_map: dict[str, set[str]] = {}
            for cache_key, cmd_names in plugin_command_map.items():
                rebuilt_map[cache_key] = self._apply_blacklist(cmd_names)
            self._plugin_no_wake_commands = rebuilt_map
            self._rebuild()
            all_cmd_names = set()
            for cmd_names in self._plugin_no_wake_commands.values():
                all_cmd_names.update(cmd_names)
            logger.info(
                f"[指令模拟器] 全局免唤醒模式已启用，共 {len(all_cmd_names)} 个指令可免唤醒触发"
            )
        except Exception as e:
            logger.error(f"[指令模拟器] 获取所有指令失败: {e}")

    async def refresh_for_plugin(self, metadata):
        if not self.all_commands_no_wake:
            return
        plugin_name = getattr(metadata, "name", None)
        module_path = getattr(metadata, "module_path", None)
        if not plugin_name and not module_path:
            return
        try:
            commands = await list_commands()
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
                    f"[指令模拟器] 插件加载增量刷新完成: {plugin_name or module_path}，新增 {added_count} 个免唤醒指令"
                )
        except Exception as e:
            logger.warning(f"[指令模拟器] 插件加载增量刷新失败: {e}")

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
                f"[指令模拟器] 插件卸载增量刷新完成: {plugin_name or module_path}，移除 {removed} 个免唤醒指令"
            )
