FROM python:3.12-slim

# LibreOffice + 日本語フォント + poppler
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libreoffice-writer \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体
COPY app.py .
COPY templates/ templates/
COPY scripts/ scripts/

# 作業ディレクトリ作成
RUN mkdir -p uploads converted

# Renderは$PORTを動的に割り当てる
ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --threads 8"]
