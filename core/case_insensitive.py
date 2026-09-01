from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CASE_INSENSITIVE_PROCESSED_EXTRA_KEY = "__multi_execute_case_insensitive_processed"


@dataclass(frozen=True)
class _CommandCandidate:
    owner: str
    name: str
    priority: int


class CaseInsensitiveCommandManager:
    """在指定群聊中，将英文指令的大小写变体还原为已注册名称。"""

    def __init__(self, command_catalog, groups: Any = None):
        self.command_catalog = command_catalog
        self.groups = self._normalize_groups(groups)
        self._candidates: dict[str, dict[str, dict[str, int]]] = {}
        self._sorted_candidates: list[_CommandCandidate] = []

    @staticmethod
    def _normalize_groups(groups: Any) -> set[str]:
        if not isinstance(groups, (list, tuple, set, frozenset)):
            return set()
        return {str(group).strip() for group in groups if str(group).strip()}

    @staticmethod
    def _normalize_name(name: Any) -> str:
        if not isinstance(name, str):
            return ""
        return " ".join(name.strip().split())

    @staticmethod
    def _is_english_name(name: str) -> bool:
        return bool(name) and name.isascii() and any(char.isalpha() for char in name)

    @property
    def enabled(self) -> bool:
        return bool(self.groups)

    def is_enabled_for(self, event) -> bool:
        if not self.enabled:
            return False
        group_id = event.get_group_id()
        if not group_id:
            return False
        normalized_group = str(group_id).split("#", 1)[0].strip()
        return normalized_group in self.groups

    def _add_candidate(self, name: Any, owner: str, priority: int) -> None:
        normalized = self._normalize_name(name)
        if not self._is_english_name(normalized):
            return
        folded = normalized.casefold()
        owners = self._candidates.setdefault(folded, {})
        owner_names = owners.setdefault(owner, {})
        previous_priority = owner_names.get(normalized)
        if previous_priority is None or priority < previous_priority:
            owner_names[normalized] = priority

    def rebuild(self, extra_names: Any = None) -> None:
        self._candidates.clear()
        self._sorted_candidates.clear()
        registered_names: set[str] = set()

        for command in self.command_catalog.iter_commands():
            if not command.get("enabled", True):
                continue
            owner = str(
                command.get("handler_full_name")
                or command.get("effective_command")
                or "unknown"
            )
            effective_command = self._normalize_name(command.get("effective_command"))
            if effective_command:
                registered_names.add(effective_command)
                self._add_candidate(effective_command, owner, 0)
            aliases = command.get("aliases", [])
            if isinstance(aliases, (list, tuple, set, frozenset)):
                for alias in aliases:
                    normalized_alias = self._normalize_name(alias)
                    if normalized_alias:
                        registered_names.add(normalized_alias)
                        self._add_candidate(normalized_alias, owner, 1)

        if isinstance(extra_names, (list, tuple, set, frozenset)):
            for name in extra_names:
                normalized_name = self._normalize_name(name)
                if normalized_name and normalized_name not in registered_names:
                    self._add_candidate(
                        normalized_name,
                        f"configured:{normalized_name}",
                        0,
                    )

        for owners in self._candidates.values():
            for owner, names in owners.items():
                for name, priority in names.items():
                    self._sorted_candidates.append(
                        _CommandCandidate(owner, name, priority)
                    )
        self._sorted_candidates.sort(key=self._candidate_sort_key)

    @staticmethod
    def _split_prefix(text: str, wake_prefixes: list[str]) -> tuple[str, str]:
        prefixes = sorted(
            {
                prefix
                for prefix in wake_prefixes
                if isinstance(prefix, str) and prefix
            },
            key=len,
            reverse=True,
        )
        for prefix in prefixes:
            if text.startswith(prefix):
                return prefix, text[len(prefix) :].lstrip()
        return "", text

    @staticmethod
    def _candidate_sort_key(candidate: _CommandCandidate) -> tuple[int, int, int, str]:
        parts_count = len(candidate.name.split(" "))
        return (-parts_count, -len(candidate.name), candidate.priority, candidate.name)

    def rewrite(self, text: str, wake_prefixes: list[str]) -> str | None:
        if not self.enabled or not isinstance(text, str) or not text.strip():
            return None

        prefix, body = self._split_prefix(text.strip(), wake_prefixes)
        body_parts = body.split()
        if not body_parts:
            return None

        exact_matches: list[_CommandCandidate] = []
        folded_matches: list[_CommandCandidate] = []
        for candidate in self._sorted_candidates:
            name_parts = candidate.name.split(" ")
            if len(body_parts) < len(name_parts):
                continue
            command_parts = body_parts[: len(name_parts)]
            if command_parts == name_parts:
                exact_matches.append(candidate)
                continue
            if [part.casefold() for part in command_parts] != [
                part.casefold() for part in name_parts
            ]:
                continue

            owners = self._candidates.get(candidate.name.casefold(), {})
            if len(owners) != 1:
                continue
            folded_matches.append(candidate)

        longest_exact = len(exact_matches[0].name.split(" ")) if exact_matches else 0
        candidate = folded_matches[0] if folded_matches else None
        if candidate is None or longest_exact >= len(candidate.name.split(" ")):
            return None

        name_parts = candidate.name.split(" ")
        suffix = body_parts[len(name_parts) :]
        rewritten_body = candidate.name
        if suffix:
            rewritten_body += " " + " ".join(suffix)
        rewritten = prefix + rewritten_body
        if rewritten == text.strip():
            return None
        return rewritten
