FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py inspectors.py nli.py quest_chains.py source_scan.py source_repairs.py training_audit.py report_publication.py ./
EXPOSE 3050
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3050"]



