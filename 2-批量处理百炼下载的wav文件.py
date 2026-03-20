import os
import re
import sys
import argparse
import csv
from datetime import datetime
from pydub import AudioSegment
from tqdm import tqdm
from typing import List, Dict, Optional

# ==============================================================================
# ⚙️ 配置区域
# ==============================================================================

INPUT_SRT_FILENAME = "第六节课输出音频_原文.srt"
INPUT_AUDIO_DIR = "audio_output_qwen3_final"
OUTPUT_FILENAME_PREFIX = "合并后第六节课输出音频_原文"

DEFAULT_THRESHOLD_A = 3.0
DEFAULT_THRESHOLD_B = 3.0


# ==============================================================================
# 工具函数
# ==============================================================================

def parse_srt_time(time_str: str) -> int:
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return int((h * 3600 + m * 60 + s) * 1000)


def parse_srt_content(content: str) -> List[Dict]:
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
            duration_ms = end_ms - start_ms
            if duration_ms <= 0: continue
            result.append({
                'index': idx,
                'start_ms': start_ms,
                'end_ms': end_ms,
                'duration_ms': duration_ms,
                'time_str': f"{match[1]} --> {match[2]}"
            })
        except Exception:
            pass

    return sorted(result, key=lambda x: x['index'])


def get_sorted_audio_files(input_dir: str) -> List[str]:
    if not os.path.exists(input_dir):
        return []
    files = [f for f in os.listdir(input_dir) if f.endswith('.wav')]

    def sort_key(f):
        num = re.search(r'(\d+)', f)
        return int(num.group(1)) if num else 0

    return sorted(files, key=sort_key)


class MergeLogger:
    def __init__(self, output_dir: str, timestamp_str: str):
        self.output_dir = output_dir
        self.csv_path = os.path.join(output_dir, f"合并日志_{timestamp_str}.csv")
        self.txt_path = os.path.join(output_dir, f"合并日志_{timestamp_str}.log")
        self.records = []

    def log(self, index, filename, scheduled_start, scheduled_end, current_cumulative_before,
            next_srt_start, next_next_srt_start, judgment_result, action_taken,
            shift_ms, truncate_info, note=""):
        self.records.append({
            '序号': index,
            '文件名': filename,
            'SRT本应开始(ms)': scheduled_start,
            'SRT本应结束(ms)': scheduled_end,
            '合并前累计时长(ms)': current_cumulative_before,
            '下一句SRT开始(ms)': next_srt_start,
            '下下句SRT开始(ms)': next_next_srt_start,
            '逻辑判断结果': judgment_result,
            '实际处理方式': action_taken,
            '时间偏移量(ms)': shift_ms,
            '截断详情': truncate_info,
            '备注': note
        })

    def save(self, total_duration_ms):
        with open(self.csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            fields = ['序号', '文件名', 'SRT本应开始(ms)', 'SRT本应结束(ms)', '合并前累计时长(ms)',
                      '下一句SRT开始(ms)', '下下句SRT开始(ms)', '逻辑判断结果', '实际处理方式',
                      '时间偏移量(ms)', '截断详情', '备注']
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.records)

        with open(self.txt_path, 'w', encoding='utf-8') as f:
            f.write("=" * 180 + "\n")
            f.write("音频智能合并与动态对齐日志 (v4 - 末尾安全版)\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"总时长: {total_duration_ms} ms ({total_duration_ms / 1000:.2f} s)\n")
            f.write("=" * 180 + "\n\n")

            header = f"{'ID':<4} {'文件名':<10} {'本应区间':<20} {'累计前':<10} {'判断结果':<20} {'处理方式':<30} {'偏移':<8} {'截断详情':<15} {'备注'}\n"
            f.write(header)
            f.write("-" * 180 + "\n")

            for r in self.records:
                interval = f"{r['SRT本应开始(ms)']}->{r['SRT本应结束(ms)']}"
                shift_str = f"{r['时间偏移量(ms)']:+d}"
                trunc = r['截断详情'] if r['截断详情'] else "-"
                f.write(f"{r['序号']:<4} {r['文件名']:<10} {interval:<20} {r['合并前累计时长(ms)']:<10} "
                        f"{r['逻辑判断结果']:<20} {r['实际处理方式']:<30} {shift_str:<8} {trunc:<15} {r['备注']}\n")

            f.write("\n" + "=" * 180 + "\n")
            f.write(f"文件位置:\n  CSV: {os.path.abspath(self.csv_path)}\n  LOG: {os.path.abspath(self.txt_path)}\n")
            f.write("=" * 180 + "\n")

        print(
            f"[INFO] 合并日志已生成:\n  - CSV: {os.path.abspath(self.csv_path)}\n  - LOG: {os.path.abspath(self.txt_path)}")


def main():
    parser = argparse.ArgumentParser(description="音频智能合并与动态对齐工具 (v4)")
    parser.add_argument('--srt', type=str, default=INPUT_SRT_FILENAME)
    parser.add_argument('--input_dir', type=str, default=INPUT_AUDIO_DIR)
    parser.add_argument('--output_name', type=str, default=None)
    parser.add_argument('--threshold-a', type=float, default=DEFAULT_THRESHOLD_A)
    parser.add_argument('--threshold-b', type=float, default=DEFAULT_THRESHOLD_B)

    args = parser.parse_args()

    thresh_a_ms = int(args.threshold_a * 1000)
    thresh_b_ms = int(args.threshold_b * 1000)

    now = datetime.now()
    timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")

    if args.output_name:
        final_filename = f"{args.output_name}_{timestamp_str}.wav"
    else:
        final_filename = f"{OUTPUT_FILENAME_PREFIX}_{timestamp_str}.wav"

    output_dir = os.getcwd()
    output_path = os.path.join(output_dir, final_filename)

    print("=" * 70)
    print("音频智能合并与动态对齐工具 (v4 - 末尾安全版)")
    print("=" * 70)
    print(f"输入目录 : {os.path.abspath(args.input_dir)}")
    print(f"SRT 文件 : {args.srt}")
    print(f"输出文件 : {final_filename}")
    print(f"阈值 A (当前句截断) : {args.threshold_a} 秒")
    print(f"阈值 B (下一句截断) : {args.threshold_b} 秒")
    print(f"特殊保护 : 最后 1 个文件禁止截断，最后 2 个文件跳过前瞻预判")
    print("-" * 70)

    try:
        AudioSegment.silent(10)
    except:
        print("❌ 未检测到 ffmpeg"); sys.exit(1)

    if not os.path.exists(args.srt): print(f"❌ SRT 不存在"); sys.exit(1)
    if not os.path.exists(args.input_dir): print(f"❌ 音频目录不存在"); sys.exit(1)

    srt_data = parse_srt_content(open(args.srt, 'r', encoding='utf-8-sig').read())
    audio_files = get_sorted_audio_files(args.input_dir)

    if not srt_data: print("❌ 无有效 SRT 数据"); sys.exit(1)
    if not audio_files: print("❌ 未找到 WAV 文件"); sys.exit(1)

    file_map = {}
    for f in audio_files:
        num = int(re.search(r'(\d+)', f).group(1))
        file_map[num] = f

    processing_list = []
    for item in srt_data:
        if item['index'] in file_map:
            item['filename'] = file_map[item['index']]
            item['filepath'] = os.path.join(args.input_dir, item['filename'])
            processing_list.append(item)
        else:
            print(f"[WARN] 跳过 SRT #{item['index']}, 未找到对应音频文件")

    if not processing_list:
        print("❌ 没有匹配的音频文件可处理");
        sys.exit(1)

    total_count = len(processing_list)
    print(f"共加载 {total_count} 个片段，开始合并...\n")

    logger = MergeLogger(output_dir, timestamp_str)

    # 初始化
    first_item = processing_list[0]
    base_audio = AudioSegment.from_wav(first_item['filepath'])
    final_output = base_audio
    current_cumulative_ms = len(base_audio)

    # 记录第一个片段
    logger.log(
        index=first_item['index'], filename=first_item['filename'],
        scheduled_start=first_item['start_ms'], scheduled_end=first_item['end_ms'],
        current_cumulative_before=0,
        next_srt_start=processing_list[1]['start_ms'] if total_count > 1 else 0,
        next_next_srt_start=processing_list[2]['start_ms'] if total_count > 2 else 0,
        judgment_result="起始片段", action_taken="直接载入", shift_ms=0, truncate_info="", note="基准片段"
    )

    pbar = tqdm(processing_list[1:], desc="合并进度", unit="seg")

    next_audio_cache = None
    next_segment_override = {}  # 用于传递截断指令

    for i, item in enumerate(pbar):
        curr_idx = item['index']
        curr_file = item['filename']
        curr_path = item['filepath']
        curr_srt_start = item['start_ms']
        curr_srt_end = item['end_ms']

        # 计算绝对索引 (在 processing_list 中的位置)
        current_list_index = i + 1

        # 判断是否为最后几个文件
        is_last_file = (current_list_index == total_count - 1)
        is_second_last_file = (current_list_index == total_count - 2)

        has_next = not is_last_file
        next_item = processing_list[current_list_index + 1] if has_next else None
        next_srt_start = next_item['start_ms'] if has_next else 0

        has_next_next = not is_last_file and not is_second_last_file
        next_next_item = processing_list[current_list_index + 2] if has_next_next else None
        next_next_srt_start = next_next_item['start_ms'] if has_next_next else 0

        # 加载当前音频
        try:
            curr_audio = AudioSegment.from_wav(curr_path)
        except Exception as e:
            print(f"[ERROR] 无法加载 {curr_file}: {e}")
            continue

        original_curr_len = len(curr_audio)
        final_curr_audio = curr_audio
        silence_to_insert = 0
        action_desc = ""
        judgment_desc = ""
        shift_ms = current_cumulative_ms - curr_srt_start
        truncate_info = ""
        note = ""

        # --------------------------------------------------------------
        # 🛡️ 特殊保护：如果是最后一个文件，禁止任何截断，直接追加
        # --------------------------------------------------------------
        if is_last_file:
            judgment_desc = "末尾文件 (保护模式)"
            action_desc = "直接追加 (禁止截断)"
            truncate_info = ""
            note = "最后一段音频，保留完整时长，不检查超时"
            silence_to_insert = 0
            # 跳过所有逻辑，直接执行合并
        else:
            # --------------------------------------------------------------
            # 🧠 检查点 A: 当前句 vs 下一句开始 (非最后文件执行)
            # --------------------------------------------------------------
            projected_end_if_appended = current_cumulative_ms + original_curr_len

            overflow_a = projected_end_if_appended - next_srt_start

            if overflow_a >= thresh_a_ms:
                # A1: 严重超时，截断当前句
                target_len = next_srt_start - current_cumulative_ms
                target_len = max(0, target_len)
                final_curr_audio = curr_audio[:target_len]
                truncated_amount = original_curr_len - len(final_curr_audio)

                judgment_desc = f"A 超时 ({overflow_a}ms>={thresh_a_ms}ms)"
                action_desc = f"截断当前句 ({truncated_amount}ms)"
                truncate_info = f"当前句-{truncated_amount}ms"
                note = f"强制对齐到下一句开始 ({next_srt_start}ms)"
                silence_to_insert = 0

            elif overflow_a > 0:
                # A2: 轻微超时
                judgment_desc = f"A 轻微超时 ({overflow_a}ms<{thresh_a_ms}ms)"
                action_desc = "直接追加 (允许挤压)"
                truncate_info = ""
                silence_to_insert = 0

            else:
                # A3: 未超时
                silence_needed = next_srt_start - projected_end_if_appended
                silence_to_insert = silence_needed
                judgment_desc = f"A 正常 (差 {silence_needed}ms)"
                action_desc = f"追加 + 静默 {silence_needed}ms"
                truncate_info = ""

            # --------------------------------------------------------------
            # 🧠 检查点 B: 前瞻 (非最后且非倒数第二文件执行)
            # --------------------------------------------------------------
            # 如果是倒数第二个文件，不存在“下下句”，因此跳过 B 逻辑，避免错误预判
            if has_next_next:
                # 获取下一句音频
                if next_audio_cache is None or getattr(next_audio_cache, '_idx', -1) != next_item['index']:
                    next_audio_cache = AudioSegment.from_wav(next_item['filepath'])
                    next_audio_cache._idx = next_item['index']

                next_orig_len = len(next_audio_cache)
                end_after_curr_no_silence = current_cumulative_ms + len(final_curr_audio)
                projected_end_next = end_after_curr_no_silence + next_orig_len

                overflow_b = projected_end_next - next_next_srt_start

                if overflow_b >= thresh_b_ms:
                    # B1: 严重超前，计划截断下一句
                    target_next_len = next_next_srt_start - end_after_curr_no_silence
                    target_next_len = max(0, target_next_len)

                    next_segment_override[next_item['index']] = {
                        'target_len': target_next_len,
                        'reason': f"B 超前 ({overflow_b}ms>={thresh_b_ms}ms)"
                    }

                    if silence_to_insert > 0:
                        silence_to_insert = 0
                        action_desc += " (取消静默)"
                        note += "; 预判下一句将严重超时，取消本句后静默并计划截断下一句"

                    judgment_desc += f" -> B 触发截断下一句"
                    truncate_info += f"; 计划截断下一句" if not truncate_info else f"; 计划截断下一句"

                elif overflow_b > 0:
                    # B2: 轻微超前，仅取消静默
                    if silence_to_insert > 0:
                        silence_to_insert = 0
                        action_desc += " (取消静默)"
                        note += "; 预判下一句轻微超时，取消本句后静默"
                    judgment_desc += f" -> B 轻微超前 ({overflow_b}ms)"
            else:
                if is_second_last_file:
                    judgment_desc += " (跳过 B: 倒数第二，无下下句)"
                    note += "; 倒数第二个文件，跳过前瞻预判，确保最后一句正常开始"

        # --------------------------------------------------------------
        # 🔨 执行合并
        # --------------------------------------------------------------

        # 检查是否有来自上一轮的“强制截断当前句”指令
        # 注意：最后一个文件即使有指令（理论上不可能，因为倒数第二个文件不会计划截断最后一个），我们也应该在这里再次保护
        if not is_last_file and curr_idx in next_segment_override:
            override = next_segment_override[curr_idx]
            target_len = override['target_len']
            if target_len < len(final_curr_audio):
                truncated_amt = len(final_curr_audio) - target_len
                final_curr_audio = final_curr_audio[:target_len]
                action_desc = f"被前序逻辑截断 ({truncated_amt}ms)"
                truncate_info = f"被前序截断-{truncated_amt}ms"
                note = override['reason']
                silence_to_insert = 0
            del next_segment_override[curr_idx]
        elif is_last_file and curr_idx in next_segment_override:
            # 防御性编程：万一有指令指向最后一个文件，强制忽略
            del next_segment_override[curr_idx]
            note += " (忽略截断指令：保护最后文件)"

        # 追加音频
        final_output += final_curr_audio

        # 追加静默
        if silence_to_insert > 0:
            silence = AudioSegment.silent(duration=silence_to_insert)
            final_output += silence

        current_cumulative_ms = len(final_output)

        logger.log(
            index=curr_idx, filename=curr_file,
            scheduled_start=curr_srt_start, scheduled_end=curr_srt_end,
            current_cumulative_before=current_cumulative_ms - len(final_curr_audio) - silence_to_insert,
            next_srt_start=next_srt_start, next_next_srt_start=next_next_srt_start,
            judgment_result=judgment_desc, action_taken=action_desc,
            shift_ms=shift_ms, truncate_info=truncate_info, note=note
        )

        pbar.set_postfix({"Cumulative": f"{current_cumulative_ms / 1000:.1f}s"})

    pbar.close()

    print(f"\n[INFO] 正在导出最终文件 ({len(final_output) / 1000:.2f} s)...")
    final_output.export(output_path, format="wav")

    logger.save(len(final_output))

    print("-" * 70)
    print(f"✅ 处理完成!")
    print(f"输出文件 : {os.path.abspath(output_path)}")
    print(f"日志文件 : {os.path.abspath(logger.csv_path)}")


if __name__ == "__main__":
    main()
