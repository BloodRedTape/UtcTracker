FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY web/ web/
COPY static/ static/
COPY main.py .

VOLUME ["/data"]

EXPOSE 8111

CMD ["python", "main.py", "/data"]
