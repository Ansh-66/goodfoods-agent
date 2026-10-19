FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN useradd --create-home app

# Editable install keeps data/ next to the package, where config.ROOT expects it.
COPY pyproject.toml README.md ./
COPY goodfoods ./goodfoods
RUN pip install -e .

COPY streamlit_app.py ./
COPY data/restaurants.json ./data/
RUN chown -R app /app
USER app

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# Seed bookings relative to today on every start, so the demo-full outlets are full now.
CMD ["sh", "-c", "python -m goodfoods.setup_db && streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=8501 --browser.gatherUsageStats=false"]
