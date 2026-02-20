import asyncio
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, At
from .core.command_trigger import CommandTrigger

@register("astrbot_plugin_multi_execute", "Foolllll", "通过 nx /指令 表示模拟执行连续 n 次 /指令", "1.0")
class MultiExecutePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.whitelist = self.config.get('whitelist', [])
        self.interval = self.config.get('interval', 1)
        self.max_times = self.config.get('max_times', 20)
        self.monitor_timeout = self.config.get('monitor_timeout', 60)
        self.prefix_mode = self.config.get('prefix_mode', True)
        self.show_start_message = self.config.get('show_start_message', True)

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

    def _is_allowed(self, event: AstrMessageEvent):
        """检查用户是否有权限使用该插件"""
        if event.is_admin():
            return True
        if not self.whitelist:
            return True
        return event.get_sender_id() in self.whitelist

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
                        logger.debug(f"MultiExecute: 自动添加前缀 {prefix}, 新指令: {command}")

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
        
        trigger = CommandTrigger(self.context, {"monitor_timeout": self.monitor_timeout})
        
        item = {
            "created_by": event.get_sender_id(),
            "creator_name": event.get_sender_name(),
            "is_admin": event.is_admin(),
            "name": "multi_execute_task"
        }
        
        unified_msg_origin = event.unified_msg_origin
        
        for i in range(times):
            logger.debug(f"MultiExecute: 第 {i+1}/{times} 次执行: {command}")
            self_id = event.get_self_id() if hasattr(event, "get_self_id") else None
            asyncio.create_task(trigger.trigger_and_forward_command(unified_msg_origin, item, command, is_admin=event.is_admin(), original_components=new_chain, self_id=self_id))
            
            if i < times - 1:
                await asyncio.sleep(self.interval)
                
    async def terminate(self):
        """插件销毁"""
        pass
