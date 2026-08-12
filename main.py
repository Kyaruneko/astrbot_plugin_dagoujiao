import asyncio
import json
import os
import random
import time

from astrbot import logger
from astrbot.api.all import (
    AstrBotMessage,
    MessageChain,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.message_components import Image, Plain, Record
from astrbot.api.star import Context, Star, register

# 图片 / 音频 / 状态数据目录（本插件目录下的 images/、audios/、data/ 文件夹）
# 音频为真 PCM wav（16bit 单声道 24kHz），发送时 ensure_wav() 会跳过 ffmpeg 转换，
# 后续 silk 编码由纯 Python 的 pysilk 完成，因此服务器上无需安装 ffmpeg。
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audios")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
WATER_STATE_FILE = os.path.join(DATA_DIR, "water_state.json")

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

# 水群默认参数（可通过插件 _conf_schema.json 配置覆盖）
# 测试阶段默认 5~8 分钟一条；正式使用建议改回小时级。
DEFAULT_WATER_MIN_MINUTES = 5  # 两次水群的最小间隔（分钟）
DEFAULT_WATER_MAX_MINUTES = 8  # 两次水群的最大间隔（分钟）
DEFAULT_WATER_PROBABILITY = 1.0  # 到点时实际执行水群的概率
DEFAULT_STEAL_OPEN_PROBABILITY = 0.3  # 水群时改为"偷偷开箱"的概率
RECENT_WINDOW_SECONDS = 24 * 3600  # 未标记目标时，"最近活跃"群的时间窗口


def _img(name: str) -> str:
    return os.path.join(IMAGE_DIR, name)


def _audio(name: str) -> str:
    return os.path.join(AUDIO_DIR, name)


def _build_outcome_chain() -> list:
    """随机生成"叫 / 不叫 / mute / 笑"的消息链组件列表。

    指令回复（on_dagou）与不定时水群共用这一套随机反应。
    """
    outcome = random.choices(
        ["bark", "nobark", "mute", "smile"],
        weights=[45, 45, 5, 5],
    )[0]
    chain = []
    if outcome == "bark":
        # 叫：细分 bb1（普通叫，文字"叫叫叫"）/ bb2（带劲的叫，文字"叫"），附对应语音
        bark_img = random.choices(["bb1.png", "bb2.jpg"], weights=[70, 30])[0]
        if bark_img == "bb1.png":
            text, audio = "叫叫叫", "bb1.wav"
        else:
            text, audio = "叫", "bb2.wav"
        chain.append(Image.fromFileSystem(_img(bark_img)))
        chain.append(Plain(text))
        chain.append(Record.fromFileSystem(_audio(audio)))
    elif outcome == "nobark":
        # 不叫：随机 nobb1（附语音）/ nobb2（无语音），并说"不叫"
        nobb_img = random.choice(["nobb1.png", "nobb2.png"])
        chain.append(Image.fromFileSystem(_img(nobb_img)))
        chain.append(Plain("不叫"))
        if nobb_img == "nobb1.png":
            chain.append(Record.fromFileSystem(_audio("nobb1.wav")))
    elif outcome == "mute":
        # mute：只发图片，发完就结束，无下文
        chain.append(Image.fromFileSystem(_img("mute.jpg")))
    else:
        # smile：发图片，然后说"笑"
        chain.append(Image.fromFileSystem(_img("smile.png")))
        chain.append(Plain("笑"))
    return chain


@register("astrbot_plugin_dagoujiao", "Kyaruneko", "大狗大狗请叫叫", "1.10.2")
class DagoujiaoPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._water_task: asyncio.Task | None = None
        # 已见过的群会话的 PlatformMetadata，用于构造偷偷开箱用的合成事件
        self._platform_meta: dict[str, PlatformMetadata] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._water_data = self._load_water_data()

    async def initialize(self):
        """实例化后启动不定时水群后台任务。"""
        if self._cfg("water_enabled", True):
            self._water_task = asyncio.create_task(self._water_loop())

    async def terminate(self):
        """插件被卸载/停用时取消水群任务。"""
        if self._water_task:
            self._water_task.cancel()
            try:
                await self._water_task
            except (asyncio.CancelledError, Exception):
                pass
            self._water_task = None

    # ===== 配置读取 =====
    def _cfg(self, key: str, default):
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    # ===== 群会话记录（水群目标） =====
    def _load_water_data(self) -> dict:
        try:
            with open(WATER_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"groups": {}}

    def _save_water_data(self):
        try:
            with open(WATER_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._water_data, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("保存水群状态失败")

    def _remember_group(self, session_str: str, platform_id: str, session_id: str):
        """记录一个群会话。仅在第一次见到或间隔 5 分钟以上时才落盘，避免频繁写文件。"""
        groups = self._water_data.setdefault("groups", {})
        g = groups.get(session_str)
        now = time.time()
        if g is None:
            g = {
                "session_str": session_str,
                "platform_id": platform_id,
                "session_id": session_id,
                "marked": False,
                "last_seen": 0.0,
            }
            groups[session_str] = g
            self._save_water_data()
        else:
            g["last_seen"] = now
            if now - g.get("_last_saved", 0) > 300:
                g["_last_saved"] = now
                self._save_water_data()

    def _set_marked(self, session_str: str, marked: bool) -> bool:
        """标记/取消标记一个水群目标。"""
        groups = self._water_data.setdefault("groups", {})
        g = groups.get(session_str)
        if g is None:
            return False
        g["marked"] = marked
        self._save_water_data()
        return True

    def _water_targets(self) -> list[str]:
        """选择水群目标：优先用户标记过的群；否则用最近活跃的群。"""
        groups = self._water_data.get("groups", {})
        if not groups:
            return []
        marked = [g["session_str"] for g in groups.values() if g.get("marked")]
        if marked:
            return marked
        now = time.time()
        recent = [
            g["session_str"]
            for g in groups.values()
            if now - g.get("last_seen", 0) < RECENT_WINDOW_SECONDS
        ]
        return recent or list(groups.keys())

    # ===== 记录所有群消息（用于水群目标 + 缓存平台元信息） =====
    # 注意：regex 装饰器在最内层，这样 priority 才会生效（get_handler_or_create 只在创建时应用 kwargs）。
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    @filter.regex(r".*", priority=100)
    async def on_record(self, event: AstrMessageEvent):
        """记录每一个群会话，不产生任何回复。"""
        try:
            self._platform_meta[event.get_platform_id()] = event.platform_meta
            self._remember_group(
                event.unified_msg_origin,
                event.get_platform_id(),
                event.get_session_id(),
            )
        except Exception:
            logger.exception("记录群会话失败")

    # ===== 水群指令：记住本群 / 别水本群 =====
    @filter.regex(r"^大狗记住本群")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_remember_group(self, event: AstrMessageEvent):
        if self._set_marked(event.unified_msg_origin, True):
            yield event.plain_result("好嘞，大狗记住这个群了，以后会不定时来这里水群～")
        else:
            yield event.plain_result("大狗还没在这个群收到过消息，先随便说句话试试？")
        event.stop_event()

    @filter.regex(r"^大狗别水本群")
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_unremember_group(self, event: AstrMessageEvent):
        if self._set_marked(event.unified_msg_origin, False):
            yield event.plain_result("收到，大狗不在这个群水群了。")
        else:
            yield event.plain_result("大狗本来也没在这个群水群。")
        event.stop_event()

    # ===== 行为一：消息以"大狗"开头，本插件直接随机反应 =====
    # 完全不依赖 @ / 唤醒词，也不调用 AI Agent / 大模型，因此不会有任何模型人格插嘴回复，
    # 也不会产生 token 消耗。末尾的 stop_event() 会终止事件传播，确保后续 AI 链路彻底不介入。
    @filter.regex(r"^大狗")
    async def on_dagou(self, event: AstrMessageEvent):
        chain = _build_outcome_chain()
        if chain:
            yield event.chain_result(chain)

        # 终止事件传播：确保 AI Agent 不会介入，也就不会出现任何模型人格的回复。
        event.stop_event()

    # ===== 行为二：兜底处理器 =====
    # 接收所有消息，但只在"没有任何其他插件 / Handler 处理"时才出场。
    # priority 取一个很低的值，保证排在所有其他 handler 之后执行；此时若
    # event._has_send_oper 仍为 False，说明前面的插件都没有回复这条消息，
    # 就由大狗来"回应"：一般情况一定回 nobb2 图片（大狗不叫/不说话的图），
    # 有时会接着递一张纸条，上面写着使用指南。
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

    # ===== 不定时水群 =====
    async def _water_loop(self):
        while True:
            try:
                interval_minutes = random.uniform(
                    self._cfg("water_interval_min_minutes", DEFAULT_WATER_MIN_MINUTES),
                    self._cfg("water_interval_max_minutes", DEFAULT_WATER_MAX_MINUTES),
                )
                await asyncio.sleep(interval_minutes * 60)
                if (
                    random.random()
                    >= self._cfg("water_probability", DEFAULT_WATER_PROBABILITY)
                ):
                    continue
                await self._do_water_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("水群任务异常，稍后继续")

    async def _do_water_once(self):
        targets = self._water_targets()
        if not targets:
            logger.warning("没有可用的水群目标群，跳过本次水群")
            return
        session_str = random.choice(targets)
        # 有时大狗会偷偷开箱（需要 CS 开箱插件已加载）
        if self._cfg("steal_open_enabled", True) and random.random() < self._cfg(
            "steal_open_probability", DEFAULT_STEAL_OPEN_PROBABILITY
        ):
            if await self._do_steal_open(session_str):
                return
            logger.info("偷偷开箱失败，退回普通叫/不叫")
        await self._send_chain(session_str, _build_outcome_chain())

    async def _send_chain(self, session_str: str, components: list):
        """主动向指定群会话发送一条消息链。"""
        try:
            platform_id, _, session_id = session_str.split(":", 2)
            platform = self.context.get_platform_inst(platform_id)
            if platform is not None and hasattr(platform, "remember_session_scene"):
                # qq_official 主动群推送要求该会话 scene 为 "group"（否则视为无 msg_id 的越权发送被跳过）。
                # scene 是内存缓存，AstrBot 重启后会清空，主动发送前先补记一次，保证重启后也能水群。
                platform.remember_session_scene(session_id, "group")
            ok = await self.context.send_message(
                session_str,
                MessageChain(chain=components),
            )
            if not ok:
                logger.warning(f"水群发送失败或未找到平台: {session_str}")
        except Exception:
            logger.exception("水群发送异常")

    async def _do_steal_open(self, session_str: str) -> bool:
        """调用 CS 开箱插件，让大狗自己偷偷开一箱。返回是否成功完成。"""
        try:
            md = self.context.get_registered_star("CS武器箱开箱模拟")
            if md is None or getattr(md, "star_cls", None) is None:
                logger.info("CS 开箱插件未加载，跳过偷偷开箱")
                return False
            case_plugin = md.star_cls

            box = (self._cfg("steal_open_box", "") or "").strip()
            if not box:
                candidates = list(getattr(case_plugin, "case_data", None) or {})
                if not candidates:
                    logger.info("CS 开箱插件没有可用箱子数据，跳过偷偷开箱")
                    return False
                box = random.choice(candidates)

            ev = self._make_synthetic_event(session_str, f"开箱 1 {box}")
            sent_any = False
            async for result in case_plugin._handle_open(ev):
                if result is None:
                    continue
                chain = getattr(result, "chain", None)
                if not chain:
                    continue
                await self._send_chain(session_str, list(chain))
                sent_any = True
            return sent_any
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("偷偷开箱失败")
            return False

    def _make_synthetic_event(self, session_str: str, text: str) -> AstrMessageEvent:
        """为偷偷开箱构造一个最小化的群消息事件。"""
        platform_id, _, session_id = session_str.split(":", 2)
        platform_meta = self._platform_meta.get(platform_id)
        if platform_meta is None:
            platform_meta = PlatformMetadata(
                name=platform_id,
                description="",
                id=platform_id,
            )
        msg_obj = AstrBotMessage()
        msg_obj.type = MessageType.GROUP_MESSAGE
        msg_obj.group_id = session_id
        msg_obj.sender = MessageMember(user_id="dagou_self", nickname="大狗")
        msg_obj.message = [Plain(text)]
        msg_obj.message_str = text
        return AstrMessageEvent(text, msg_obj, platform_meta, session_id)
