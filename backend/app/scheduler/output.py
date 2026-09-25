"""宿主输出的落盘与尾部截断.

两条流各一份: 全量写文件 (**原始字节**), 尾部留在内存里供详情页展示. 两者口径不同是刻意的:
排障要看全量, 而文件里按原始字节存, 任何编码都能事后重新解释; 详情页只看最后几 KB, 且必须是
能直接渲染的文本.

内存里的尾部是**有界**的: 策略往 stdout 打一整个循环是常事, 无界缓存等于让被执行的代码决定
后端的常驻内存. 裁剪只在字节层面做, 且永不切进多字节字符——见 `decode_tail`.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import BinaryIO


OUTPUT_READ_CHUNK_BYTES = 64 * 1024


def decode_tail(raw_tail: bytes) -> str:
    """把按字节截出的尾部对齐到 UTF-8 首字节后解码.

    不对齐的话, 起点落在**续字节**上会凭空多出一个 U+FFFD: 它看着像"引擎输出了乱码", 而原始
    输出完全正常, 排查方向一开始就是错的.
    """

    start = 0

    while start < len(raw_tail) and (raw_tail[start] & 0xC0) == 0x80:
        start += 1

    return raw_tail[start:].decode("utf-8", errors="replace")


class OutputStreamTail:
    """一条输出流的落盘与尾部保留."""

    def __init__(self, destination_file: BinaryIO, maximum_tail_bytes: int) -> None:
        self._destination_file = destination_file
        self._maximum_tail_bytes = maximum_tail_bytes
        self._recent_bytes = bytearray()

    def append(self, chunk: bytes) -> None:
        """落盘一个读取块, 并把它并入内存尾部."""

        self._destination_file.write(chunk)
        self._recent_bytes.extend(chunk)

        # 保留最近 maximum_tail_bytes 字节, 另留一个读取块的余量. 余量不是浪费: 没有它, 每次
        # 裁剪都要判断"这一步裁掉的是不是半个字符", 而那个判断在字节层面做不干净——留出余量后
        # 只需要在**取值时**对齐一次首字节, 逻辑只有一处.
        excess_bytes = (
            len(self._recent_bytes) - self._maximum_tail_bytes - OUTPUT_READ_CHUNK_BYTES
        )

        if excess_bytes > 0:
            del self._recent_bytes[:excess_bytes]

    @property
    def tail(self) -> str:
        """最近 maximum_tail_bytes 字节对应的文本."""

        return decode_tail(bytes(self._recent_bytes[-self._maximum_tail_bytes :]))

    def close(self) -> None:
        """关闭落盘文件."""

        self._destination_file.close()


class JobOutputCapture:
    """一个作业的两条输出流.

    整体做上下文管理器: 文件句柄的释放必须是确定性的, 而作业体的任何退出路径 (正常、超时、
    取消) 都要走到这里.
    """

    def __init__(
        self, stdout_path: Path, stderr_path: Path, maximum_tail_bytes: int
    ) -> None:
        self._stdout_stream = OutputStreamTail(
            stdout_path.open("wb"), maximum_tail_bytes
        )
        self._stderr_stream = OutputStreamTail(
            stderr_path.open("wb"), maximum_tail_bytes
        )

    def __enter__(self) -> JobOutputCapture:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stdout_stream.close()
        self._stderr_stream.close()

    @property
    def stdout_tail(self) -> str:
        """标准输出尾部."""

        return self._stdout_stream.tail

    @property
    def stderr_tail(self) -> str:
        """标准错误尾部."""

        return self._stderr_stream.tail

    def append_stdout(self, chunk: bytes) -> None:
        """落盘一段标准输出."""

        self._stdout_stream.append(chunk)

    def append_stderr(self, chunk: bytes) -> None:
        """落盘一段标准错误."""

        self._stderr_stream.append(chunk)
