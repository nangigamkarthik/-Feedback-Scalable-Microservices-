FROM python:3.11-slim

WORKDIR /app
COPY . /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "services.gateway.app"]
