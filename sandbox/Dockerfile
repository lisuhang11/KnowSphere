FROM python:3.12-slim

# 可选技能运行时镜像：默认 SKILL_SANDBOX_IMAGE=python:3.12-slim 不含第三方库。
# 构建：docker build -t knowsphere-skill:latest sandbox
# 使用：SKILL_SANDBOX_IMAGE=knowsphere-skill:latest
RUN pip install --no-cache-dir "pypdf>=5,<6"

WORKDIR /workspace
