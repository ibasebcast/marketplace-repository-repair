FROM python:3.13-alpine

WORKDIR /app
# Pin and verify the shared repair engine used by the standalone CLI.
ADD https://raw.githubusercontent.com/ibasebcast/marketplace-repository-repair/8d097e537afbfd53900083187f3df254576af94a/repair.py /app/repair.py
RUN echo "fad2ee24eb1a8321f2e0b2103603df1887373ef66106562b577a55c2157648c0  /app/repair.py" | sha256sum -c -
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/app.py"]
