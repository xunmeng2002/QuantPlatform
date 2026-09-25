"""宿主输出的落盘与尾部截断.

全量文件与详情页尾部**口径不同**, 两条各有一组断言:

- 全量文件按**原始字节**写, 故"文件字节数 == 进程写出的字节数"是精确可判的 (若先解码成文本再写,
  中文会按替换字符落盘, 字节数也随编码变化). 任何编码都能事后重新解释, 前提是字节还在.
- 尾部是"最近 N 字节", 起点必然落在任意位置——**包括多字节字符中间**. 不对齐就凭空多出一个
  U+FFFD, 它看着像"引擎输出了乱码", 而原始输出完全正常, 排查方向一开始就是错的.

尾部在内存里**有界**, 这一条只有从内存侧才观测得到: 取值时总会切片, 故"忘了裁剪"在返回值上看不出
任何差别, 只有常驻内存会随策略的输出量一起涨——等于让被执行的代码决定后端的常驻内存.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

from app.scheduler.output import (
    OUTPUT_READ_CHUNK_BYTES,
    JobOutputCapture,
    OutputStreamTail,
    decode_tail,
)


MAXIMUM_TAIL_BYTES = 8192
MULTIBYTE_TEXT = "中文测试"

STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"

# 一个多字节字符完全落在尾部窗口之外、只有后半截留在窗口内的情形: "中文测试" 共 12 字节, 取最后
# 5 字节时起点落在"测"的中间.
TAIL_SLICE_BYTES = 5
EXPECTED_ALIGNED_TAIL = "试"


def test_the_tail_starts_on_a_character_boundary() -> None:
    """按字节截出的尾部对齐到 UTF-8 首字节后再解码, 不产生替换字符."""

    raw_slice = MULTIBYTE_TEXT.encode("utf-8")[-TAIL_SLICE_BYTES:]

    assert decode_tail(raw_slice) == EXPECTED_ALIGNED_TAIL
    assert "�" not in decode_tail(raw_slice)


def test_the_same_bytes_without_alignment_produce_a_replacement_character() -> None:
    """不推起点会多出一个 U+FFFD——这正是要防的那个症状.

    这条断言是上一条的对照面: 少了它, "对齐"这件事看起来只是几行防御性的循环, 而它实际换来的是
    "引擎日志里凭空出现的乱码".
    """

    raw_slice = MULTIBYTE_TEXT.encode("utf-8")[-TAIL_SLICE_BYTES:]

    assert "�" in raw_slice.decode("utf-8", errors="replace")


def test_the_whole_output_is_written_byte_exactly(tmp_path: Path) -> None:
    """全量文件按原始字节写: 文件字节数**精确等于**喂进去的字节数."""

    stdout_path = tmp_path / STDOUT_FILENAME
    chunks = [MULTIBYTE_TEXT.encode("utf-8")] * 7

    with JobOutputCapture(stdout_path, tmp_path / STDERR_FILENAME, MAXIMUM_TAIL_BYTES) as capture:
        for chunk in chunks:
            capture.append_stdout(chunk)

    written_bytes = stdout_path.read_bytes()

    assert written_bytes == b"".join(chunks)
    assert len(written_bytes) == sum(len(chunk) for chunk in chunks)


def test_the_two_streams_do_not_mix(tmp_path: Path) -> None:
    """两条流各落各的文件、各留各的尾部."""

    stdout_path = tmp_path / STDOUT_FILENAME
    stderr_path = tmp_path / STDERR_FILENAME

    with JobOutputCapture(stdout_path, stderr_path, MAXIMUM_TAIL_BYTES) as capture:
        capture.append_stdout(b"standard output\n")
        capture.append_stderr(b"standard error\n")

        assert capture.stdout_tail == "standard output\n"
        assert capture.stderr_tail == "standard error\n"

    assert stdout_path.read_bytes() == b"standard output\n"
    assert stderr_path.read_bytes() == b"standard error\n"


def test_the_tail_is_a_true_suffix_of_the_written_file(tmp_path: Path) -> None:
    """尾部是文件内容的真后缀, 且 UTF-8 字节数不超过配置的上限.

    中文行让尾部窗口的起点必然落在字符中间 (每行 11 字节, 8192 不是它的整数倍), 故"是真后缀"这条
    断言同时也在盯对齐——对齐没做的话, 尾部开头那个替换字符会让它不再是文件的后缀.
    """

    line_count = 4096
    stdout_path = tmp_path / STDOUT_FILENAME

    with JobOutputCapture(
        stdout_path, tmp_path / STDERR_FILENAME, MAXIMUM_TAIL_BYTES
    ) as capture:
        for line_number in range(line_count):
            capture.append_stdout(f"第 {line_number} 行\n".encode("utf-8"))

        tail = capture.stdout_tail

    written_bytes = stdout_path.read_bytes()

    assert written_bytes.endswith(tail.encode("utf-8"))
    assert len(tail.encode("utf-8")) <= MAXIMUM_TAIL_BYTES
    assert "�" not in tail


def test_the_retained_bytes_stay_bounded_when_the_output_floods(tmp_path: Path) -> None:
    """被灌 8 MB 时, 常驻内存只有"上限 + 一个读取块"的规模.

    只有从内存侧才判得出来: 返回值总会切片, 故"忘了裁剪"在尾部文本上看不出任何差别. 阈值取灌入量
    的八分之一, 与裁剪后的真实占用 (约 72 KB) 差着两个数量级, 不会因解释器开销而误判.
    """

    flood_bytes = 8 * 1024 * 1024
    maximum_retained_bytes = flood_bytes // 8
    chunk = b"x" * OUTPUT_READ_CHUNK_BYTES

    tracemalloc.start()

    try:
        with JobOutputCapture(
            tmp_path / STDOUT_FILENAME, tmp_path / STDERR_FILENAME, MAXIMUM_TAIL_BYTES
        ) as capture:
            for _ in range(flood_bytes // OUTPUT_READ_CHUNK_BYTES):
                capture.append_stdout(chunk)

        peak_retained_bytes = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak_retained_bytes < maximum_retained_bytes


def test_both_files_are_closed_when_the_capture_exits(tmp_path: Path) -> None:
    """退出上下文即释放句柄: Windows 上文件还开着时删不掉, 故"删得掉"就是"句柄已关".

    句柄泄漏在这里不是抽象的风险: 作业体的每条退出路径 (正常、超时、取消) 都要走到这个上下文,
    漏一条就有一份输出文件在进程生命周期里一直开着.
    """

    stdout_path = tmp_path / STDOUT_FILENAME
    stderr_path = tmp_path / STDERR_FILENAME

    with JobOutputCapture(stdout_path, stderr_path, MAXIMUM_TAIL_BYTES):
        pass

    stdout_path.unlink()
    stderr_path.unlink()

    assert not stdout_path.exists()
    assert not stderr_path.exists()


def test_a_stream_tail_keeps_the_last_bytes_of_a_chunked_write(tmp_path: Path) -> None:
    """分块喂养时尾部取的是**全部**已写字节的末尾, 不是最后一块.

    一次读取块的余量若被误当成"只有最后一块算数", 一份分了很多块的长输出就只有最后一小块进尾部,
    而详情页看到的最后几行会缺一大截.
    """

    stdout_path = tmp_path / STDOUT_FILENAME

    with JobOutputCapture(stdout_path, tmp_path / STDERR_FILENAME, MAXIMUM_TAIL_BYTES) as capture:
        capture.append_stdout(b"first line\n")
        capture.append_stdout(b"second line\n")

        assert capture.stdout_tail == "first line\nsecond line\n"


def test_a_stream_tail_can_be_used_on_its_own(tmp_path: Path) -> None:
    """不组作业也能直接用: 单条流的落盘 + 尾部口径与上面一致."""

    stdout_path = tmp_path / STDOUT_FILENAME
    maximum_tail_bytes = 16

    with stdout_path.open("wb") as destination_file:
        stream_tail = OutputStreamTail(destination_file, maximum_tail_bytes)

        for _ in range(10):
            stream_tail.append(b"12345678")

        assert stream_tail.tail == "1234567812345678"

    assert stdout_path.read_bytes() == b"12345678" * 10
