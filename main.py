import asyncio
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain, At

from .core.event_factory import EventFactory
from .core.disguise import DisguiseManager
from .core.alias import AliasManager, DISGUISE_ALIAS_EXTRA_KEY
from .core.case_insensitive import (
    CASE_INSENSITIVE_PROCESSED_EXTRA_KEY,
    CaseInsensitiveCommandManager,
)
from .core.command_catalog import CommandCatalog
from .core.command_access import (
    COMMAND_ACCESS_CHECKED_EXTRA_KEY,
    CommandAccessManager,
)
from .core.no_wake import NoWakeManager
from .core.command_utils import (
    extract_command_key,
    is_valid_command_match,
    extract_message_components,
    build_prefixed_components,
    replace_first_plain_text,
    refresh_temp_media,
    extract_at_user,
    extract_after_target_at,
    is_user_admin,
    is_allowed,
    is_no_wake_trigger_allowed,
    ensure_wake_prefix,
)


class MultiExecutePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.simulate_allowed_users = self.config.get("simulate_allowed_users", [])

        multiply_section = self.config.get("multiply", {})
        if not isinstance(multiply_section, dict):
            multiply_section = {}
        self.multiply_allowed_users = multiply_section.get("allowed_users", [])
        self.interval = multiply_section.get("interval", 1)
        self.max_times = multiply_section.get("max_times", 10)

        no_wake_section = self.config.get("no_wake", {})
        if not isinstance(no_wake_section, dict):
            no_wake_section = {}

        self.command_catalog = CommandCatalog()
        self.no_wake = NoWakeManager(
            context,
            self.config,
            no_wake_section,
            self.command_catalog,
        )
        self.disguise = DisguiseManager(context)
        self.alias = AliasManager()
        self.case_insensitive = CaseInsensitiveCommandManager(
            self.command_catalog,
            self.config.get("case_insensitive_command_groups", []),
        )
        self.command_access = CommandAccessManager(
            self.config.get("command_access_rules", [])
        )
        self.event_factory = EventFactory(context)

        self.disguise.load_rules(self.config.get("disguise", []))
        self.alias.load_rules(self.config.get("disguise", []))

    async def _refresh_command_state(self):
        needs_catalog = (
            self.case_insensitive.enabled
            or self.no_wake.all_commands_no_wake
            or bool(self.command_access.rules_by_command)
        )
        if not needs_catalog:
            return

        await self.command_catalog.refresh()
        self.case_insensitive.rebuild(
            self.alias.get_alias_names()
            | self.no_wake.get_configured_command_names()
        )
        await self.command_access.initialize(self.command_catalog)

    async def initialize(self):
        await self._refresh_command_state()
        if self.no_wake.all_commands_no_wake:
            await self.no_wake.initialize_all_commands()

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        await self._refresh_command_state()
        if self.no_wake.all_commands_no_wake:
            await self.no_wake.initialize_all_commands()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata):
        if getattr(metadata, "module_path", None) == self.__class__.__module__:
            return
        await self._refresh_command_state()
        await self.no_wake.refresh_for_plugin(metadata)

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, metadata):
        await self.no_wake.remove_for_plugin(metadata)
        await self._refresh_command_state()

    def _stop_if_command_blocked(
        self,
        event: AstrMessageEvent,
        *,
        text: str | None = None,
        command_names=None,
        user_id: str | None = None,
        wake_prefixes=None,
    ) -> bool:
        if text is not None:
            blocked_decision = self.command_access.blocked_decision_for_text(
                event,
                text,
                command_names=command_names,
                user_id=user_id,
                wake_prefixes=wake_prefixes,
            )
        else:
            blocked_decision = self.command_access.blocked_decision_for_event(
                event,
                command_names=command_names,
                user_id=user_id,
            )

        if blocked_decision is None:
            return False

        logger.info(
            "[指令模拟器] 已拦截用户 %s 使用指令 %s",
            user_id if user_id is not None else event.get_sender_id(),
            blocked_decision.command,
        )
        if blocked_decision.notify_user:
            event.set_result(event.plain_result("你没有权限使用该指令。"))
        event.stop_event()
        return True

    @filter.on_decorating_result()
    async def on_disguise_reply(self, event: AstrMessageEvent):
        await self.disguise.handle_result(event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=999)
    async def on_message(self, event: AstrMessageEvent):
        try:
            if event.get_extra(
                "multi_execute_origin", False
            ) or event.get_extra(COMMAND_ACCESS_CHECKED_EXTRA_KEY, False):
                return
        except Exception:
            pass

        msg_id = getattr(getattr(event, "message_obj", None), "message_id", "")
        if isinstance(msg_id, str) and msg_id.startswith("command_trigger_"):
            return

        text = event.message_str.strip() if event.message_str else ""
        if not text:
            return

        is_wake = bool(getattr(event, "is_at_or_wake_command", False))

        alias_processed = False
        try:
            alias_processed = event.get_extra(DISGUISE_ALIAS_EXTRA_KEY, False)
        except Exception:
            pass

        case_processed = False
        try:
            case_processed = event.get_extra(
                CASE_INSENSITIVE_PROCESSED_EXTRA_KEY,
                False,
            )
        except Exception:
            pass

        if not case_processed and self.case_insensitive.is_enabled_for(event):
            wake_prefixes = self.no_wake.get_wake_prefixes()
            case_target_text = self.case_insensitive.rewrite(text, wake_prefixes)
            if case_target_text and not is_wake:
                if not (
                    is_no_wake_trigger_allowed(
                        event, self.no_wake.whitelist_groups
                    )
                    and self.no_wake.is_no_wake_text(
                        case_target_text,
                        wake_prefixes,
                    )
                ):
                    case_target_text = None

            if case_target_text:
                original_components = extract_message_components(event)
                rewritten_components = replace_first_plain_text(
                    original_components,
                    case_target_text,
                )
                sender_id = str(event.get_sender_id())
                sender_name = (
                    event.get_sender_name()
                    if hasattr(event, "get_sender_name")
                    else "用户"
                )
                is_admin = False
                try:
                    is_admin = event.is_admin()
                except (AttributeError, TypeError):
                    pass

                temp_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=case_target_text,
                    creator_id=sender_id,
                    creator_name=sender_name,
                    original_components=rewritten_components,
                    is_admin=is_admin,
                    self_id=event.get_self_id(),
                    source_message_id=getattr(
                        getattr(event, "message_obj", None), "message_id", None
                    ),
                )
                refresh_temp_media(rewritten_components, temp_event)
                try:
                    temp_event.is_wake = is_wake
                    temp_event.is_at_or_wake_command = is_wake
                except Exception:
                    pass
                case_command_names = (
                    self.command_access.command_names_from_text(
                        text, wake_prefixes
                    )
                    | self.command_access.command_names_from_text(
                        case_target_text, wake_prefixes
                    )
                )
                if self._stop_if_command_blocked(
                    event,
                    text=case_target_text,
                    command_names=case_command_names,
                    wake_prefixes=wake_prefixes,
                ):
                    return
                temp_event.set_extra(CASE_INSENSITIVE_PROCESSED_EXTRA_KEY, True)
                self.context.get_event_queue().put_nowait(temp_event)
                logger.info(
                    "[指令模拟器] 已将大小写变体还原为指令: %s -> %s",
                    text,
                    case_target_text,
                )
                event.stop_event()
                return

        if not alias_processed:
            wake_prefixes = self.no_wake.get_wake_prefixes()
            alias_target_text = self.alias.check_alias(text, wake_prefixes)
            if alias_target_text and not (
                is_wake
                or (
                    is_no_wake_trigger_allowed(
                        event, self.no_wake.whitelist_groups
                    )
                    and self.no_wake.is_no_wake_text(text, wake_prefixes)
                )
            ):
                alias_target_text = None
            if alias_target_text:
                wake_prefixes = self.no_wake.get_wake_prefixes()
                command_names = (
                    self.command_access.command_names_from_text(
                        alias_target_text, wake_prefixes
                    )
                    | self.command_access.command_names_from_text(text, wake_prefixes)
                )
                if self._stop_if_command_blocked(
                    event,
                    command_names=command_names,
                    wake_prefixes=wake_prefixes,
                ):
                    return

                original_components = extract_message_components(event)
                rewritten_components = replace_first_plain_text(
                    original_components, alias_target_text
                )
                sender_id = str(event.get_sender_id())
                sender_name = (
                    event.get_sender_name()
                    if hasattr(event, "get_sender_name")
                    else "用户"
                )
                is_admin = False
                try:
                    is_admin = event.is_admin()
                except (AttributeError, TypeError):
                    pass

                temp_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=alias_target_text,
                    creator_id=sender_id,
                    creator_name=sender_name,
                    original_components=rewritten_components,
                    is_admin=is_admin,
                    self_id=event.get_self_id(),
                    source_message_id=getattr(
                        getattr(event, "message_obj", None), "message_id", None
                    ),
                )
                refresh_temp_media(rewritten_components, temp_event)
                self.disguise.apply_reply(temp_event, alias_target_text)
                temp_event.set_extra(DISGUISE_ALIAS_EXTRA_KEY, True)
                try:
                    temp_event.is_at_or_wake_command = True
                except Exception:
                    pass
                temp_event.set_extra(COMMAND_ACCESS_CHECKED_EXTRA_KEY, True)
                self.context.get_event_queue().put_nowait(temp_event)
                logger.info(
                    "[指令模拟器] 命中指令别名: %s -> %s",
                    text,
                    alias_target_text,
                )
                event.stop_event()
                return

        wake_prefixes = self.no_wake.get_wake_prefixes()
        command_key = extract_command_key(text, wake_prefixes)
        if not command_key:
            return

        if is_wake and self._stop_if_command_blocked(
            event,
            text=text,
            wake_prefixes=wake_prefixes,
        ):
            return

        if not is_wake:
            if not is_no_wake_trigger_allowed(event, self.no_wake.whitelist_groups):
                return

            matched_prefix = ""
            normalized_text = text
            if wake_prefixes:
                for prefix in wake_prefixes:
                    if prefix and text.startswith(prefix):
                        matched_prefix = prefix
                        normalized_text = text[len(prefix) :].lstrip()
                        break

            if not normalized_text:
                return

            matched_command = None
            for command in self.no_wake.no_wake_commands:
                if is_valid_command_match(normalized_text, command):
                    if matched_command is None or len(command) > len(matched_command):
                        matched_command = command

            if not matched_command:
                return

            if self._stop_if_command_blocked(
                event,
                text=normalized_text,
                wake_prefixes=wake_prefixes,
            ):
                return

            suffix = normalized_text[len(matched_command) :]
            command_prefix = (
                matched_prefix
                if matched_prefix
                else (wake_prefixes[0] if wake_prefixes else "/")
            )
            new_command = f"{command_prefix}{matched_command}{suffix}"

            original_components = extract_message_components(event)
            if matched_prefix:
                prefixed_components = original_components
            else:
                prefixed_components = build_prefixed_components(
                    original_components, command_prefix
                )

            is_admin = False
            try:
                is_admin = event.is_admin()
            except (AttributeError, TypeError):
                pass

            sender_id = str(event.get_sender_id())
            sender_name = (
                event.get_sender_name() if hasattr(event, "get_sender_name") else "用户"
            )

            temp_event = self.event_factory.create_event(
                unified_msg_origin=event.unified_msg_origin,
                command=new_command,
                creator_id=sender_id,
                creator_name=sender_name,
                original_components=prefixed_components,
                is_admin=is_admin,
                self_id=event.get_self_id(),
                source_message_id=getattr(
                    getattr(event, "message_obj", None), "message_id", None
                ),
            )
            refresh_temp_media(prefixed_components, temp_event)

            has_disguise = self.disguise.apply_reply(temp_event, new_command)
            temp_event.set_extra(COMMAND_ACCESS_CHECKED_EXTRA_KEY, True)
            temp_event.set_extra("multi_execute_origin", True)
            self.context.get_event_queue().put_nowait(temp_event)
            logger.debug(
                "[指令模拟器] 命中免唤醒指令=%s，命中伪装=%s",
                matched_command,
                has_disguise,
            )
            logger.info("[指令模拟器] 已派发命令事件: %s", new_command)
            event.stop_event()
            return

        self.disguise.apply_reply(event, text)

    @filter.regex(r"^(\d+)[xX]\s+(.*)")
    async def multi_execute(self, event: AstrMessageEvent):
        if not is_allowed(event, self.multiply_allowed_users):
            yield event.plain_result("抱歉，你没有权限使用该指令。")
            return

        match = re.search(r"^(\d+)[xX]\s+(.*)", event.message_str)
        if not match:
            return

        times_str = match.group(1)
        command = match.group(2).strip()

        try:
            times = int(times_str)
        except ValueError:
            yield event.plain_result(f"次数解析失败: {times_str}")
            return

        if times <= 0:
            yield event.plain_result("执行次数必须大于 0")
            return

        if times > self.max_times:
            yield event.plain_result(f"执行次数过多，最高支持 {self.max_times} 次")
            return

        if not command:
            yield event.plain_result("请输入要执行的指令")
            return

        requested_command = command
        if self.case_insensitive.is_enabled_for(event):
            case_command = self.case_insensitive.rewrite(
                command,
                self.no_wake.get_wake_prefixes(),
            )
            if case_command:
                command = case_command

        alias_target = self.alias.check_alias(command, self.no_wake.get_wake_prefixes())
        alias_applied = alias_target is not None
        if alias_applied:
            command = alias_target

        command, added_prefix = ensure_wake_prefix(
            command,
            self.no_wake.get_wake_prefixes(),
        )
        if added_prefix:
            logger.debug(
                "[指令模拟器] 自动添加前缀 %s，新指令: %s",
                added_prefix,
                command,
            )

        wake_prefixes = self.no_wake.get_wake_prefixes()
        command_names = (
            self.command_access.command_names_from_text(
                requested_command,
                wake_prefixes,
            )
            | self.command_access.command_names_from_text(command, wake_prefixes)
        )
        if self._stop_if_command_blocked(
            event,
            text=command,
            command_names=command_names,
            wake_prefixes=wake_prefixes,
        ):
            return

        prefix_len = match.start(2)
        new_chain = []
        chars_to_skip = prefix_len

        for comp in event.get_messages():
            if chars_to_skip > 0:
                if isinstance(comp, Plain):
                    text_len = len(comp.text)
                    if text_len <= chars_to_skip:
                        chars_to_skip -= text_len
                        continue
                    else:
                        rest = comp.text[chars_to_skip:]
                        chars_to_skip = 0
                        if rest:
                            new_chain.append(Plain(rest))
            else:
                if isinstance(comp, (Plain, At)):
                    new_chain.append(comp)

        if added_prefix:
            if new_chain and isinstance(new_chain[0], Plain):
                new_chain[0].text = added_prefix + new_chain[0].text
            else:
                new_chain.insert(0, Plain(added_prefix))

        if alias_applied:
            new_chain = replace_first_plain_text(new_chain, command)

        logger.info(
            "[指令模拟器] 开始连续执行 %s 次指令: %s，间隔 %s 秒",
            times,
            command,
            self.interval,
        )

        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        is_admin = event.is_admin()
        self_id = event.get_self_id()

        for i in range(times):
            logger.debug(
                "[指令模拟器] 第 %s/%s 次执行: %s",
                i + 1,
                times,
                command,
            )

            new_event = self.event_factory.create_event(
                unified_msg_origin=event.unified_msg_origin,
                command=command,
                creator_id=sender_id,
                creator_name=sender_name,
                original_components=new_chain,
                is_admin=is_admin,
                self_id=self_id,
                source_message_id=getattr(
                    getattr(event, "message_obj", None), "message_id", None
                ),
            )
            self.disguise.apply_reply(new_event, command)
            new_event.set_extra(COMMAND_ACCESS_CHECKED_EXTRA_KEY, True)
            if alias_applied:
                new_event.set_extra(DISGUISE_ALIAS_EXTRA_KEY, True)

            self.context.get_event_queue().put_nowait(new_event)

            if i < times - 1:
                await asyncio.sleep(self.interval)

        yield event.stop_event()

    async def terminate(self):
        pass

    @filter.command("模拟")
    async def simulate_command(self, event: AstrMessageEvent):
        if not is_allowed(event, self.simulate_allowed_users):
            yield event.plain_result("抱歉，你没有权限使用该指令。")
            event.stop_event()
            return

        target_user_id, target_user_name = extract_at_user(event)
        if not target_user_id:
            yield event.plain_result("请先艾特要模拟的用户。用法：模拟 @用户 指令")
            event.stop_event()
            return

        new_chain = extract_after_target_at(event, target_user_id)

        command = ""
        for comp in new_chain:
            if isinstance(comp, Plain):
                command += comp.text
            elif isinstance(comp, At):
                at_name = getattr(comp, "name", "") or f"用户{comp.qq}"
                command += f" @{at_name}({comp.qq})"

        command = command.strip()

        if not command:
            yield event.plain_result("请输入要执行的指令")
            event.stop_event()
            return

        if self.case_insensitive.is_enabled_for(event):
            case_command = self.case_insensitive.rewrite(
                command,
                self.no_wake.get_wake_prefixes(),
            )
            if case_command:
                command = case_command

        alias_target = self.alias.check_alias(command, self.no_wake.get_wake_prefixes())
        alias_applied = alias_target is not None
        if alias_applied:
            command = alias_target

        command, added_prefix = ensure_wake_prefix(
            command,
            self.no_wake.get_wake_prefixes(),
        )
        if added_prefix:
            logger.debug(
                "[指令模拟器] 自动添加前缀 %s，新指令: %s",
                added_prefix,
                command,
            )
            if new_chain and isinstance(new_chain[0], Plain):
                new_chain[0].text = added_prefix + new_chain[0].text
            else:
                new_chain.insert(0, Plain(added_prefix))

        if alias_applied:
            new_chain = replace_first_plain_text(new_chain, command)

        final_user_name = (
            target_user_name if target_user_name else f"用户{target_user_id}"
        )

        logger.info(
            "[指令模拟器] 开始模拟用户 %s 执行指令: %s",
            final_user_name,
            command,
        )

        event.stop_event()

        target_is_admin = is_user_admin(self.context, target_user_id)

        self_id = event.get_self_id()

        new_event = self.event_factory.create_event(
            unified_msg_origin=event.unified_msg_origin,
            command=command,
            creator_id=target_user_id,
            creator_name=final_user_name,
            original_components=new_chain,
            is_admin=target_is_admin,
            self_id=self_id,
            sender_id=target_user_id,
            sender_name=final_user_name,
            source_message_id=getattr(
                getattr(event, "message_obj", None), "message_id", None
            ),
        )
        self.disguise.apply_reply(new_event, command)
        new_event.set_extra(COMMAND_ACCESS_CHECKED_EXTRA_KEY, True)
        if alias_applied:
            new_event.set_extra(DISGUISE_ALIAS_EXTRA_KEY, True)

        self.context.get_event_queue().put_nowait(new_event)
