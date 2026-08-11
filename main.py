import os
import random

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image, Plain, Record

# 图片 / 音频资源目录（本插件目录下的 images/ 与 audios/ 文件夹）
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audios")


def _img(name: str) -> str:
    return os.path.join(IMAGE_DIR, name)


def _audio(name: str) -> str:
    return os.path.join(AUDIO_DIR, name)


@register("astrbot_plugin_dagoujiao", "Kyaruneko", "大狗大狗请叫叫", "1.4.0")
class DagoujiaoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册正则过滤器。当用户发送的消息中包含 "大狗" 时触发（不受唤醒词约束）。
    @filter.regex(r"大狗")
    async def dagoujiao(self, event: AstrMessageEvent):
        """大狗叫/不叫：按概率随机回复，发送 图片 + 文字（如果有）+ 语音（如果有）。

        概率分配：
        - 叫    45%（细分：bb1 普通叫 70% / bb2 带劲的叫 30%）
        - 不叫  45%（随机 nobb1 / nobb2）
        - mute   5%（只发图片，无下文）
        - smile  5%（图片 + 说"笑"）
        """
        outcome = random.choices(
            ["bark", "nobark", "mute", "smile"],
            weights=[45, 45, 5, 5],
        )[0]

        if outcome == "bark":
            # 叫：细分 bb1（普通叫，文字"叫叫叫"）/ bb2（带劲的叫，文字"叫"），附对应语音
            bark_img = random.choices(["bb1.png", "bb2.jpg"], weights=[70, 30])[0]
            if bark_img == "bb1.png":
                text, audio = "叫叫叫", "bb1.mp3"
            else:
                text, audio = "叫", "bb2.mp3"
            chain = [
                Image.fromFileSystem(_img(bark_img)),
                Plain(text),
                Record.fromFileSystem(_audio(audio)),
            ]
            yield event.chain_result(chain)
        elif outcome == "nobark":
            # 不叫：随机 nobb1（附语音）/ nobb2（无语音），并说"不叫"
            nobb_img = random.choice(["nobb1.png", "nobb2.png"])
            chain = [Image.fromFileSystem(_img(nobb_img)), Plain("不叫")]
            if nobb_img == "nobb1.png":
                chain.append(Record.fromFileSystem(_audio("nobb1.mp3")))
            yield event.chain_result(chain)
        elif outcome == "mute":
            # mute：只发图片，发完就结束，无下文
            yield event.image_result(_img("mute.jpg"))
        else:
            # smile：发图片，然后说"笑"
            yield event.image_result(_img("smile.png")).message("笑")

        event.stop_event()  # 停止事件传播，防止后续再调用 LLM/AI Agent

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
