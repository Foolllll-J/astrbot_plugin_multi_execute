from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain, At


def get_wake_prefixes(context) -> list[str]:
    try:
        config = context.get_config()
        prefixes = config.get("wake_prefix")
        if not prefixes:
            return []
        if isinstance(prefixes, str):
            return [prefixes]
        return list(prefixes)
    except Exception as e:
        logger.warning(f"获取唤醒前缀失败: {e}")
        return []


def normalize_disguise_command(command: str, wake_prefixes: list[str]) -> str:
    if not isinstance(command, str):
        return ""
    text = command.strip()
    if not text:
        return ""
    prefixes = wake_prefixes or ["/"]
    for prefix in prefixes:
        if prefix and text.startswith(prefix):
            text = text[len(prefix) :].lstrip()
            break
    return text.split(" ", 1)[0]


def extract_command_key(command: str, wake_prefixes: list[str]) -> str:
    if not isinstance(command, str):
        return ""
    text = command.strip()
    if not text:
        return ""
    prefixes = wake_prefixes or ["/"]
    matched_prefix = ""
    for prefix in prefixes:
        if prefix and text.startswith(prefix):
            matched_prefix = prefix
            break
    if matched_prefix:
        text = text[len(matched_prefix) :].lstrip()
    if not text:
        return ""
    return text.split(" ", 1)[0]


def is_valid_command_match(text: str, command: str) -> bool:
    if not text.startswith(command):
        return False
    if len(text) == len(command):
        return True
    next_char = text[len(command)]
    if next_char != " ":
        return False
    return True


def extract_message_components(event: AstrMessageEvent) -> list:
    components = []
    try:
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            message_chain = event.message_obj.message
        else:
            message_chain = event.get_messages()
        if message_chain:
            for comp in message_chain:
                components.append(comp)
    except (AttributeError, TypeError) as e:
        logger.warning(f"[指令模拟器] 提取消息组件时出错: {e}")
    return components


def build_prefixed_components(components: list, prefix: str) -> list:
    if not prefix:
        return list(components) if components else []
    if not components:
        return [Plain(prefix)]
    first = components[0]
    new_components = []
    if isinstance(first, Plain):
        new_components.append(Plain(prefix + first.text))
        new_components.extend(components[1:])
    else:
        new_components.append(Plain(prefix))
        new_components.extend(components)
    return new_components


def replace_first_plain_text(components: list, new_text: str) -> list:
    if not components:
        return [Plain(new_text)]
    result = list(components)
    for i, comp in enumerate(result):
        if isinstance(comp, Plain):
            result[i] = Plain(new_text)
            return result
    result.insert(0, Plain(new_text))
    return result


def extract_at_user(event: AstrMessageEvent) -> tuple[str | None, str | None]:
    messages = event.get_messages()
    self_id = event.get_self_id()
    for comp in messages:
        if isinstance(comp, At):
            if self_id and str(comp.qq) == str(self_id):
                continue
            user_id = str(comp.qq)
            user_name = getattr(comp, "name", None) or None
            return user_id, user_name
    return None, None


def extract_after_target_at(event: AstrMessageEvent, target_user_id: str) -> list:
    messages = event.get_messages()
    result = []
    found_target_at = False
    for comp in messages:
        if isinstance(comp, At) and str(comp.qq) == str(target_user_id):
            found_target_at = True
            continue
        if found_target_at:
            result.append(comp)
    return result


def stringify_result_for_log(event: AstrMessageEvent) -> str:
    result = event.get_result()
    if result is None:
        return "[无回复]"
    try:
        text = result.get_plain_text(with_other_comps_mark=True).strip()
    except Exception as e:
        logger.warning(f"[指令模拟器] 读取伪装前回复失败: {e}")
        return "[回复解析失败]"
    if text:
        return text
    if getattr(result, "chain", None):
        return "[空文本回复]"
    return "[无回复]"


def parse_commands(commands_config: list | None = None) -> set:
    commands_config = commands_config or []
    result = set()
    for item in commands_config:
        if isinstance(item, str) and item.strip():
            result.add(item.strip())
    if result:
        logger.info(
            f"[指令模拟器] 插件已加载，共 {len(result)} 个免唤醒指令: {list(result)}"
        )
    return result


def is_allowed(event: AstrMessageEvent, whitelist: list) -> bool:
    if event.is_admin():
        return True
    if not whitelist:
        return True
    return event.get_sender_id() in whitelist


def is_no_wake_trigger_allowed(event: AstrMessageEvent, whitelist_groups: list) -> bool:
    if not whitelist_groups:
        return True
    group_id = event.get_group_id()
    if not group_id:
        return False
    group_id = str(group_id).split("#")[0]
    return group_id in whitelist_groups


def is_user_admin(context, user_id: str) -> bool:
    try:
        config = context.get_config()
        admins_id = config.get("admins_id", [])
        return str(user_id) in [str(admin_id) for admin_id in admins_id]
    except Exception as e:
        logger.warning(f"检查用户管理员权限失败: {e}")
        return False
