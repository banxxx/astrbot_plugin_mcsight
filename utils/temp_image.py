"""临时图片文件的统一生命周期：mkstemp 唯一命名 + 发送后删除。

用固定文件名写进程 CWD 会有并发互踩问题：不同群/不同用户可以同时执行同一命令，
后保存的会覆盖先保存的，而 AstrBot 是在发送那一刻才读盘的——先触发的那个人
会拿到后触发者的图（可能含对方服务器的玩家数据），或读到半写文件。

AstrBot 会把 handler yield 出的整条链攒到最后才发，图片在发送那一刻才读盘，
所以带图结果必须用 event.send 直发（await 返回时协议端已受理、字节已读走），
之后才允许删文件。参考 commands/bind_check.py 顶部的说明。
"""
import os
import tempfile


def make_temp_png(prefix: str) -> str:
    """在系统临时目录创建一个唯一的 .png 文件并返回其路径。"""
    fd, path = tempfile.mkstemp(suffix=".png", prefix=prefix)
    os.close(fd)
    return path


def remove_quietly(path: str) -> None:
    """删除临时文件；失败不抛出（发送失败时留着文件反而方便排查）。"""
    try:
        os.remove(path)
    except OSError:
        pass
