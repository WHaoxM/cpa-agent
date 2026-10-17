# CPA-Agent Backend Dockerfile

# This candidate's base is the locally verified Linux/amd64 digest, not a
# floating tag. A registry re-pull and provenance check remain release gates.
FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置工作目录
WORKDIR /app

# Candidate hash lock includes the CPU-only PyTorch wheel and the complete
# resolved dependency set. A clean hash-checked install is still a release gate.
COPY requirements-linux-amd64-py310.lock ./

# Keep the wheel cache across interrupted builds. Hashes reject substitutions;
# source builds and unpinned dependencies are rejected.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes --only-binary=:all: \
    --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-linux-amd64-py310.lock

# 复制应用代码
COPY . .

# Only the runtime data paths are writable. New named volumes inherit these
# ownerships; existing root-owned volumes require an explicit maintenance move.
RUN groupadd --gid 10001 cpa \
    && useradd --uid 10001 --gid cpa --home-dir /home/cpa --create-home \
       --shell /usr/sbin/nologin cpa \
    && mkdir -p /app/uploads /app/logs /app/milvus_data \
       /app/modelscope_cache /app/embeddings_cache \
    && chown -R 10001:10001 /home/cpa /app/uploads /app/logs \
       /app/milvus_data /app/modelscope_cache /app/embeddings_cache
ENV HOME=/home/cpa
USER 10001:10001

# 暴露端口
EXPOSE 5001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=5)"]

# Standalone runs must use the same fail-closed config/schema gate as Compose.
# Compose may override only process sizing, never fall back to Flask's dev server.
CMD ["gunicorn", "--bind=0.0.0.0:5001", "--workers=2", "--worker-class=gthread", "--threads=4", "--timeout=180", "--graceful-timeout=60", "--access-logfile=-", "--error-logfile=-", "production_wsgi:create_production_app()"]
