import asyncio
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .core.command_trigger import CommandTrigger

@register("astrbot_plugin_multi_execute", "Foolllll", "通过 /nx /指令 指令表示模拟执行连续 n 次 /指令", "1.0.0")
class MultiExecutePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.interval = self.config.get('interval', 1)
        self.monitor_timeout = self.config.get('monitor_timeout', 60)

    @filter.regex(r"^/(\d+)x\s+(.*)")
    async def multi_execute(self, event: AstrMessageEvent, match: re.Match):
        """多次执行指令。用法：/3x 指令 (内部指令前缀可选)"""
        times = int(match.group(1))
        command = match.group(2).strip()
        
        if times <= 0:
            yield event.plain_result("执行次数必须大于 0")
            return
        
        if times > 20: # 安全限制
            yield event.plain_result("执行次数过多，最高支持 20 次")
            return

        if not command:
            yield event.plain_result("请输入要执行的指令")
            return

        yield event.plain_result(f"开始连续执行 {times} 次指令: {command}，间隔 {self.interval} 秒")
        
        trigger = CommandTrigger(self.context, {"monitor_timeout": self.monitor_timeout})
        
        # 构造 item 信息，供 EventFactory 使用
        item = {
            "created_by": event.get_sender_id(),
            "creator_name": event.get_sender_name(),
            "name": "multi_execute_task"
        }
        
        unified_msg_origin = event.unified_msg_origin
        
        for i in range(times):
            logger.info(f"第 {i+1}/{times} 次执行: {command}")
            # 模拟执行
            asyncio.create_task(trigger.trigger_and_forward_command(unified_msg_origin, item, command))
            
            if i < times - 1:
                await asyncio.sleep(self.interval)
                
    async def terminate(self):
        """插件销毁"""
        pass
