import random

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("astrbot_plugin_dagoujiao", "Kyaruneko", "大狗大狗请叫叫", "1.2.0")
class DagoujiaoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册正则过滤器。当用户发送的消息中包含 "大狗" 时触发（不受唤醒词约束）。
    @filter.regex(r"大狗")
    async def dagoujiao(self, event: AstrMessageEvent):
        """大狗叫/不叫：随机回复"叫"或"不叫"，各占一半概率。"""
        reply = random.choice(["叫", "不叫"])
        yield event.plain_result(reply) # 发送一条纯文本消息
        event.stop_event() # 停止事件传播，防止后续再调用 LLM/AI Agent

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
