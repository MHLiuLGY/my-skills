#!/usr/bin/env python3
"""
Docling Convert - 使用 docling API 转换文档为 Markdown
"""
import argparse
import base64
import glob
import os
import re
import requests
import sys

DOCLING_API_URL = "http://localhost:5001/v1/convert/source"


def convert_file(file_path, output_dir=None, api_url=None):
    """转换单个文件"""
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在: {file_path}")
        return False

    # 确定输出目录
    if output_dir is None:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_dir = os.path.join(os.path.dirname(file_path), base_name)

    os.makedirs(output_dir, exist_ok=True)

    # 读取文件并转换为 base64
    print(f"读取文件: {file_path}")
    with open(file_path, 'rb') as f:
        file_content = f.read()
    base64_content = base64.b64encode(file_content).decode('utf-8')

    # 构建请求
    payload = {
        "sources": [{
            "kind": "file",
            "filename": os.path.basename(file_path),
            "base64_string": base64_content
        }],
        "options": {
            "to_formats": ["md"],
            "image_export_mode": "embedded",
            "do_ocr": True,
            "table_mode": "accurate"
        }
    }

    url = api_url or DOCLING_API_URL
    print(f"正在调用 docling API 转换...")

    try:
        response = requests.post(url, json=payload, timeout=600)
    except requests.exceptions.ConnectionError:
        print(f"错误: 无法连接到 docling 服务 ({url})")
        print("请确保 docling 服务正在运行")
        return False

    if response.status_code != 200:
        print(f"API 错误: {response.status_code}")
        print(response.text)
        return False

    result = response.json()
    md_content = result['document']['md_content']

    # 提取图片
    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    img_pattern = r'!\[Image\]\(data:image/([^;]+);base64,([^)]+)\)'
    matches = re.findall(img_pattern, md_content)

    print(f"发现 {len(matches)} 张图片")

    if matches:
        for idx, (img_type, b64_data) in enumerate(matches):
            img_bytes = base64.b64decode(b64_data)
            ext = 'png' if img_type == 'png' else img_type
            img_path = os.path.join(images_dir, f"image_{idx}.{ext}")
            with open(img_path, 'wb') as f:
                f.write(img_bytes)
            print(f"保存图片: {img_path}")

        # 替换引用
        def replace_with_ref(match):
            replace_with_ref.count = getattr(replace_with_ref, 'count', -1) + 1
            return f'![Image](./images/image_{replace_with_ref.count}.png)'

        md_content = re.sub(img_pattern, replace_with_ref, md_content)

    # 保存 markdown
    md_name = os.path.splitext(os.path.basename(file_path))[0] + ".md"
    md_path = os.path.join(output_dir, md_name)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"转换完成!")
    print(f"Markdown 文件: {md_path}")
    print(f"图片目录: {images_dir}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='使用 docling API 转换文档为 Markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python docling_convert.py 设计报告.pdf
  python docling_convert.py 文档.docx -o ./output
  python docling_convert.py "*.pdf"
        '''
    )
    parser.add_argument('input', help='要转换的文件路径（支持通配符）')
    parser.add_argument('-o', '--output', help='输出目录（默认：输入文件同目录下的同名文件夹）')
    parser.add_argument('-u', '--url', help='docling API 地址（默认：http://localhost:5001）')

    args = parser.parse_args()

    # 支持通配符
    input_pattern = args.input
    files = glob.glob(input_pattern)

    if not files:
        # 尝试作为单个文件处理
        files = [input_pattern]

    success_count = 0
    for file_path in files:
        if convert_file(file_path, args.output, args.url):
            success_count += 1

    print(f"\n总计: {len(files)} 个文件, {success_count} 个成功")

    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
