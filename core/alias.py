import random


DISGUISE_ALIAS_EXTRA_KEY = "__multi_execute_disguise_alias"


class AliasManager:
    def __init__(self):
        self.alias_rules: dict[str, list[str]] = {}

    def load_rules(self, rules_config: list | None) -> dict[str, list[str]]:
        if not isinstance(rules_config, list):
            return {}
        rules: dict[str, list[str]] = {}
        for raw_rule in rules_config:
            if not isinstance(raw_rule, dict):
                continue
            # 只处理别名模板条目（由 alias_commands 字段标识）
            alias_commands = raw_rule.get("alias_commands")
            if not alias_commands:
                continue
            target_commands = raw_rule.get("target_commands", [])
            normalized_targets: list[str] = []
            if isinstance(target_commands, list):
                for t in target_commands:
                    if isinstance(t, str) and t.strip():
                        normalized_targets.append(t.strip())
            elif isinstance(target_commands, str):
                t = target_commands.strip()
                if t:
                    normalized_targets.append(t)
            if not normalized_targets:
                continue
            if isinstance(alias_commands, list):
                for alias in alias_commands:
                    if isinstance(alias, str) and alias.strip():
                        alias = alias.strip()
                        if alias not in rules:
                            rules[alias] = []
                        rules[alias] = normalized_targets
        self.alias_rules = rules
        return rules

    def check_alias(
        self, text: str, wake_prefixes: list[str] | None = None
    ) -> str | None:
        if not self.alias_rules:
            return None
        result = self._match_alias(text)
        if result is not None:
            return result
        if wake_prefixes:
            for prefix in wake_prefixes:
                if prefix and text.startswith(prefix):
                    stripped = text[len(prefix) :].lstrip()
                    if not stripped:
                        continue
                    result = self._match_alias(stripped)
                    if result is not None:
                        return result
        return None

    def _match_alias(self, text: str) -> str | None:
        matched_alias = None
        matched_targets: list[str] = []
        for alias, targets in self.alias_rules.items():
            if not alias or not targets:
                continue
            if text == alias or text.startswith(alias + " "):
                if matched_alias is None or len(alias) > len(matched_alias):
                    matched_alias = alias
                    matched_targets = targets
        if matched_alias is None or not matched_targets:
            return None
        target = random.choice(matched_targets)
        suffix = text[len(matched_alias) :]
        return target + suffix
