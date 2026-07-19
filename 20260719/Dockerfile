# =============================================================================
#  Dockerfile pentru Render  ---  Simulator Loto 6/49 + Analizor pariuri fotbal
# =============================================================================
#  De ce Docker: Render nativ NU permite instalarea librariilor de sistem de
#  care are nevoie Chromium (root e blocat in build). Imaginea oficiala
#  Playwright are deja Chromium + toate librariile (libgtk, libnss, etc.),
#  deci extragerea din Flashscore merge garantat.
#
#  IMPORTANT: tag-ul imaginii (v1.61.0) trebuie sa fie ACELASI cu versiunea
#  playwright din requirements.txt (playwright==1.61.0). Daca schimbi una,
#  schimba si cealalta.
# =============================================================================

FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

# 1) Dependentele Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Binarul Chromium (librariile de sistem sunt deja in imagine).
#    Il rulam ca sa fim siguri ca binarul corespunde versiunii playwright.
RUN python -m playwright install chromium

# 3) Restul aplicatiei
COPY . .

# Render trimite portul prin variabila de mediu PORT; serverul o citeste deja.
ENV PORT=10000
EXPOSE 10000

# Pornire cu gunicorn, configurat pentru RAM mic (512 MB):
# --workers 1 --threads 2: minim de procese/thread-uri (fiecare worker in plus
#   ar dubla memoria). Extragerea foloseste oricum un singur browser pe rand.
# --timeout 180: extragerea + lansarea Chromium pe 0.1 CPU e lenta, marim timeout-ul.
# --max-requests 20: reciclam workerul periodic ca sa eliberam memoria acumulata.
CMD ["sh", "-c", "gunicorn server:app --bind 0.0.0.0:${PORT:-10000} --timeout 180 --workers 1 --threads 2 --max-requests 20 --max-requests-jitter 5"]
