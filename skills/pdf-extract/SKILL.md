---
name: pdf-extract
description: >
  从用户上传的 PDF 抽取正文和表格，输出按页 Markdown。用户要读 PDF、导出文字、
  提取表格，或附件是 .pdf 且需要结构化文本时使用。不要用它生成 PPT。
compatibility: >
  需要 KnowSphere 技能沙箱（Docker）。镜像需能 import pypdf；
  缺少该包时脚本会做有限抽取，并在结果里说明。
metadata:
  author: knowsphere
  version: "1.0"
allowed-tools: execute_skill_script
---

# PDF 抽取

把 PDF 变成可阅读的 Markdown：按页正文，能识别的表格写成 Markdown 表。

## 何时使用

- 用户上传了 PDF，并要求提取文字、摘要、表格或复制内容
- 用户问「这个 PDF 里写了什么」，且附件尚未被当成纯文本用尽

不要用本技能：生成演示文稿（用 `generate_pptx`）、检索知识库文档（用 `doc_retrieval` 或 `list_chunks`）。

## 流程

1. 确认本轮 `/workspace/input` 中的 PDF 文件名（用户消息或附件列表）。
2. 运行抽取脚本：

   `scripts/extract_text.py`

   调用 `execute_skill_script`：

   - `skill_name`: `pdf-extract`
   - `script_path`: `scripts/extract_text.py`
   - `script_args`: 每个输入文件的容器路径，例如 `["/workspace/input/report.pdf"]`。不传则处理 `input/` 下全部 PDF
3. 脚本把结果写到 `$KNOWSPHERE_SKILL_OUTPUT_DIR/extracted.md`（即 `/workspace/output/extracted.md`）。stdout 为 JSON 摘要。
4. 根据抽取结果回答用户。工具未成功返回前，不要声称已经抽取。
