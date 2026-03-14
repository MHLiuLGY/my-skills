---
name: docling-convert
description: 使用 docling API 批量转换文档为 Markdown 并提取图片。适用于 docx、pdf、pptx、xlsx 等格式转换为 markdown，保留文档中的图片和表格。
---

# Docling Convert

使用本地部署的 docling 服务批量转换文档为 Markdown 并提取图片。

## 依赖安装

使用前需要安装依赖（默认用户已经安装）：

```bash
pip install requests
```

确保 docling 服务已本地部署，默认为 `http://localhost:5001`

## 基本 API 调用

```python
import requests
import base64

DOCLING_API_URL = "http://localhost:5001/v1/convert/source"

# 读取文件并转换为 base64
with open('doc.docx', 'rb') as f:
    file_content = f.read()
base64_content = base64.b64encode(file_content).decode('utf-8')

# 构建请求 - 关键：使用 embedded 模式
payload = {
    "sources": [{
        "kind": "file",
        "filename": "doc.docx",
        "base64_string": base64_content
    }],
    "options": {
        "to_formats": ["md"],
        "image_export_mode": "embedded",  # 关键：embedded 模式返回 base64 图片
        "do_ocr": True,                 # 启用 OCR
        "table_mode": "accurate"        # 表格模式
    }
}

response = requests.post(DOCLING_API_URL, json=payload, timeout=300)
result = response.json()

# 提取 markdown 内容
md_content = result['document']['md_content']
```

## 提取图片并替换引用

```python
import re
import base64
import os

# 正则匹配 base64 图片
img_pattern = r'!\[Image\]\(data:image/([^;]+);base64,([^)]+)\)'
matches = re.findall(img_pattern, md_content)

if matches:
    images_dir = "./doc_images"
    os.makedirs(images_dir, exist_ok=True)

    # 保存图片文件
    for idx, (img_type, b64_data) in enumerate(matches):
        img_bytes = base64.b64decode(b64_data)
        with open(f"{images_dir}/image_{idx}.png", 'wb') as f:
            f.write(img_bytes)

    # 使用闭包函数替换引用（保留递增索引）
    def replace_with_ref(match):
        replace_with_ref.count = getattr(replace_with_ref, 'count', -1) + 1
        return f'![Image](./doc_images/image_{replace_with_ref.count}.png)'

    md_content = re.sub(img_pattern, replace_with_ref, md_content)

# 保存 markdown 文件
with open('doc.md', 'w', encoding='utf-8') as f:
    f.write(md_content)
```

## 支持的文件格式

- `.docx` - Word 文档
- `.pdf` - PDF 文件
- `.pptx` - PowerPoint
- `.xlsx` - Excel
- `.html` - 网页
- `.md` - Markdown
- `.csv` - 表格
- `.png`, `.jpg`, `.jpeg` - 图片

## API 选项说明

| 选项 | 值 | 说明 |
|-----|-----|-----|
| `image_export_mode` | `embedded` | 返回 base64 图片数据 |
| `image_export_mode` | `referenced` | 仅返回引用（无实际数据） |
| `image_export_mode` | `placeholder` | 使用占位符 |
| `table_mode` | `accurate` | 精确模式（推荐） |
| `table_mode` | `fast` | 快速模式 |
| `ocr_engine` | `easyocr` | 默认 OCR 引擎 |

## 适用场景

- 批量转换 docx/pdf/pptx/xlsx 为 markdown
- 需要保留文档中的图片
- 使用本地部署的 docling 服务
- 文档包含截图、表格等复杂内容

## 注意事项

1. **referenced 模式问题**: 该模式不会返回实际图片数据，适合直接查看，不适合批量处理
2. **embedded 模式**: 图片以 base64 编码返回，需要额外处理才能保存为文件
3. **大文件处理**: 增加 timeout 值避免超时
4. **API 端点**: 本地服务默认为 `http://localhost:5001/v1/convert/source`
