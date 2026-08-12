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

# 兜底时"大狗递纸条"的概率（"有时会接着一句..."）
NOTE_PROBABILITY = 0.5

# 纸条内容：本插件/相关插件的使用指南
NOTE_TEXT = (
    "@大狗后，输入以下内容\n"
    "今日运势---进行签到求运\n"
    "开箱 数量 XXX箱---进行cs开箱\n"
    "/steam查价 游戏名---游戏价格查询\n"
    "大狗大狗请叫叫---大狗叫\n"
    "更多内容询问炸鱼哥"
)


def _img(name: str) -> str:
    return os.path.join(IMAGE_DIR, name)


def _audio(name: str) -> str:
    return os.path.join(AUDIO_DIR, name)


@register("astrbot_plugin_dagoujiao", "Kyaruneko", "大狗大狗请叫叫", "1.9.2")
class DagoujiaoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # ===== 行为一：消息以"大狗"开头，本插件直接随机反应 =====
    # 完全不依赖 @ / 唤醒词，也不调用 AI Agent / 大模型，因此不会有任何模型人格插嘴回复，
    # 也不会产生 token 消耗。末尾的 stop_event() 会终止事件传播，确保后续 AI 链路彻底不介入。
    @filter.regex(r"^大狗")
    async def on_dagou(self, event: AstrMessageEvent):
        outcome = random.choices(
            ["bark", "nobark", "mute", "smile"],
            weights=[45, 45, 5, 5],
        )[0]

        if outcome == "bark":
            # 叫：细分 bb1（普通叫，文字"叫叫叫"）/ bb2（带劲的叫，文字"叫"），附对应语音
            bark_img = random.choices(["bb1.png", "bb2.jpg"], weights=[70, 30])[0]
            if bark_img == "bb1.png":
                text, audio = "叫叫叫", "bb1.wav"
            else:
                text, audio = "叫", "bb2.wav"
            yield event.image_result(_img(bark_img))  # 图片
            yield event.plain_result(text)  # 文字
            yield event.chain_result(  # 语音
                [Record.fromFileSystem(_audio(audio))],
            )
        elif outcome == "nobark":
            # 不叫：随机 nobb1（附语音）/ nobb2（无语音），并说"不叫"
            nobb_img = random.choice(["nobb1.png", "nobb2.png"])
            yield event.image_result(_img(nobb_img))  # 图片
            yield event.plain_result("不叫")  # 文字
            if nobb_img == "nobb1.png":
                yield event.chain_result(  # 语音（nobb1 才有）
                    [Record.fromFileSystem(_audio("nobb1.wav"))],
                )
        elif outcome == "mute":
            # mute：只发图片，发完就结束，无下文
            yield event.image_result(_img("mute.jpg"))
        else:
            # smile：发图片，然后说"笑"
            yield event.image_result(_img("smile.png"))
            yield event.plain_result("笑")

        # 终止事件传播：确保 AI Agent 不会介入，也就不会出现任何模型人格的回复。
        event.stop_event()

    # ===== 行为二：兜底处理器 =====
    # 接收所有消息，但只在"没有任何其他插件 / Handler 处理"时才出场。
    # priority 取一个很低的值，保证排在所有其他 handler 之后执行；此时若
    # event._has_send_oper 仍为 False，说明前面的插件都没有回复这条消息，
    # 就由大狗来"回应"：一般情况一定回 nobb2 图片（大狗不叫/不说话的图），
    # 有时会接着递一张纸条，上面写着用户这条未被处理的消息内容。
    @filter.regex(r".*", priority=-100)
    async def on_fallback(self, event: AstrMessageEvent):
        if event._has_send_oper:
            # 前面的插件 / Handler 已经处理并发送了内容，不需要大狗出场。
            return

        # 一般情况：一定回复 nobb2 图片
        yield event.image_result(_img("nobb2.png"))

        # 有时会递一张纸条，上面写着使用指南
        if random.random() < NOTE_PROBABILITY:
            yield event.plain_result(f"大狗递过来一张纸条，上面写着：\n{NOTE_TEXT}")

        # 终止事件传播：这条消息已被兜底处理，不再让其他链路介入。
        event.stop_event()

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
