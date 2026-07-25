# Larz app — production image. The app is a plain WSGI callable, so any WSGI
# server (gunicorn, uWSGI, waitress) runs it. Larz itself has zero deps.
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir larz gunicorn
ENV LARZ_ENV=production
EXPOSE 8000
# app.py must expose `app` (the Larz instance)
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "app:app"]
