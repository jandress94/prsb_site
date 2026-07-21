# PRSB Site

## Dev server

Load `env_vars/dev.env` and start the dev server:

```bash
set -a && source env_vars/dev.env && set +a && cd prsb && poetry run python manage.py runserver
```

First time (or after migrations):

```bash
set -a && source env_vars/dev.env && set +a && cd prsb && poetry run python manage.py migrate && poetry run python manage.py runserver
```

## Tests

Unit tests use an in-memory SQLite database (no Postgres required):

```bash
cd prsb && poetry run python manage.py test
```

To run only the band app tests:

```bash
cd prsb && poetry run python manage.py test band.tests
```
