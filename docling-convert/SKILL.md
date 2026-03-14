---
name: docling-convert
description: 使用 docling API 批量转换文档为 Markdown 并提取图片。适用于 docx、pdf、pptx、xlsx 等格式转换为 markdown，保留文档中的图片和表格。
---

# Docling Convert

使用本地部署的 docling 服务批量转换文档为 Markdown 并提取图片。

## 环境配置

确保 docling 服务已本地部署，默认为 `http://localhost:5001`

安装依赖：
```bash
pip install requests
```

## 使用方法

### 基本用法

```bash
# 转换 PDF 文件
python "scripts/docling_convert.py" 设计报告.pdf

# 转换 Word 文档
python "scripts/docling_convert.py" 文档.docx

# 指定输出目录
python "scripts/docling_convert.py" 设计报告.pdf -o 输出目录

# 批量转换（支持通配符）
python "scripts/docling_convert.py" "*.pdf"

# 自定义 API 地址
python "scripts/docling_convert.py" 文档.docx -u http://192.168.1.100:5001
```

### 在 skills 目录执行

```bash
cd "C:\Users\12556\Desktop\cc\skills\docling-convert"
python "scripts/docling_convert.py" 设计报告.pdf
```

### 参数说明

| 参数 | 说明 |
|-----|-----|
| `输入文件` | 要转换的文件路径（支持 pdf, docx, pptx, xlsx 等） |
| `-o, --output` | 输出目录（默认：输入文件同目录下的同名文件夹） |
| `-u, --url` | docling API 地址（默认：http://localhost:5001） |

## 输出结果

转换后会生成：
- `{文件名}.md` - 转换后的 Markdown 文件
- `images/` 文件夹 - 提取的图片文件

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

## 注意事项

1. **referenced 模式问题**: 该模式不会返回实际图片数据，适合直接查看，不适合批量处理
2. **embedded 模式**: 图片以 base64 编码返回，需要额外处理才能保存为文件
3. **大文件处理**: 增加 timeout 值避免超时
4. **API 端点**: 本地服务默认为 `http://localhost:5001/v1/convert/source`
