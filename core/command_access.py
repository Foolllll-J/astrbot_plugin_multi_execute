from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger


COMMAND_ACCESS_CHECKED_EXTRA_KEY = "__multi_execute_command_access_checked"


@dataclass(frozen=True)
class CommandAccessRule:
    commands: frozenset[str]
    policy: str
    users: frozenset[str]
    groups: frozenset[str]
    notify_user: bool = True


@dataclass(frozen=True)
class CommandAccessDecision:
    command: str
    notify_user: bool


class CommandAccessManager:
    """根据配置拦截指定用户的指令访问。"""

    _POLICY_ALIASES = {
        "blacklist": "blacklist",
        "黑名单": "blacklist",
        "whitelist": "whitelist",
        "白名单": "whitelist",
    }

    def __init__(self, rules_config: Any = None):
        self.rules_by_command: dict[str, list[CommandAccessRule]] = {}
        self.command_alias_groups: dict[str, set[str]] = {}
        self.rule_count = 0
        self.reload(rules_config)

    @staticmethod
    def _values(value: Any) -> list[Any]:
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        if isinstance(value, str):
            return [value]
        return []

    @staticmethod
    def _normalize(value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).strip().split()).casefold()

    @classmethod
    def _normalize_set(cls, value: Any) -> frozenset[str]:
        return frozenset(
            normalized
            for item in cls._values(value)
            if (normalized := cls._normalize(item))
        )

    @classmethod
    def _normalize_command_names(
        cls, command_names: Iterable[Any] | None
    ) -> frozenset[str]:
        if command_names is None:
            return frozenset()
        return frozenset(
            normalized
            for item in command_names
            if (normalized := cls._normalize(item))
        )

    @classmethod
    def _policy(cls, value: Any) -> str:
        normalized = cls._normalize(value)
        return cls._POLICY_ALIASES.get(normalized, "blacklist")

    def reload(self, rules_config: Any = None) -> None:
        self.rules_by_command.clear()
        self.rule_count = 0

        if not isinstance(rules_config, list):
            return

        for index, raw_rule in enumerate(rules_config, start=1):
            if not isinstance(raw_rule, dict):
                logger.warning(
                    "[指令模拟器] 第 %s 条指令访问规则不是对象，已跳过",
                    index,
                )
                continue

            commands = self._normalize_set(raw_rule.get("commands"))
            users = self._normalize_set(raw_rule.get("users"))
            groups = self._normalize_set(raw_rule.get("groups"))
            policy = self._policy(raw_rule.get("policy", "blacklist"))
            notify_user = raw_rule.get("notify_user", True)
            if not isinstance(notify_user, bool):
                notify_user = True

            if not commands:
                logger.warning(
                    "[指令模拟器] 第 %s 条指令访问规则未配置指令，已跳过",
                    index,
                )
                continue

            if policy == "whitelist" and not users:
                logger.warning(
                    "[指令模拟器] 第 %s 条指令访问白名单规则未配置用户，已跳过",
                    index,
                )
                continue

            rule = CommandAccessRule(
                commands=commands,
                policy=policy,
                users=users,
                groups=groups,
                notify_user=notify_user,
            )
            self.rule_count += 1
            for command in commands:
                self.rules_by_command.setdefault(command, []).append(rule)

        if self.rule_count > 0:
            logger.info(
                "[指令模拟器] 已加载 %s 条指令访问规则，覆盖 %s 个指令",
                self.rule_count,
                len(self.rules_by_command),
            )

    async def initialize(self, command_catalog=None) -> None:
        """同步 AstrBot 当前已注册的主指令和别名。"""
        if not self.rules_by_command:
            return

        try:
            if command_catalog is None:
                from astrbot.core.star.command_management import list_commands

                commands = await list_commands()
                command_records = self._iter_command_records(commands)
            else:
                command_records = command_catalog.iter_commands()

            command_alias_groups: dict[str, set[str]] = {}
            for command_info in command_records:
                names = self._normalize_command_names(
                    [
                        command_info.get("effective_command", ""),
                        *(command_info.get("aliases", []) or []),
                    ]
                )
                if not names:
                    continue
                for name in names:
                    command_alias_groups.setdefault(name, set()).update(names)
            if command_alias_groups != self.command_alias_groups:
                logger.info(
                    "[指令模拟器] 已同步 %s 个指令别名组",
                    len(command_alias_groups),
                )
            self.command_alias_groups = command_alias_groups
        except Exception as exc:
            logger.warning("[指令模拟器] 同步指令注册表失败: %s", exc)

    @staticmethod
    def _iter_command_records(commands):
        for command in commands or []:
            if not isinstance(command, dict):
                continue
            yield command
            sub_commands = command.get("sub_commands", [])
            if isinstance(sub_commands, list):
                yield from CommandAccessManager._iter_command_records(sub_commands)

    def command_names_from_text(
        self, text: Any, wake_prefixes: Iterable[str] | None = None
    ) -> frozenset[str]:
        normalized_text = self._normalize(text)
        if not normalized_text:
            return frozenset()

        prefixes = sorted(
            {
                prefix
                for prefix in (wake_prefixes or [])
                if isinstance(prefix, str) and prefix
            },
            key=len,
            reverse=True,
        )
        for prefix in prefixes:
            normalized_prefix = self._normalize(prefix)
            if normalized_prefix and normalized_text.startswith(normalized_prefix):
                normalized_text = normalized_text[len(normalized_prefix) :].lstrip()
                break

        if not normalized_text:
            return frozenset()

        configured_names = {
            command
            for command in self.rules_by_command
            if normalized_text == command
            or normalized_text.startswith(command + " ")
        }

        for registered_name in sorted(
            self.command_alias_groups,
            key=len,
            reverse=True,
        ):
            if normalized_text == registered_name or normalized_text.startswith(
                registered_name + " "
            ):
                return frozenset(
                    {registered_name}
                    | configured_names
                    | self.command_alias_groups.get(registered_name, set())
                )

        command_name = normalized_text.split(" ", 1)[0]
        return frozenset(
            {command_name}
            | configured_names
            | self.command_alias_groups.get(command_name, set())
        )

    @classmethod
    def command_names_from_handlers(cls, handlers: Iterable[Any] | None) -> frozenset[str]:
        names: set[str] = set()
        for handler in handlers or []:
            for event_filter in getattr(handler, "event_filters", []) or []:
                get_names = getattr(event_filter, "get_complete_command_names", None)
                if callable(get_names):
                    try:
                        names.update(
                            normalized
                            for item in get_names()
                            if (normalized := cls._normalize(item))
                        )
                    except Exception as exc:
                        logger.debug(
                            "[指令模拟器] 读取指令名称失败: %s",
                            exc,
                        )

                command_name = cls._normalize(
                    getattr(event_filter, "command_name", "")
                )
                if command_name:
                    names.add(command_name)
        return frozenset(names)

    def blocked_command(
        self,
        command_names: Iterable[Any] | None,
        user_id: Any,
        group_id: Any = "",
    ) -> str | None:
        decision = self.blocked_decision(command_names, user_id, group_id)
        return decision.command if decision else None

    def blocked_decision(
        self,
        command_names: Iterable[Any] | None,
        user_id: Any,
        group_id: Any = "",
    ) -> CommandAccessDecision | None:
        normalized_user = self._normalize(user_id)
        if not normalized_user:
            return None

        normalized_group = self._normalize(group_id)
        normalized_commands = self._normalize_command_names(command_names)
        blocked_command = None
        notify_user = False
        for command in sorted(normalized_commands):
            for rule in self.rules_by_command.get(command, []):
                if rule.groups and normalized_group not in rule.groups:
                    continue

                if rule.policy == "blacklist":
                    blocked = normalized_user in rule.users
                else:
                    blocked = normalized_user not in rule.users

                if blocked:
                    blocked_command = blocked_command or command
                    notify_user = notify_user or rule.notify_user
        if blocked_command is None:
            return None
        return CommandAccessDecision(blocked_command, notify_user)

    def blocked_for_event(
        self,
        event: Any,
        command_names: Iterable[Any] | None = None,
        user_id: Any = None,
    ) -> str | None:
        decision = self.blocked_decision_for_event(event, command_names, user_id)
        return decision.command if decision else None

    def blocked_decision_for_event(
        self,
        event: Any,
        command_names: Iterable[Any] | None = None,
        user_id: Any = None,
    ) -> CommandAccessDecision | None:
        if command_names is None:
            command_names = self.command_names_from_handlers(
                event.get_extra("activated_handlers", [])
            )
        return self.blocked_decision(
            command_names,
            user_id if user_id is not None else event.get_sender_id(),
            event.get_group_id(),
        )

    def blocked_for_text(
        self,
        event: Any,
        text: Any,
        command_names: Iterable[Any] | None = None,
        user_id: Any = None,
        wake_prefixes: Iterable[str] | None = None,
    ) -> str | None:
        decision = self.blocked_decision_for_text(
            event,
            text,
            command_names,
            user_id,
            wake_prefixes,
        )
        return decision.command if decision else None

    def blocked_decision_for_text(
        self,
        event: Any,
        text: Any,
        command_names: Iterable[Any] | None = None,
        user_id: Any = None,
        wake_prefixes: Iterable[str] | None = None,
    ) -> CommandAccessDecision | None:
        if command_names is None:
            command_names = self.command_names_from_text(text, wake_prefixes)
        return self.blocked_decision_for_event(event, command_names, user_id)
