"""
Code OCR Tool - 使用百度 OCR 高精度含位置版将代码截图转换为文本文件
保持原始缩进，支持代码识别
"""

import os
import sys
import json
import base64
import time
import requests
from datetime import datetime


def get_access_token():
    """获取百度 API Access Token"""
    api_key = os.environ.get('BAIDU_API_KEY')
    secret_key = os.environ.get('BAIDU_SECRET_KEY')

    if not api_key or not secret_key:
        raise ValueError("请设置环境变量 BAIDU_API_KEY 和 BAIDU_SECRET_KEY")

    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key
    }
    response = requests.post(url, params=params)
    result = response.json()

    if "access_token" not in result:
        raise Exception(f"获取 access_token 失败: {result}")

    return result["access_token"]


def recognize_code_with_location(image_path, access_token):
    """使用百度 OCR 高精度含位置版识别代码"""
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate?access_token={access_token}"

    # 读取图片并转为 base64
    with open(image_path, 'rb') as f:
        image_base64 = base64.b64encode(f.read()).decode('utf-8')

    payload = {
        'image': image_base64,
        'detect_direction': 'false',
        'vertexes_location': 'true',  # 启用顶点位置
        'paragraph': 'false',  # 不使用段落模式，我们需要逐行处理
        'probability': 'false'
    }

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }

    response = requests.post(url, headers=headers, data=payload)
    result = response.json()

    if "words_result" not in result:
        if "error_code" in result:
            raise Exception(f"OCR API 错误: {result.get('error_msg', result)}")
        raise Exception(f"OCR 识别失败: {result}")

    return result["words_result"]


def reconstruct_code_with_indentation(words_result, avg_char_width_delta=1):
    """
    根据位置信息重构代码，保持原始缩进
    使用 vertexes_location 提高精度

    参数:
        words_result: OCR 识别结果
        avg_char_width_delta: 平均字符宽度的调整量（可选，默认0，即使用OCR计算结果）
    """
    if not words_result:
        return ""

    # 解析每个字符的位置信息
    chars_with_position = []

    for word in words_result:
        vertexes = word.get('vertexes_location', [])
        text = word.get('words', '')

        if not text:
            continue

        # 使用 vertexes_location 获取更精确的位置
        if len(vertexes) >= 4:
            # vertexes: [左上, 右上, 右下, 左下]
            left_x = vertexes[0]['x']   # 左上 x
            top_y = vertexes[0]['y']   # 左上 y
            right_x = vertexes[2]['x']  # 右下 x
            bottom_y = vertexes[2]['y'] # 右下 y
            width = right_x - left_x
            height = bottom_y - top_y
            # 使用 y 坐标中心值作为 top
            center_y = (top_y + bottom_y) / 2
        else:
            # 缺少 vertexes_location，无法精确计算缩进
            print("Error: OCR 结果缺少 vertexes_location 位置信息，无法精确处理缩进")
            sys.exit(1)

        chars_with_position.append({
            'text': text,
            'top': center_y,  # 使用更精确的 y 中心坐标
            'left': left_x,
            'top_raw': top_y,  # 保留原始 top 值用于参考
            'width': width,
            'height': height
        })

    if not chars_with_position:
        return ""

    # 计算平均字符高度，用于动态聚类容差
    heights = [c['height'] for c in chars_with_position if c['height'] > 0]
    avg_height = sum(heights) / len(heights)
    # 容差为平均字符高度的 30%，确保同一行的字符能被正确归类
    cluster_tolerance = avg_height * 0.3

    # 按行分组 - 使用聚类算法，基于 center_y 坐标（更精确）
    all_tops = sorted([c['top'] for c in chars_with_position])

    # 使用聚类方法：找到主要的"行"位置
    # 思路：top 值相近的归为一组（动态容差）
    clusters = []
    current_cluster = [all_tops[0]]

    for i in range(1, len(all_tops)):
        top = all_tops[i]
        # 如果当前值与当前簇的平均值差距小于容差，归入同一簇
        cluster_avg = sum(current_cluster) / len(current_cluster)
        if abs(top - cluster_avg) < cluster_tolerance:
            current_cluster.append(top)
        else:
            clusters.append(current_cluster)
            current_cluster = [top]
    clusters.append(current_cluster)

    # 计算每个簇的中心点作为行键
    line_centers = {}
    for i, cluster in enumerate(clusters):
        center = sum(cluster) / len(cluster)
        line_centers[round(center)] = i  # 存储排序后的行号

    # 根据行中心点进行分组
    lines = {}
    for char_info in chars_with_position:
        top = char_info['top']

        # 找到最近的行中心
        closest_line_key = min(line_centers.keys(), key=lambda x: abs(x - top))
        line_key = line_centers[closest_line_key]

        if line_key not in lines:
            lines[line_key] = []
        lines[line_key].append(char_info)

    # 计算平均字符宽度，用于动态空格计算
    widths = []
    for c in chars_with_position:
        if c['width'] > 0 and len(c['text']) > 0:
            # 估算单个字符宽度 = 总宽度 / 字符数
            char_w = c['width'] / len(c['text'])
            if char_w > 0:
                widths.append(char_w)
    calculated_avg = sum(widths) / len(widths) if widths else 8

    # 计算平均行高，用于空行检测（使用垂直聚类的中心点）
    # clusters 是列表的列表，每个元素是一组的 top 值
    cluster_centers = [sum(c) / len(c) for c in clusters]  # 先计算每个簇的中心
    if len(cluster_centers) >= 2:
        row_diffs = []
        for i in range(1, len(cluster_centers)):
            diff = cluster_centers[i] - cluster_centers[i-1]
            if diff > 0:
                row_diffs.append(diff)
        avg_row_height = sum(row_diffs) / len(row_diffs) if row_diffs else 0
    else:
        avg_row_height = 0

    # 应用 ratio 作为倍率调整
    avg_char_width = calculated_avg * avg_char_width_delta
    print(f"avg_char_width: {avg_char_width} (calculated: {calculated_avg}, ratio: {avg_char_width_delta})")

    # 字符宽度容差（固定值）
    char_width_tolerance = avg_char_width * 0.5

    # 按行号排序
    sorted_lines = sorted(lines.items(), key=lambda x: x[0])

    # 计算所有行中所有文本块的全局最小 left 值
    global_min_left = float('inf')
    for line_key, chars in sorted_lines:
        for c in chars:
            if c['left'] < global_min_left:
                global_min_left = c['left']

    # 全局聚类：对所有行的 left 值一起进行聚类
    all_chars = []
    for line_key, chars in sorted_lines:
        all_chars.extend(chars)

    all_unique_lefts = sorted(set(c['left'] for c in all_chars))

    # 对 left 值进行聚类：相近的 left 值归为一簇
    global_left_clusters = []
    if all_unique_lefts:
        current_cluster = [all_unique_lefts[0]]
        for i in range(1, len(all_unique_lefts)):
            curr_left = all_unique_lefts[i]
            # 当前值与当前簇的平均值差距小于容差，归入同一簇
            cluster_avg = sum(current_cluster) / len(current_cluster)
            if abs(curr_left - cluster_avg) < char_width_tolerance:
                current_cluster.append(curr_left)
            else:
                global_left_clusters.append(current_cluster)
                current_cluster = [curr_left]
        global_left_clusters.append(current_cluster)

    # 使用全局最小 left 值，将所有簇的 left 值减去这个值
    global_left_clusters = [[left - global_min_left for left in cluster] for cluster in global_left_clusters]

    # 重构每行文本
    result_lines = []
    prev_line_avg_top = None  # 上一行的平均 top 值

    for line_key, chars in sorted_lines:
        # 按 left 位置排序字符
        chars_sorted = sorted(chars, key=lambda x: x['left'])

        if not chars_sorted:
            continue

        # 按簇的 left 值排序（global_left_clusters 已排序，直接用索引）
        cluster_centers = [sum(cluster) / len(cluster) for cluster in global_left_clusters]

        # 简化：直接按顺序处理字符，找到其所属聚类并计算空格
        line_parts = []
        total_prev_chars = 0

        for c in chars_sorted:
            # 找到最近的簇中心
            left_rel = c['left'] - global_min_left
            best_cluster_idx = min(range(len(cluster_centers)), key=lambda i: abs(left_rel - cluster_centers[i]))

            # 使用簇中心值计算空格数
            space_count = round(cluster_centers[best_cluster_idx] / avg_char_width) - total_prev_chars
            space_count = max(0, space_count)

            if space_count > 0:
                line_parts.append(' ' * space_count)
                total_prev_chars += space_count

            line_parts.append(c['text'])
            total_prev_chars += len(c['text'])

        line_text = ''.join(line_parts)

        # 去除尾随空格
        line_text = line_text.rstrip()

        if line_text:
            # 空行检测：如果与上一行的行高差距大于 avg_row_height * 1.5，则插入空行
            current_line_avg_top = sum(c['top'] for c in chars) / len(chars)
            if avg_row_height > 0 and prev_line_avg_top is not None:
                gap = current_line_avg_top - prev_line_avg_top
                # 如果间距大于平均行高的 1.5 倍，认为是空行
                if gap > avg_row_height * 1.5:
                    # 计算空行数（向上取整）
                    empty_lines = int(round(gap / avg_row_height)) - 1
                    for _ in range(empty_lines):
                        result_lines.append('')

            result_lines.append(line_text)
            prev_line_avg_top = current_line_avg_top

    return '\n'.join(result_lines)


def save_output(text, input_path, output_dir=None, avg_char_width_delta=None):
    """保存输出文件，使用同名+时间戳+ratio命名"""
    # 获取输入文件名（不含扩展名）
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 构建输出文件名（包含 avg_char_width_delta）
    if avg_char_width_delta is not None:
        output_name = f"{base_name}_{timestamp}_{avg_char_width_delta}.txt"
    else:
        output_name = f"{base_name}_{timestamp}.txt"

    # 确定输出目录
    if output_dir is None:
        output_dir = os.path.dirname(input_path) or '.'
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(input_path), output_dir)

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, output_name)

    # 保存文件（使用 UTF-8 编码）
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)

    return output_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Code OCR - 将代码截图转换为文本')
    parser.add_argument('input', nargs='?', default=None, help='图片路径（当使用 -l 时可选）')
    parser.add_argument('output_dir', nargs='?', default=None, help='输出目录')
    parser.add_argument('--ratio', '-r', type=float, default=0.955, help='平均字符宽度倍率（默认0.955）')
    parser.add_argument('--save-json', '-s', metavar='FILE', help='保存 OCR 中间结果到 JSON 文件')
    parser.add_argument('--load-json', '-l', metavar='FILE', help='从 JSON 文件加载 OCR 结果（跳过 OCR）')

    args = parser.parse_args()

    # 如果指定了 -l/--load-json，直接使用它作为 JSON 文件路径
    if args.load_json:
        load_json_path = args.load_json
        input_path = args.input if args.input else load_json_path
    else:
        load_json_path = None
        input_path = args.input

    output_dir = args.output_dir
    ratio = args.ratio
    save_json_path = args.save_json

    try:
        # 加载或进行 OCR 识别
        if load_json_path:
            # 从 JSON 加载
            print(f"从 JSON 文件加载 OCR 结果: {load_json_path}")
            with open(load_json_path, 'r', encoding='utf-8') as f:
                words_result = json.load(f)
        else:
            # 运行 OCR
            if not os.path.exists(input_path):
                print(f"Error: 文件不存在: {input_path}")
                sys.exit(1)

            print(f"正在识别图片: {input_path}")

            # 获取 Access Token
            print("获取 Access Token...")
            access_token = get_access_token()

            # 进行 OCR 识别
            print("正在进行 OCR 识别...")
            words_result = recognize_code_with_location(input_path, access_token)

            # 保存中间结果（如果指定了 --save-json）
            if save_json_path:
                print(f"保存 OCR 结果到: {save_json_path}")
                with open(save_json_path, 'w', encoding='utf-8') as f:
                    json.dump(words_result, f, ensure_ascii=False, indent=2)

        if not words_result:
            print("Warning: 未识别到文字")
            sys.exit(1)

        # 根据位置信息重构代码
        print("正在重构代码...")
        code_text = reconstruct_code_with_indentation(words_result, ratio)

        if not code_text:
            print("Warning: 无法重构代码，尝试直接输出识别结果")

            # 降级方案：直接输出所有识别文字
            for word in words_result:
                print(word.get('words', ''), end=' ')
            print()
            sys.exit(1)

        # 保存输出
        output_path = save_output(code_text, input_path, output_dir, ratio)
        print(f"识别完成！已保存至: {output_path}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
