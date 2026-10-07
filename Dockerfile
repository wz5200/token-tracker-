FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py analyzer.py chains.py ./
COPY static ./static
# Hugging Face Spaces (Docker) 要求监听 7860；本地/Render 可用 PORT 环境变量覆盖
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "gunicorn -w 2 --timeout 120 -b 0.0.0.0:${PORT:-7860} app:app"]
