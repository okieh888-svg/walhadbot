# Option Docker (le mode recommandé sous Windows reste le venv + Planificateur de tâches).
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Dans le conteneur, Chrome n'est pas installé : on utilise le Chromium fourni par l'image.
ENV NAVIGATEUR_CANAL= HEADLESS=1 PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
