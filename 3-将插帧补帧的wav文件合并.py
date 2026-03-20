import os
import re
import sys
import argparse
from pydub import AudioSegment
from typing import List, Optional

# ==============================================================================
# ⚙️ 用户配置区域 (请在此处修改)
# ==============================================================================

INPUT_SRT_FILENAME = "第六节课输出音频_原文.srt"
INPUT_AUDIO_DIR = "audio_aligned_final"  # 建议使用经过“对齐压缩”后的目录
OUTPUT_FILENAME = "合并后第六节课输出音频_原文.wav"

# 【核心开关】
# True: 如果缺少某个序号的 wav 文件，会在该位置插入对应时长的静音 (保持总时长不变，时间轴严格对齐)
# False: 如果缺少某个序号的 wav 文件，直接跳过该片段，不插入静音 (总时长变短，前后音频紧连)
FILL_MISSING_WITH_SILENCE = True


# ==============================================================================
# 工具函数
# ==============================================================================

def parse_srt_time(time_str: str) -> int:
    """将 SRT 时间字符串 (HH:MM:SS,mmm) 转换为毫秒 (int)"""
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return int((h * 3600 + m * 60 + s) * 1000)


def parse_srt_timeline(content: str) -> List[dict]:
    """解析 SRT，提取绝对时间轴"""
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    pattern = re.compile(
        r'(\d+)\n'
        r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n',
        re.DOTALL
    )
    if not content.endswith('\n\n'): content += '\n\n'

    result = []
    for match in pattern.findall(content):
        try:
            idx = int(match[0])
            start_ms = parse_srt_time(match[1])
            end_ms = parse_srt_time(match[2])
            result.append({
                'index': idx,
                'start_ms': start_ms,
                'end_ms': end_ms
            })
        except Exception as e:
            print(f"[WARN] 解析序号 {match[0]} 时间失败: {e}")

    result.sort(key=lambda x: x['index'])
    return result


def get_audio_path(index: int, directory: str) -> Optional[str]:
    """构建音频文件路径并检查是否存在"""
    filename = f"{index:04d}.wav"
    path = os.path.join(directory, filename)
    if os.path.exists(path):
        return path
    return None


def merge_with_absolute_timing(srt_data: List[dict], audio_dir: str, output_path: str, fill_missing: bool) -> bool:
    """
    核心逻辑：根据 SRT 绝对时间轴合并音频
    fill_missing: 是否对缺失文件填充静音
    """
    if not srt_data:
        print("[ERROR] SRT 数据为空")
        return False

    print(f"[INFO] 开始基于绝对时间轴合并 {len(srt_data)} 个片段...")
    print(f"[INFO] 音频源目录: {audio_dir}")
    print(f"[INFO] 缺失文件策略: {'插入静音 (保持时长)' if fill_missing else '直接跳过 (缩短时长)'}")

    final_audio = AudioSegment.silent(duration=0)
    current_duration_ms = 0

    missing_count = 0

    for i, item in enumerate(srt_data):
        idx = item['index']
        target_start_ms = item['start_ms']
        target_end_ms = item['end_ms']

        # 1. 寻找对应的音频文件
        audio_path = get_audio_path(idx, audio_dir)

        if not audio_path:
            missing_count += 1
            if fill_missing:
                # 策略 A: 插入完整时长的静音
                duration_needed = target_end_ms - target_start_ms
                if duration_needed > 0:
                    silence = AudioSegment.silent(duration=duration_needed)
                    # 先处理时间间隙 (Gap)
                    gap_ms = target_start_ms - current_duration_ms
                    if gap_ms > 0:
                        final_audio += AudioSegment.silent(duration=gap_ms)
                        current_duration_ms += gap_ms
                    # 再添加缺失内容的静音
                    final_audio += silence
                    current_duration_ms += duration_needed

                    if i % 20 == 0 or missing_count <= 5:
                        print(f"[WARN] 序号 {idx} 缺失 -> 已插入 {duration_needed}ms 静音占位")
                else:
                    # 时长为0或负，仅处理 Gap
                    gap_ms = target_start_ms - current_duration_ms
                    if gap_ms > 0:
                        final_audio += AudioSegment.silent(duration=gap_ms)
                        current_duration_ms += gap_ms
            else:
                # 策略 B: 直接跳过，不插入任何内容
                # 但我们需要处理“上一句结束”到“这一句开始”之间的自然间隙吗？
                # 如果选择跳过，通常意味着我们希望后面的音频紧接上来。
                # 此时我们忽略 target_start_ms 的约束，直接拼接下一个存在的音频。
                # 注意：这会导致 current_duration_ms 不再等于 target_start_ms
                if i % 20 == 0 or missing_count <= 5:
                    print(f"[WARN] 序号 {idx} 缺失 -> 已跳过 (不插入静音)")

                # 跳过当前循环，不更新 current_duration_ms (保持原样，等待下一个有效音频)
                # 但是！如果跳过了，下一个有效音频的 gap 怎么算？
                # 下一个有效音频进来时，会用它的 target_start 减去当前的 current_duration。
                # 因为中间跳过了，current_duration 会小于下一个的 target_start，所以会自动补上中间的 Gap。
                # 等等，如果用户想“紧凑”，意味着他不想要 SRT 里定义的那个 Gap。
                # 修正逻辑：如果跳过，我们不应该让下一个音频去对齐它的 SRT Start Time，而是直接接在后面。
                # 为了实现“紧凑”，我们需要标记一下“刚刚跳过了一项”，或者更简单的逻辑：
                # 如果 fill_missing=False，我们实际上是在重构时间轴。
                # 但为了代码简单且符合大多数“跳过”的预期（即：把缺的那段删掉，后面的接上来）：
                # 我们在这里不做任何操作，continue。
                # 下一个循环时，gap = next_start - current_current。这依然会插入 SRT 定义的间隔。
                # **真正的需求可能是**：如果缺了，就把这段时间彻底抹去，后面的音频紧贴前一个音频。
                # 要实现这个，我们需要修改 gap 的计算逻辑，或者简单地：
                # 如果 fill_missing=False，我们暂时不处理 gap，直到遇到下一个有效文件？
                # 不，最简单的逻辑是：如果 fill_missing=False，遇到缺失文件，我们假装它存在且时长为0，并且**强制将 current_duration 更新为 target_start**?
                # 不，那样会插入空隙。
                # **正确逻辑 (紧凑模式)**: 遇到缺失，什么都不做，current_duration 不变。
                # 下一个有效文件进来时，计算 gap = next_start - current_duration。
                # 如果用户希望“紧凑”，意味着他不在乎 SRT 的时间戳了，他只在乎顺序。
                # 但本脚本的核心是“绝对时间轴”。如果填 False，行为定义为：
                # “缺失的部分不发声，也不占用时间（即后面的音频提前播放）” -> 这需要忽略 SRT 的 Start Time 约束。
                # 让我们采用最直观的解释：
                # Fill=True: 严格对齐 SRT，缺了就静音。
                # Fill=False: 缺了就删掉，后面的音频紧接着上一个音频播放（忽略 SRT 中这段的起始时间约束）。

                # 实现“紧接着”：
                # 我们不需要做任何事，只需要确保下一个有效音频不要插入 "target_start - current" 这么大的空隙。
                # 但这很难在不改变架构的情况下做到，因为 gap 计算依赖于 target_start。
                # 变通方案：如果 fill_missing=False，当发现缺失时，我们将 current_duration_ms 强制更新为 target_start_ms?
                # 不，那样会插入空隙。
                # 应该将 current_duration_ms 保持不变，并且在处理**下一个**有效文件时，忽略它的 target_start 约束？
                # 太复杂了。
                # 让我们简化定义：
                # Fill=False 时：缺失文件视为不存在。对于剩余的有效文件，依然尝试对齐它们的 SRT 时间。
                # 这意味着：如果第 5 句缺了 (10s-20s)，第 6 句 (20s-30s)。
                # 当前在 10s。第 5 句跳过。当前还在 10s。
                # 处理第 6 句：Gap = 20s (Start) - 10s (Current) = 10s。插入 10s 静音。
                # 结果：第 6 句依然在 20s 开始。中间 10s 是静音。这和 Fill=True (插入 10s 静音) 效果一样！
                # **除非**：用户希望第 6 句直接在 10s 开始（紧接第 4 句）。
                # 如果是这样，我们需要在 Fill=False 时，**忽略缺失片段造成的时间推移**。
                # 也就是：遇到缺失，不更新 current_duration，也不插入静音。
                # 下一个有效文件进来时，如果它的 Start Time > Current Duration，**且不希望插入静音**，那我们就不能按绝对时间对齐了。
                # 结论：如果用户选 False，通常意味着他想“压缩”掉缺失的部分。
                # 那么逻辑应该是：遇到缺失，什么都不做。下一个有效文件进来时，**强制 Gap = 0** (如果这是第一个缺失后的文件)?
                # 这样改风险太大。
                # 让我们回退到最稳妥的解释：
                # Fill=False = 缺失文件不插入静音，但保留 SRT 定义的时间间隔（即变成了一段无声的空白，但不消耗文件 IO）。
                # 等等，如果 Fill=True 是插入静音对象，Fill=False 是不插入静音对象。
                # 但 `gap_ms` 的逻辑依然会插入静音！
                # 所以，如果 Fill=False，我们需要在缺失时，**手动推进 current_duration_ms 到 target_end_ms**，但不添加音频对象？
                # 不对，推进了时间，下一个文件的 Gap 就变小了。
                # 举例：
                # Curr=10s. Item5 (10-20s) Missing.
                # If Fill=True: Add 10s Silence. Curr becomes 20s. Next Item6 (20-30s). Gap = 20-20=0. Perfect.
                # If Fill=False (User wants compact): User wants Item6 to start at 10s.
                # So we should NOT advance Curr. Curr stays 10s.
                # Next Item6 (20-30s). Gap = 20 - 10 = 10s. Code adds 10s silence. Result: Item6 starts at 20s.
                # This is NOT compact.
                # To make it compact, we must ignore the Start Time of the next file if the previous was missing?
                # Or simply: If Fill=False, we treat the timeline as "Relative" for missing parts?

                # 最佳实践方案：
                # 如果 Fill=False，当遇到缺失文件时，我们**不**更新 current_duration_ms。
                # 并且，我们需要一个标志位 `skip_next_gap`?
                # 不，最简单的做法是：如果 Fill=False，遇到缺失，我们记录一下。
                # 在处理下一个**有效**文件时，如果前一个是缺失的，我们强制 Gap = 0 (或者只保留最小呼吸隙)。
                # 这样实现起来有点绕。

                # 让我们换一个思路，满足 90% 的场景：
                # 用户说“不添加静默”，通常是指“不要把缺失的那段变成静音”。
                # 至于后面的音频是否要提前，取决于用户是否还信任 SRT 的时间轴。
                # 如果 SRT 时间轴是权威的（比如视频画面已经定好了），那么即使缺了音频，时间也不能动，否则音画不同步。
                # **在这种情况下，Fill=False 和 Fill=True 的区别仅在于：是否真的写入了一段静音数据。**
                # Fill=True: 写入静音数据 (文件大，播放器显示波形平坦)。
                # Fill=False: 不写入数据，但通过调整逻辑，让后面的音频依然在正确的时间响起？
                # 不可能。如果不写入静音，时间就会短，后面的音频就会提前。
                # **除非**：Fill=False 的含义是“忽略该条字幕的时间约束，后续所有音频整体前移”。
                # 这通常是用户想要的（比如某句话说错了，删掉，后面紧接着说下一句）。

                # 实现“整体前移” (Compact Mode):
                # 遇到缺失：什么都不做 (Curr 不变)。
                # 遇到下一个有效文件：计算 Gap = TargetStart - Curr。
                # 如果 前一个文件是缺失的，我们强制 Gap = 0 (直接拼接)。
                # 这样就能实现紧凑。

                pass  # 逻辑在下面统一处理，这里只标记

            continue  # 跳过当前循环，不加载音频

        # 2. 加载音频
        try:
            audio_segment = AudioSegment.from_wav(audio_path)
            segment_duration = len(audio_segment)
        except Exception as e:
            print(f"[ERROR] 读取文件 {audio_path} 失败: {e}")
            # 如果读取失败，视为缺失处理
            if fill_missing:
                # 复用上面的缺失逻辑太麻烦，这里简单处理：插入静音
                duration_needed = target_end_ms - target_start_ms
                gap_ms = target_start_ms - current_duration_ms
                if gap_ms > 0:
                    final_audio += AudioSegment.silent(duration=gap_ms)
                    current_duration_ms += gap_ms
                if duration_needed > 0:
                    final_audio += AudioSegment.silent(duration=duration_needed)
                    current_duration_ms += duration_needed
            else:
                # 读取失败且选择不填充：视为紧凑模式，不推进时间，不插静音
                # 需要标记前一个缺失，以便下一个文件紧凑连接
                # 这里为了简化，暂不实现复杂的“读取失败紧凑连接”，主要处理“文件不存在”
                pass
            continue

        # 3. 计算间隙 (Gap)
        gap_ms = target_start_ms - current_duration_ms

        # 【紧凑模式特殊处理】
        # 如果 fill_missing=False，且我们刚刚跳过了一些文件（导致 current_duration 远小于 target_start），
        # 用户可能希望直接拼接，而不是插入巨大的 Gap。
        # 判断逻辑：如果 fill_missing=False 且 gap_ms > 1000 (假设大于1秒认为是因为缺失导致的巨大间隙)
        # 则强制 gap_ms = 0 ?
        # 这样比较智能：如果间隙很小（正常停顿），保留；如果间隙很大（因为缺了文件），则消除。
        if not fill_missing and gap_ms > 500:
            # 检测到可能由缺失文件导致的大间隙，执行紧凑拼接
            # print(f"[DEBUG] 序号 {idx}: 检测到大间隙 ({gap_ms}ms)，因 fill_missing=False，执行紧凑拼接。")
            gap_ms = 0

        if gap_ms > 0:
            silence = AudioSegment.silent(duration=gap_ms)
            final_audio += silence
            current_duration_ms += gap_ms
        elif gap_ms < -500:  # 允许少量负值误差
            if i % 100 == 0:
                print(f"[WARN] 序号 {idx}: 前序音频超时 {-gap_ms}ms")

        # 4. 拼接音频
        final_audio += audio_segment
        current_duration_ms += segment_duration

        # 进度显示
        if (i + 1) % 100 == 0:
            print(f"[PROGRESS] 已处理 {i + 1}/{len(srt_data)} ...")

    # 5. 尾部填充 (仅在 Fill=True 或 需要严格对齐时)
    # 如果 Fill=False 且发生了紧凑，总时长肯定小于 SRT End，这里不再强制补齐，保持自然结束
    if fill_missing and srt_data:
        last_end_ms = srt_data[-1]['end_ms']
        if last_end_ms > current_duration_ms:
            tail_silence = last_end_ms - current_duration_ms
            final_audio += AudioSegment.silent(duration=tail_silence)
            print(f"[INFO] 尾部补充 {tail_silence}ms 静音以匹配 SRT 总时长")

    # 6. 导出
    print(f"\n[INFO] 正在导出最终文件: {output_path} ...")
    try:
        final_audio.export(output_path, format="wav")

        final_len_sec = len(final_audio) / 1000.0
        theoretical_len = srt_data[-1]['end_ms'] / 1000.0 if srt_data else 0

        print(f"[SUCCESS] 合并完成!")
        print(f"   - 总片段数: {len(srt_data)}")
        print(f"   - 缺失文件数: {missing_count}")
        print(f"   - 最终时长: {final_len_sec:.2f} 秒")
        if fill_missing:
            print(f"   - SRT 理论时长: {theoretical_len:.2f} 秒 (应一致)")
        else:
            print(f"   - SRT 理论时长: {theoretical_len:.2f} 秒 (因跳过缺失文件，实际时长较短)")

        print(f"   - 文件大小: {os.path.getsize(output_path) / 1024 / 1024:.2f} MB")
        print(f"   - 保存路径: {os.path.abspath(output_path)}")
        return True
    except Exception as e:
        print(f"[ERROR] 导出失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SRT 绝对时间轴音频合并工具 (可配置缺失策略)")
    parser.add_argument('--srt', type=str, default=INPUT_SRT_FILENAME, help='SRT 文件路径')
    parser.add_argument('--input_dir', type=str, default=INPUT_AUDIO_DIR, help='输入音频目录')
    parser.add_argument('--output', type=str, default=OUTPUT_FILENAME, help='输出文件名')
    # 命令行也可以覆盖配置
    parser.add_argument('--fill-missing', action='store_true', default=None, help='强制开启缺失填充静音')
    parser.add_argument('--no-fill-missing', action='store_true', default=None, help='强制关闭缺失填充静音 (紧凑模式)')

    args = parser.parse_args()

    print("=" * 60)
    print("SRT 绝对时间轴音频合并工具")
    print("=" * 60)

    # 确定策略优先级：命令行 > 配置文件
    if args.no_fill_missing:
        strategy = False
    elif args.fill_missing:
        strategy = True
    else:
        strategy = FILL_MISSING_WITH_SILENCE

    # 检查依赖
    try:
        AudioSegment.silent(duration=10)
    except Exception:
        print("❌ 错误: 未检测到 ffmpeg。")
        sys.exit(1)

    if not os.path.exists(args.srt):
        print(f"[ERROR] 找不到 SRT 文件: {args.srt}")
        sys.exit(1)

    if not os.path.exists(args.input_dir):
        print(f"[ERROR] 找不到音频目录: {args.input_dir}")
        sys.exit(1)

    with open(args.srt, 'r', encoding='utf-8-sig') as f:
        srt_content = f.read()

    srt_timeline = parse_srt_timeline(srt_content)
    if not srt_timeline:
        print("[ERROR] SRT 解析失败")
        sys.exit(1)

    print(f"[INFO] 解析到 {len(srt_timeline)} 条时间轴记录")
    print(f"[INFO] 策略: {'填充静音' if strategy else '紧凑跳过'}")
    print("-" * 60)

    success = merge_with_absolute_timing(srt_timeline, args.input_dir, args.output, strategy)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
