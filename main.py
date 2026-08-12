import os
import random

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star, register

# 图片 / 音频资源目录（本插件目录下的 images/ 与 audios/ 文件夹）
# 音频为真 PCM wav（16bit 单声道 24kHz），发送时 ensure_wav() 会跳过 ffmpeg 转换，
# 后续 silk 编码由纯 Python 的 pysilk 完成，因此服务器上无需安装 ffmpeg。
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audios")


def _img(name: str) -> str:
    return os.path.join(IMAGE_DIR, name)


def _audio(name: str) -> str:
    return os.path.join(AUDIO_DIR, name)


@register("astrbot_plugin_dagoujiao", "Kyaruneko", "大狗大狗请叫叫", "1.7.0")
class DagoujiaoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册为 Agent 可调用的 LLM 工具。由大模型根据对话语境自行判断是否触发，
    # 不再依赖 @ 或关键词正则，避免误触/漏触。调用后会直接向会话发送图片/文字/语音。
    @filter.llm_tool(name="dagou_jiao")
    async def dagou_jiao(self, event: AstrMessageEvent):
        """当用户在对话中提及"大狗"这个梗、让大狗叫/不叫、或者语境适合让"大狗"这个角色回应时，调用本工具让大狗随机做出反应。

        大狗的反应是概率性的，共四种：
        - 叫（45%）：发送"叫"的图片 + 文字 + 狗叫声语音
        - 不叫（45%）：发送"不叫"的图片 + 文字"不叫"（部分情况附语音）
        - 沉默（5%）：只发送一张"沉默"的图片，什么都不说
        - 笑（5%）：发送"笑"的图片 + 文字"笑"

        图片 / 文字 / 语音会分别发送给用户。调用本工具即可，无需再向用户确认。
        """
        outcome = random.choices(
            ["bark", "nobark", "mute", "smile"],
            weights=[45, 45, 5, 5],
        )[0]

        if outcome == "bark":
            # 叫：细分 bb1（普通叫，文字"叫叫叫"）/ bb2（带劲的叫，文字"叫"），附对应语音
            bark_img = random.choices(["bb1.png", "bb2.jpg"], weights=[70, 30])[0]
            if bark_img == "bb1.png":
                text, audio, summary = "叫叫叫", "bb1.wav", "大狗叫了（普通地叫）"
            else:
                text, audio, summary = "叫", "bb2.wav", "大狗非常带劲地叫了"
            yield event.image_result(_img(bark_img))  # 图片
            yield event.plain_result(text)  # 文字
            yield event.chain_result(  # 语音
                [Record.fromFileSystem(_audio(audio))],
            )
            yield summary
        elif outcome == "nobark":
            # 不叫：随机 nobb1（附语音）/ nobb2（无语音），并说"不叫"
            nobb_img = random.choice(["nobb1.png", "nobb2.png"])
            yield event.image_result(_img(nobb_img))  # 图片
            yield event.plain_result("不叫")  # 文字
            if nobb_img == "nobb1.png":
                yield event.chain_result(  # 语音（nobb1 才有）
                    [Record.fromFileSystem(_audio("nobb1.wav"))],
                )
            yield "大狗不叫了"
        elif outcome == "mute":
            # mute：只发图片，发完就结束，无下文
            yield event.image_result(_img("mute.jpg"))
            yield "大狗沉默了，什么都没有说"
        else:
            # smile：发图片，然后说"笑"
            yield event.image_result(_img("smile.png"))
            yield event.plain_result("笑")
            yield "大狗笑了"

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
