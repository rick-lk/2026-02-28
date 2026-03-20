import os
import re
import time
import sys
import requests
import dashscope
import argparse
from typing import List, Tuple, Optional

# ==============================================================================
# 配置区域
# ==============================================================================

CONFIG_API_KEY = "sk-********"
INPUT_SRT_FILENAME = "第六节课输出音频_原文.srt"
OUTPUT_DIR = "audio_output_qwen3_final"

# 【核心配置】
MODEL_NAME = "qwen3-tts-vd-2026-01-26"
VOICE_NAME = "******"  # 请确保此处填写正确的音色ID

REGION_URL = 'https://dashscope.aliyuncs.com/api/v1'
MAX_RETRIES = 3
TIMEOUT = 60
# 【核心开关】
# default=-1时脚本自动检测断点进行断点继续生成，-1改成100时意思是从srt文件的第100条开始继续生成
default=-1

# ==============================================================================
# 初始化工具
# ==============================================================================

def init_env():
    api_key = CONFIG_API_KEY
    if not api_key or "xxxx" in api_key:
        api_key = os.getenv("DASHSCOPE_API_KEY")

    if not api_key:
        print("❌ 错误: 未找到 API Key")
        sys.exit(1)

    os.environ["DASHSCOPE_API_KEY"] = api_key
    dashscope.base_http_api_url = REGION_URL
    dashscope.api_key = api_key

    print(f"[INFO] API Key 已加载 (末尾: ...{api_key[-6:]})")
    print(f"[INFO] 模型: {MODEL_NAME} | 音色: {VOICE_NAME}")


def parse_srt_content(content: str) -> List[Tuple[int, str]]:
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    srt_pattern = re.compile(
        r'(\d+)\n'
        r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n'
        r'(.*?)\n\n',
        re.DOTALL
    )
    if not content.endswith('\n\n'): content += '\n\n'

    matches = srt_pattern.findall(content)
    result = []
    for match in matches:
        try:
            index = int(match[0])
            text = re.sub(r'\s+', ' ', match[1].strip())
            if text: result.append((index, text))
        except ValueError:
            continue
    return result


def get_existing_count(output_dir: str) -> int:
    """扫描目录，返回已成功生成的最大序号"""
    if not os.path.exists(output_dir):
        return 0

    max_idx = 0
    # 匹配 .wav 或 .mp3 文件，假设格式为 0001.wav
    pattern = re.compile(r'(\d+)\.(wav|mp3)')

    for filename in os.listdir(output_dir):
        match = pattern.match(filename)
        if match:
            idx = int(match.group(1))
            if idx > max_idx:
                max_idx = idx
    return max_idx


def download_audio_from_url(url: str, filepath: str) -> bool:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return True
        else:
            print(f"[WARN] 下载失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"[WARN] 下载异常: {str(e)}")
        return False


def synthesize_speech(text: str, index: int, output_dir: str) -> bool:
    # 确定文件后缀 (根据之前测试是 wav)
    ext = ".wav"
    filename = f"{index:04d}{ext}"
    filepath = os.path.join(output_dir, filename)

    # 【关键】如果文件已存在，直接跳过
    if os.path.exists(filepath):
        # 简单检查文件大小，避免空文件
        if os.path.getsize(filepath) > 100:
            print(f"[SKIP] 序号 {index} 已存在，跳过。")
            return True
        else:
            print(f"[WARN] 序号 {index} 文件存在但过小，重新生成...")
            # 可以选择删除坏文件或直接覆盖，这里选择直接覆盖逻辑继续往下走

    clean_text = text.replace('#', '').replace('*', '')
    if len(clean_text) < 1: clean_text = "。"

    if index % 50 == 0 or index <= 5:
        print(f"[INFO] 处理序号 {index}: {clean_text[:30]}...")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = dashscope.MultiModalConversation.call(
                model=MODEL_NAME,
                api_key=os.getenv("DASHSCOPE_API_KEY"),
                text=clean_text,
                voice=VOICE_NAME,
                stream=False
            )

            if isinstance(response, dict):
                status_code = response.get('status_code', 200)
                if status_code != 200:
                    msg = response.get('message', 'Unknown')
                    code = response.get('code', '')
                    print(f"\n[ERROR] 序号 {index} 服务拒绝: Code={code}, Msg={msg}")
                    if "invalid" in str(msg).lower() or "auth" in str(msg).lower():
                        return False
                    if attempt < MAX_RETRIES:
                        time.sleep(2)
                        continue
                    return False

                output_data = response.get('output', {})
                audio_data = output_data.get('audio', {})
                audio_url = audio_data.get('url')

                if not audio_url:
                    # 尝试 Base64 备用
                    audio_base64 = audio_data.get('data')
                    if audio_base64:
                        import base64
                        try:
                            file_bytes = base64.b64decode(audio_base64)
                            with open(filepath, 'wb') as f:
                                f.write(file_bytes)
                            return True
                        except:
                            pass

                    print(f"\n[WARN] 序号 {index} 无音频数据 (尝试 {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES: time.sleep(2); continue
                    return False

                if download_audio_from_url(audio_url, filepath):
                    if index % 50 == 0 or index <= 5:
                        print(f"[SUCCESS] 序号 {index} 保存成功")
                    return True
                else:
                    if attempt < MAX_RETRIES: time.sleep(2); continue
                    return False
            else:
                return False

        except Exception as e:
            if index <= 5: print(f"\n[EXCEPTION] 序号 {index}: {str(e)}")
            if "Unauthorized" in str(e): return False
            if attempt < MAX_RETRIES:
                time.sleep(2)
            else:
                return False

    return False


def main():
    # ==========================================
    # 命令行参数解析
    # ==========================================
    parser = argparse.ArgumentParser(description="SRT 转语音 (支持断点续传)")
    parser.add_argument('--start', type=int, default=-1,
                        help='手动指定起始序号 (例如: --start 100)。若不填，自动检测断点。')
    args = parser.parse_args()

    print("=" * 60)
    print("SRT 转语音脚本 (支持断点续传版)")
    print("=" * 60)

    init_env()

    # 路径检查
    final_path = ""
    candidates = [INPUT_SRT_FILENAME, os.path.basename(INPUT_SRT_FILENAME),
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(INPUT_SRT_FILENAME))]
    for p in candidates:
        if os.path.exists(p): final_path = p; break

    if not final_path:
        print(f"[ERROR] 找不到文件: {INPUT_SRT_FILENAME}")
        sys.exit(1)

    print(f"[INFO] 文件: {final_path}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 读取 SRT
    srt_content = ""
    for enc in ['utf-8', 'gbk', 'utf-8-sig']:
        try:
            with open(final_path, 'r', encoding=enc) as f:
                srt_content = f.read()
            break
        except:
            continue

    if not srt_content:
        print("[ERROR] 文件为空")
        sys.exit(1)

    subtitles = parse_srt_content(srt_content)
    if not subtitles:
        print("[ERROR] 未解析到字幕")
        sys.exit(1)

    total_count = len(subtitles)
    print(f"[INFO] SRT 共 {total_count} 条字幕")

    # ==========================================
    # 计算起始位置
    # ==========================================
    start_index = 1

    if args.start != -1:
        # 用户手动指定
        start_index = args.start
        print(f"[INFO] 检测到手动指定参数 --start {start_index}")
    else:
        # 自动检测
        last_success = get_existing_count(OUTPUT_DIR)
        if last_success > 0:
            start_index = last_success + 1
            print(f"[INFO] 自动检测到已生成 {last_success} 条，将从序号 {start_index} 继续")
        else:
            print("[INFO] 未检测到历史进度，从头开始")

    # 过滤出需要处理的字幕列表
    # 注意：subtitles 列表中的 index 是 SRT 原始序号，我们需要找到列表中第一个 >= start_index 的元素
    tasks = [(idx, text) for idx, text in subtitles if idx >= start_index]

    if not tasks:
        print(f"[INFO] 所有任务已完成 (最大序号 >= {start_index})")
        return

    print(f"[INFO] 本次计划处理 {len(tasks)} 条 (序号 {start_index} 到 {tasks[-1][0]})")
    print("-" * 60)

    success_count = 0
    fail_count = 0
    consecutive_fail = 0

    for index, text in tasks:
        if synthesize_speech(text, index, OUTPUT_DIR):
            success_count += 1
            consecutive_fail = 0  # 重置连续失败计数
        else:
            fail_count += 1
            consecutive_fail += 1
            print(f"[FAIL] 序号 {index} 失败。")

            # 如果连续失败 5 次，停止
            if consecutive_fail >= 5:
                print("\n[STOP] 连续失败 5 次，任务终止。请检查网络或 API 状态。")
                print(f"[提示] 下次运行可继续使用自动续传，或手动指定: python script.py --start {index}")
                break

        # 限流
        time.sleep(0.8)

    print("-" * 60)
    print(f"[完成] 本次成功: {success_count}, 失败: {fail_count}")
    print(f"[总进度] 目录中已有约 {get_existing_count(OUTPUT_DIR)} 条音频")
    print(f"[输出目录] {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    try:
        import requests
    except ImportError:
        print("❌ 错误: 缺少 requests 库。请运行: pip install requests")
        sys.exit(1)

    main()
