import random
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from .command_utils import (
    normalize_disguise_command,
    extract_command_key,
    stringify_result_for_log,
    get_wake_prefixes,
)

DISGUISE_REPLY_EXTRA_KEY = "__multi_execute_disguise_reply"


class DisguiseManager:
    def __init__(self, context):
        self.context = context
        self.rules: dict[str, list[str]] = {}

    def load_rules(self, rules_config: list | None) -> dict[str, list[str]]:
        wake_prefixes = get_wake_prefixes(self.context)
        if not isinstance(rules_config, list):
            return {}
        rules: dict[str, list[str]] = {}
        for raw_rule in rules_config:
            if not isinstance(raw_rule, dict):
                continue
            # 跳过别名模板条目（由 AliasManager 处理）
            if raw_rule.get("alias_commands"):
                continue
            for target_command in raw_rule.get("target_command", []):
                target_command = normalize_disguise_command(
                    target_command, wake_prefixes
                )
                if not target_command:
                    continue
                reply_texts = raw_rule.get("reply_texts", [])
                normalized_texts: list[str] = []
                if isinstance(reply_texts, list):
                    for text in reply_texts:
                        if isinstance(text, str):
                            text = text.strip()
                            if text:
                                normalized_texts.append(text)
                elif isinstance(reply_texts, str):
                    t = reply_texts.strip()
                    if t:
                        normalized_texts.append(t)
                rules[target_command] = normalized_texts
        self.rules = rules
        return rules

    def get_reply_texts(self, command: str) -> list[str] | None:
        if not isinstance(self.rules, dict):
            return None
        wake_prefixes = get_wake_prefixes(self.context)
        target_command = extract_command_key(command, wake_prefixes)
        if not target_command:
            return None
        if target_command not in self.rules:
            return None
        texts = self.rules.get(target_command, [])
        return list(texts) if isinstance(texts, list) else []

    def apply_reply(self, event: AstrMessageEvent, command_text: str) -> bool:
        disguise_reply_texts = self.get_reply_texts(command_text)
        if disguise_reply_texts is None:
            return False
        event.set_extra(DISGUISE_REPLY_EXTRA_KEY, disguise_reply_texts)
        return True

    async def handle_result(self, event: AstrMessageEvent):
        extra = event.get_extra(DISGUISE_REPLY_EXTRA_KEY)
        if extra is None:
            return
        try:
            reply_texts = list(extra)
        except Exception:
            return
        original_reply = stringify_result_for_log(event)
        selected = random.choice(reply_texts) if reply_texts else ""
        logger.info(
            f"[指令模拟器] 伪装回复前原始结果: 指令：{event.message_str}, "
            f"原始回复：{original_reply}"
        )
        if selected:
            event.set_result(event.plain_result(selected))
        else:
            event.set_result(event.make_result())
            event.stop_event()
