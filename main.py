import asyncio
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, At
from .core.event_factory import EventFactory


class MultiExecutePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.whitelist = self.config.get('whitelist', [])
        self.interval = self.config.get('interval', 1)
        self.max_times = self.config.get('max_times', 20)
        self.prefix_mode = self.config.get('prefix_mode', True)
        self.show_start_message = self.config.get('show_start_message', True)
        self.all_commands_no_wake = self.config.get('all_commands_no_wake', False)
        self.no_wake_blacklist = set(self.config.get('no_wake_blacklist', []))
        self.no_wake_commands = self._parse_commands()
        self._manual_no_wake_commands = set(self.no_wake_commands)
        self._plugin_no_wake_commands: dict[str, set[str]] = {}
        self.no_wake_whitelist_groups = [str(sid) for sid in self.config.get('no_wake_whitelist_groups', [])]

        # 创建事件工厂
        self.event_factory = EventFactory(context)

    async def initialize(self):
        """插件初始化时先尝试加载一次全量指令。"""
        if self.all_commands_no_wake:
            await self._initialize_all_commands()

    def _extract_enabled_command_names(
        self,
        commands: list[dict],
        plugin_name: str | None = None,
        module_path: str | None = None,
    ) -> set[str]:
        """从 list_commands 的结果中提取启用指令名与别名。"""
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
                cmd,
                activated_modules,
                activated_names,
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

    def _get_activated_plugin_index(self) -> tuple[set[str], set[str]]:
        """获取当前已激活插件的 module_path/name 索引。"""
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
        """判断指令是否属于已激活插件。"""
        cmd_module = command_info.get("module_path")
        cmd_plugin = command_info.get("plugin")

        if cmd_module:
            return cmd_module in activated_modules
        if cmd_plugin:
            return cmd_plugin in activated_names
        return False

    def _plugin_cache_key(
        self,
        plugin_name: str | None = None,
        module_path: str | None = None,
    ) -> str:
        """为插件命令缓存生成稳定 key。"""
        if module_path:
            return f"module:{module_path}"
        if plugin_name:
            return f"name:{plugin_name}"
        return ""

    def _group_enabled_command_names_by_plugin(self, commands: list[dict]) -> dict[str, set[str]]:
        """按插件维度分组提取启用指令名与别名。"""
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

    def _rebuild_no_wake_commands(self):
        """根据手动配置 + 插件动态缓存重建免唤醒指令集合。"""
        merged_commands = set(self._manual_no_wake_commands)
        for cmd_names in self._plugin_no_wake_commands.values():
            merged_commands.update(cmd_names)
        self.no_wake_commands = merged_commands

    def _apply_no_wake_blacklist(self, command_names: set[str]) -> set[str]:
        """应用免唤醒黑名单过滤。"""
        if not self.no_wake_blacklist:
            return command_names

        before_count = len(command_names)
        filtered_commands = command_names - self.no_wake_blacklist
        filtered_count = before_count - len(filtered_commands)
        if filtered_count > 0:
            logger.info(f"[指令模拟器] 黑名单已过滤 {filtered_count} 个指令: {self.no_wake_blacklist}")
        return filtered_commands

    def _get_wake_prefixes(self) -> list[str]:
        """获取系统配置的唤醒前缀"""
        try:
            config = self.context.get_config()
            prefixes = config.get("wake_prefix")
            if not prefixes:
                return []

            if isinstance(prefixes, str):
                return [prefixes]
            return list(prefixes)
        except Exception as e:
            logger.warning(f"获取唤醒前缀失败: {e}")
            return []

    def _parse_commands(self) -> set:
        """解析配置中的免唤醒指令列表，返回指令集合"""
        commands_config = self.config.get("no_wake_commands", [])
        result = set()

        for item in commands_config:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())

        if result:
            logger.info(f"[指令模拟器] 插件已加载，共 {len(result)} 个免唤醒指令: {list(result)}")

        return result

    async def _initialize_all_commands(self):
        """初始化时获取所有已注册的指令，并与现有免唤醒指令取并集"""
        try:
            from astrbot.core.star.command_management import list_commands
            commands = await list_commands()
            plugin_command_map = self._group_enabled_command_names_by_plugin(commands)

            # 全量重建插件缓存，避免旧缓存残留
            rebuilt_map: dict[str, set[str]] = {}
            for cache_key, cmd_names in plugin_command_map.items():
                rebuilt_map[cache_key] = self._apply_no_wake_blacklist(cmd_names)
            self._plugin_no_wake_commands = rebuilt_map
            self._rebuild_no_wake_commands()

            all_cmd_names = set()
            for cmd_names in self._plugin_no_wake_commands.values():
                all_cmd_names.update(cmd_names)

            logger.info(f"[指令模拟器] 全局免唤醒模式已启用，共 {len(all_cmd_names)} 个指令可免唤醒触发")

        except Exception as e:
            logger.error(f"[指令模拟器] 获取所有指令失败: {e}")

    async def _refresh_commands_for_plugin(self, metadata):
        """插件加载时增量刷新对应插件的免唤醒指令。"""
        if not self.all_commands_no_wake:
            return

        plugin_name = getattr(metadata, "name", None)
        module_path = getattr(metadata, "module_path", None)
        if not plugin_name and not module_path:
            return

        try:
            from astrbot.core.star.command_management import list_commands

            commands = await list_commands()
            plugin_cmd_names = self._extract_enabled_command_names(
                commands,
                plugin_name=plugin_name,
                module_path=module_path,
            )
            plugin_cmd_names = self._apply_no_wake_blacklist(plugin_cmd_names)

            cache_key = self._plugin_cache_key(plugin_name=plugin_name, module_path=module_path)
            if not cache_key:
                return

            before_count = len(self.no_wake_commands)
            self._plugin_no_wake_commands[cache_key] = plugin_cmd_names
            self._rebuild_no_wake_commands()
            added_count = len(self.no_wake_commands) - before_count

            if plugin_cmd_names or added_count != 0:
                logger.info(
                    f"[指令模拟器] 插件加载增量刷新完成: {plugin_name or module_path}，新增 {added_count} 个免唤醒指令"
                )
        except Exception as e:
            logger.warning(f"[指令模拟器] 插件加载增量刷新失败: {e}")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot 完成启动后，统一构建全量免唤醒指令。"""
        if self.all_commands_no_wake:
            await self._initialize_all_commands()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata):
        """插件加载后增量刷新免唤醒指令，避免后续重载/安装导致列表过期。"""
        if getattr(metadata, "module_path", None) == self.__class__.__module__:
            return
        await self._refresh_commands_for_plugin(metadata)

    @filter.on_plugin_unloaded()
    async def on_plugin_unloaded(self, metadata):
        """插件卸载/禁用后移除对应免唤醒指令缓存，避免禁用后仍可匹配。"""
        if not self.all_commands_no_wake:
            return

        plugin_name = getattr(metadata, "name", None)
        module_path = getattr(metadata, "module_path", None)
        cache_key = self._plugin_cache_key(plugin_name=plugin_name, module_path=module_path)
        if not cache_key:
            return

        removed = len(self._plugin_no_wake_commands.get(cache_key, set()))
        self._plugin_no_wake_commands.pop(cache_key, None)
        self._rebuild_no_wake_commands()
        if removed > 0:
            logger.info(
                f"[指令模拟器] 插件卸载增量刷新完成: {plugin_name or module_path}，移除 {removed} 个免唤醒指令"
            )

    def _is_allowed(self, event: AstrMessageEvent):
        """检查用户是否有权限使用该插件"""
        if event.is_admin():
            return True
        if not self.whitelist:
            return True
        return event.get_sender_id() in self.whitelist

    def _is_no_wake_trigger_allowed(self, event: AstrMessageEvent):
        """检查免唤醒触发是否允许在当前群组触发"""
        # 如果没有设置白名单，则全部群组都允许
        if not self.no_wake_whitelist_groups:
            return True

        # 获取群组ID
        group_id = event.get_group_id()
        if not group_id:
            # 私聊不触发免唤醒
            return False

        group_id = str(group_id).split('#')[0]
        return group_id in self.no_wake_whitelist_groups

    def _extract_message_components(self, event: AstrMessageEvent) -> list:
        """从原始消息中提取完整消息组件链"""
        components = []

        try:
            if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
                message_chain = event.message_obj.message
            else:
                message_chain = event.get_messages()

            if message_chain:
                for comp in message_chain:
                    components.append(comp)
        except (AttributeError, TypeError) as e:
            logger.warning(f"[指令模拟器] 提取消息组件时出错: {e}")

        return components

    def _build_prefixed_components(self, components: list, prefix: str) -> list:
        """在消息组件链前补充唤醒前缀，保持原始组件顺序"""
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

    def _is_valid_command_match(self, text: str, command: str) -> bool:
        """检查指令匹配是否有效（严格空格分隔）
        规则：
        1. 必须以指令开头
        2. 如果 text 长度等于 command 长度，则是完全匹配，有效
        3. 如果 text 长度大于 command 长度，则 command 后的第一个字符必须是空格
        """
        if not text.startswith(command):
            return False

        # 完全匹配
        if len(text) == len(command):
            return True

        # 严格检查：指令后面必须跟空格
        next_char = text[len(command)]
        if next_char != " ":
            return False

        return True

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，处理指令模拟器"""
        # 跳过由本插件创建的事件
        try:
            if event.get_extra("multi_execute_origin", False):
                return
        except Exception:
            pass

        # 跳过本插件创建的内部命令事件，避免循环
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", "")
        if isinstance(msg_id, str) and msg_id.startswith("command_trigger_"):
            return

        # 检查免唤醒白名单群组
        if not self._is_no_wake_trigger_allowed(event):
            return

        # 获取唤醒前缀
        wake_prefixes = self._get_wake_prefixes()
        raw_msg_str = getattr(getattr(event, "message_obj", None), "message_str", "")
        if isinstance(raw_msg_str, str):
            for prefix in wake_prefixes:
                if raw_msg_str.startswith(prefix):
                    return

        # 检查事件是否已经是指令（避免重复触发）
        if hasattr(event, 'is_at_or_wake_command') and event.is_at_or_wake_command:
            logger.debug(f"[指令模拟器] 跳过已识别为指令的事件: {event.message_str}")
            return

        # 提取纯文本内容
        text = event.message_str.strip() if event.message_str else ""
        if not text:
            return

        # 跳过已经带命令前缀的消息
        if any(text.startswith(prefix) for prefix in wake_prefixes):
            logger.debug(f"[指令模拟器] 跳过命令格式消息: '{text}'")
            return

        # 如果已 @ 机器人或回复机器人，交给正常唤醒流程处理，避免重复触发
        try:
            self_id = str(event.get_self_id())
            for comp in event.get_messages() or []:
                if isinstance(comp, At) and str(comp.qq) == self_id:
                    return
                if hasattr(comp, "sender_id") and str(getattr(comp, "sender_id", "")) == self_id:
                    return
        except Exception:
            pass

        # 检查是否匹配免唤醒指令（前缀匹配，支持指令后带参数）
        matched_command = None
        for command in self.no_wake_commands:
            if self._is_valid_command_match(text, command):
                # 优先选择更长的指令匹配（避免短指令误匹配）
                if matched_command is None or len(command) > len(matched_command):
                    matched_command = command

        if matched_command:
            # 获取指令后面的内容（参数部分）
            suffix = text[len(matched_command):]
            command_prefix = wake_prefixes[0] if wake_prefixes else "/"
            new_command = f"{command_prefix}{matched_command}{suffix}"

            # 提取原始消息组件，并补充唤醒前缀
            original_components = self._extract_message_components(event)
            prefixed_components = self._build_prefixed_components(original_components, command_prefix)

            # 获取原始事件的管理员状态
            is_admin = False
            try:
                is_admin = event.is_admin()
            except (AttributeError, TypeError):
                pass

            logger.info(f"[指令模拟器] 匹配免唤醒指令 '{matched_command}' → 转换为 '{new_command}'")
            if prefixed_components:
                logger.info(f"[指令模拟器] 保留 {len(prefixed_components)} 个原始消息组件")

            try:
                # 获取发送者信息
                sender_id = str(event.get_sender_id())
                sender_name = event.get_sender_name() if hasattr(event, 'get_sender_name') else "用户"

                # 使用 EventFactory 创建新的命令事件
                new_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=new_command,
                    creator_id=sender_id,
                    creator_name=sender_name,
                    original_components=prefixed_components,
                    is_admin=is_admin,
                    self_id=event.get_self_id()
                )

                try:
                    new_event.set_extra("multi_execute_origin", True)
                except Exception:
                    pass

                # 将新事件放入事件队列
                self.context.get_event_queue().put_nowait(new_event)

                logger.info(f"[指令模拟器] 已派发命令事件: {new_command}")

                # 阻止原消息继续传播，避免 LLM 响应
                event.stop_event()

            except Exception as e:
                logger.error(f"[指令模拟器] 派发事件失败: {e}")

    @filter.regex(r"^(\d+)x\s+(.*)")
    async def multi_execute(self, event: AstrMessageEvent):
        """多次执行指令。用法：3x 指令"""
        if not self._is_allowed(event):
            yield event.plain_result("抱歉，你没有权限使用该指令。")
            return

        match = re.search(r"^(\d+)x\s+(.*)", event.message_str)
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

        # 前缀模式关闭：自动补充唤醒前缀
        added_prefix = ""
        if not self.prefix_mode:
            prefixes = self._get_wake_prefixes()
            if prefixes:
                has_prefix = False
                for p in prefixes:
                    if command.startswith(p):
                        has_prefix = True
                        break
                
                if not has_prefix:
                    # 使用第一个前缀
                    prefix = prefixes[0]
                    if prefix: # 确保前缀不为空
                        command = prefix + command
                        added_prefix = prefix
                        logger.debug(f"[指令模拟器] 自动添加前缀 {prefix}, 新指令: {command}")

        # 提取命令中的 At 组件
        # 根据 regex 匹配的位置，跳过前缀 (如 "3x ")
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
                        # 当前组件包含了部分前缀和部分命令
                        rest = comp.text[chars_to_skip:]
                        chars_to_skip = 0
                        if rest:
                            new_chain.append(Plain(rest))
                else:
                    pass
            else:
                # 已经跳过前缀，收集允许的组件
                if isinstance(comp, (Plain, At)):
                    new_chain.append(comp)

        # 如果自动添加了前缀，需要将其添加到消息链的最前面
        if added_prefix:
            if new_chain and isinstance(new_chain[0], Plain):
                new_chain[0].text = added_prefix + new_chain[0].text
            else:
                new_chain.insert(0, Plain(added_prefix))

        start_msg = f"开始连续执行 {times} 次指令: {command}，间隔 {self.interval} 秒"
        logger.info(start_msg)
        if self.show_start_message:
            yield event.plain_result(start_msg)

        # 获取发送者信息
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        is_admin = event.is_admin()
        self_id = event.get_self_id()

        for i in range(times):
            logger.debug(f"[指令模拟器] 第 {i+1}/{times} 次执行: {command}")

            # 创建并派发新事件
            new_event = self.event_factory.create_event(
                unified_msg_origin=event.unified_msg_origin,
                command=command,
                creator_id=sender_id,
                creator_name=sender_name,
                original_components=new_chain,
                is_admin=is_admin,
                self_id=self_id
            )

            # 将新事件放入事件队列
            self.context.get_event_queue().put_nowait(new_event)

            if i < times - 1:
                await asyncio.sleep(self.interval)
        
        yield event.stop_event()
                
    async def terminate(self):
        """插件销毁"""
        pass

    def _extract_at_user(self, event: AstrMessageEvent) -> tuple[str | None, str | None]:
        """从消息链中提取第一个非bot的艾特用户ID和用户名

        Returns:
            (user_id, user_name) 元组，user_name 可能是 None
        """
        messages = event.get_messages()
        self_id = event.get_self_id()
        for comp in messages:
            if isinstance(comp, At):
                # 跳过艾特机器人的情况
                if self_id and str(comp.qq) == str(self_id):
                    continue
                user_id = str(comp.qq)
                user_name = getattr(comp, 'name', None) or None
                return user_id, user_name
        return None, None

    def _extract_after_target_at(self, event: AstrMessageEvent, target_user_id: str) -> list:
        """提取目标用户艾特之后的所有消息组件

        Returns:
            之后的消息组件列表
        """
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

    def _is_user_admin(self, user_id: str) -> bool:
        """检查指定用户是否是管理员

        Args:
            user_id: 用户ID

        Returns:
            是否是管理员（从框架配置中检查）
        """
        try:
            config = self.context.get_config()
            admins_id = config.get('admins_id', [])
            return str(user_id) in [str(admin_id) for admin_id in admins_id]
        except Exception as e:
            logger.warning(f"检查用户管理员权限失败: {e}")
            return False

    @filter.command("模拟")
    async def simulate_command(self, event: AstrMessageEvent):
        """模拟其他用户执行指令。用法：模拟 @用户 指令"""
        if not self._is_allowed(event):
            yield event.plain_result("抱歉，你没有权限使用该指令。")
            event.stop_event()
            return

        # 提取艾特用户ID和名称（跳过bot本身的艾特）
        target_user_id, target_user_name = self._extract_at_user(event)
        if not target_user_id:
            yield event.plain_result("请先艾特要模拟的用户。用法：模拟 @用户 指令")
            event.stop_event()
            return

        # 提取指令（去掉目标用户的艾特部分）
        new_chain = self._extract_after_target_at(event, target_user_id)

        # 从消息链中提取指令文本
        command = ""
        for comp in new_chain:
            if isinstance(comp, Plain):
                command += comp.text
            elif isinstance(comp, At):
                at_name = getattr(comp, 'name', '') or f"用户{comp.qq}"
                command += f" @{at_name}({comp.qq})"

        command = command.strip()

        if not command:
            yield event.plain_result("请输入要执行的指令")
            event.stop_event()
            return

        # 前缀模式关闭：自动补充唤醒前缀
        added_prefix = ""
        if not self.prefix_mode:
            prefixes = self._get_wake_prefixes()
            if prefixes:
                has_prefix = False
                for p in prefixes:
                    if command.startswith(p):
                        has_prefix = True
                        break

                if not has_prefix:
                    prefix = prefixes[0]
                    if prefix:
                        command = prefix + command
                        added_prefix = prefix
                        logger.debug(f"[指令模拟器] 自动添加前缀 {prefix}, 新指令: {command}")
                        if new_chain and isinstance(new_chain[0], Plain):
                            new_chain[0].text = added_prefix + new_chain[0].text
                        else:
                            new_chain.insert(0, Plain(added_prefix))

        # 使用获取到的用户名，如果没有则使用默认值
        final_user_name = target_user_name if target_user_name else f"用户{target_user_id}"

        start_msg = f"开始模拟用户 {final_user_name} 执行指令: {command}"
        logger.info(start_msg)
        if self.show_start_message:
            yield event.plain_result(start_msg)

        # 终止事件传播，防止继续触发 LLM
        event.stop_event()

        # 检查被模拟用户是否是管理员
        target_is_admin = self._is_user_admin(target_user_id)

        # 获取发送者信息
        self_id = event.get_self_id()

        # 创建并派发新事件，使用被模拟用户的信息
        new_event = self.event_factory.create_event(
            unified_msg_origin=event.unified_msg_origin,
            command=command,
            creator_id=target_user_id,
            creator_name=final_user_name,
            original_components=new_chain,
            is_admin=target_is_admin,
            self_id=self_id,
            sender_id=target_user_id,
            sender_name=final_user_name
        )

        # 将新事件放入事件队列
        self.context.get_event_queue().put_nowait(new_event)
