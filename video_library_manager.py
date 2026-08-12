# -*- coding: utf-8 -*-
"""大狗音乐视频库管理工具（本地小应用）

用法：
1. 安装依赖：pip install imageio-ffmpeg   （tkinter 为 Python 自带，无需额外安装）
2. 运行：python video_library_manager.py

功能：
- 粘贴 B 站视频链接（支持 /video/BV... 、av 号、b23.tv 短链），自动抓取视频封面
- 可选选择本地 MP3 作为试听音频，自动转成 QQ 语音可发送的真 PCM wav
  （16bit / 单声道 / 24kHz，与插件 audios/ 里现有语音一致，服务器无需 ffmpeg）
- 数据写入脚本同目录下的 video_library/ ：
    video_library/library.json   视频库索引
    video_library/covers/        封面图
    video_library/audios/        试听 wav
- 之后把 video_library/ 下新增文件提交到 git、服务器 git pull 后，
  在群里对机器人说「大狗 更新视频库」即可生效；说「大狗 音乐」随机抽一条发送。

注：B 站 web 接口偶发风控（返回 -412），遇到时稍后重试即可。
"""

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(BASE_DIR, "video_library")
COVERS_DIR = os.path.join(LIB_DIR, "covers")
AUDIOS_DIR = os.path.join(LIB_DIR, "audios")
LIB_FILE = os.path.join(LIB_DIR, "library.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}


# ===== 数据读写 =====
def load_library() -> dict:
    if not os.path.isfile(LIB_FILE):
        return {"videos": []}
    try:
        with open(LIB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
            return {"videos": []}
        return data
    except (OSError, ValueError):
        return {"videos": []}


def save_library(lib: dict):
    os.makedirs(LIB_DIR, exist_ok=True)
    with open(LIB_FILE, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)


# ===== B 站解析 =====
def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _final_url(url: str) -> str:
    """跟随重定向（用于 b23.tv 短链），返回最终 URL。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.geturl()


def extract_video_id(url: str) -> str:
    """从链接中提取 bvid（BV 开头）或 av 号。"""
    m = re.search(r"BV[0-9A-Za-z]{10}", url)
    if m:
        return m.group(0)
    m = re.search(r"av(\d+)", url, re.IGNORECASE)
    if m:
        return "av" + m.group(1)
    raise ValueError("无法从链接中识别 B 站视频（需要 BV 号 / av 号 / b23.tv 短链）")


def fetch_view(video_id: str) -> dict:
    """调用 B 站 web 接口，返回包含 bvid/title/pic 的 data。"""
    if video_id.startswith("av"):
        api = f"https://api.bilibili.com/x/web-interface/view?aid={video_id[2:]}"
    else:
        api = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
    raw = _http_get(api, timeout=15)
    data = json.loads(raw.decode("utf-8"))
    if data.get("code") != 0:
        raise ValueError(f"B 站接口返回错误：{data.get('message')}")
    info = data.get("data") or {}
    if not info.get("pic"):
        raise ValueError("B 站接口返回的封面为空，可能是风控，请稍后重试")
    return info


def resolve(url: str) -> dict:
    """解析链接 → {bvid, title, url, pic}。"""
    if "b23.tv" in url:
        url = _final_url(url)
    video_id = extract_video_id(url)
    info = fetch_view(video_id)
    bvid = info.get("bvid") or video_id
    return {
        "bvid": bvid,
        "title": info.get("title") or bvid,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "pic": info["pic"],
    }


def _cover_ext(pic_url: str) -> str:
    path = urllib.parse.urlparse(pic_url).path
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return ".jpg"
    return ext


def download_cover(pic_url: str, dest: str):
    with open(dest, "wb") as f:
        f.write(_http_get(pic_url, timeout=30))


def mp3_to_wav(mp3_path: str, wav_path: str):
    """用 imageio-ffmpeg 自带的静态 ffmpeg 把 MP3 转成真 PCM wav。"""
    try:
        import imageio_ffmpeg
    except ImportError:
        raise RuntimeError("缺少 imageio-ffmpeg，请先执行：pip install imageio-ffmpeg")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        mp3_path,
        "-ac", "1",
        "-ar", "24000",
        "-sample_fmt", "s16",
        wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ===== 图形界面 =====
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("大狗音乐视频库管理")
        self.root.geometry("680x520")
        self.library = load_library()
        self.audio_path = None

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="B站视频链接：").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.url_var, width=62).grid(
            row=0, column=1, columnspan=2, sticky="we"
        )

        ttk.Button(frm, text="选择试听音频(可选)", command=self.pick_audio).grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.audio_var = tk.StringVar(value="未选择试听音频")
        ttk.Label(frm, textvariable=self.audio_var, foreground="#555").grid(
            row=1, column=1, columnspan=2, sticky="w"
        )

        ttk.Button(frm, text="解析并添加", command=self.add_video).grid(
            row=2, column=0, columnspan=3, pady=8
        )

        self.tree = ttk.Treeview(
            frm, columns=("title", "bvid", "audio"), show="headings", height=16
        )
        self.tree.heading("title", text="标题")
        self.tree.heading("bvid", text="BV号")
        self.tree.heading("audio", text="试听")
        self.tree.column("title", width=360)
        self.tree.column("bvid", width=150)
        self.tree.column("audio", width=60, anchor="center")
        self.tree.grid(row=3, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(3, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Button(frm, text="删除选中", command=self.delete_video).grid(
            row=4, column=0, pady=6, sticky="w"
        )
        ttk.Button(frm, text="刷新", command=self.refresh).grid(
            row=4, column=2, pady=6, sticky="e"
        )

        self.status_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.status_var, foreground="#666").grid(
            row=5, column=0, columnspan=3, sticky="w"
        )

        self.refresh()

    def pick_audio(self):
        path = filedialog.askopenfilename(
            title="选择试听音频（MP3/WAV，可选）",
            filetypes=[("音频文件", "*.mp3 *.wav"), ("所有文件", "*.*")],
        )
        if path:
            self.audio_path = path
            self.audio_var.set("已选择：" + os.path.basename(path))
        else:
            self.audio_path = None
            self.audio_var.set("未选择试听音频")

    def refresh(self):
        self.library = load_library()
        for row in self.tree.get_children():
            self.tree.delete(row)
        for v in self.library.get("videos", []):
            self.tree.insert(
                "",
                "end",
                values=(
                    v.get("title", ""),
                    v.get("bvid", ""),
                    "有" if v.get("audio") else "无",
                ),
            )
        self.status_var.set(
            f"共 {len(self.library.get('videos', []))} 条，存储于 {LIB_DIR}"
        )

    def add_video(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先粘贴 B 站视频链接。")
            return
        try:
            info = resolve(url)
        except Exception as e:
            messagebox.showerror("解析失败", str(e))
            return

        try:
            os.makedirs(COVERS_DIR, exist_ok=True)
            cover_name = info["bvid"] + _cover_ext(info["pic"])
            cover_path = os.path.join(COVERS_DIR, cover_name)
            if not os.path.isfile(cover_path):
                self.status_var.set("正在下载封面…")
                self.root.update_idletasks()
                download_cover(info["pic"], cover_path)

            audio_rel = None
            if self.audio_path:
                os.makedirs(AUDIOS_DIR, exist_ok=True)
                wav_path = os.path.join(AUDIOS_DIR, info["bvid"] + ".wav")
                if os.path.isfile(wav_path):
                    os.remove(wav_path)
                self.status_var.set("正在转换试听音频…")
                self.root.update_idletasks()
                mp3_to_wav(self.audio_path, wav_path)
                audio_rel = os.path.relpath(wav_path, LIB_DIR).replace("\\", "/")
        except Exception as e:
            messagebox.showerror("添加失败", str(e))
            return

        entry = {
            "bvid": info["bvid"],
            "title": info["title"],
            "url": info["url"],
            "cover": os.path.relpath(cover_path, LIB_DIR).replace("\\", "/"),
            "audio": audio_rel,
        }
        videos = self.library.setdefault("videos", [])
        videos[:] = [v for v in videos if v.get("bvid") != info["bvid"]]
        videos.append(entry)
        save_library(self.library)

        self.refresh()
        self.url_var.set("")
        self.audio_path = None
        self.audio_var.set("未选择试听音频")
        self.status_var.set(f"已添加：{info['title']}（{info['bvid']}）")

    def delete_video(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择要删除的视频。")
            return
        idx = self.tree.index(sel[0])
        videos = self.library.get("videos", [])
        if idx >= len(videos):
            return
        v = videos[idx]
        if not messagebox.askyesno(
            "确认删除", f"删除「{v.get('title')}」及其封面/试听文件？"
        ):
            return
        for key in ("cover", "audio"):
            rel = v.get(key)
            if rel:
                p = os.path.join(LIB_DIR, rel)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        videos.pop(idx)
        save_library(self.library)
        self.refresh()
        self.status_var.set(f"已删除：{v.get('title')}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
