FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV PAPER_BIND=0.0.0.0 PAPER_NO_BROWSER=1
EXPOSE 8765
CMD ["python", "serve.py"]
