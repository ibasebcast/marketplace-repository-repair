FROM python:3.13-alpine

WORKDIR /app
# Pin and verify the shared repair engine used by the standalone CLI.
ADD https://raw.githubusercontent.com/ibasebcast/marketplace-repository-repair/dd9ee926e1f218a8207b39aec58493a3b5a93b97/repair.py /app/repair.py
RUN echo "c208be1c18e44e0ca118ee43bda412979fa353db7b740e0f88f6657cccea447e  /app/repair.py" | sha256sum -c -
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/app.py"]
