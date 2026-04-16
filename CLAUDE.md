# CLAUDE.md

Project guidance for AI/code assistants.

## Project
- Python CLI scraper for US school STEM teacher contacts.
- Main entrypoint: `main.py`
- Core modules: `crawler.py`, `parser.py`, `enricher.py`, `email_finder.py`, `exporter.py`, `config.py`

## Local setup
```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium
cp .env.example .env
```

Set `HACKCLUB_AI_KEY` in `.env`.

## Run
Single site:
```bash
./venv/bin/python -u main.py "https://www.cvsdvt.org/" -o output.csv
```

Multiple sites:
```bash
./venv/bin/python -u main.py "https://www.cvsdvt.org/" "https://sbhs.sbschools.net/" -o demo_combined.csv
```

From file:
```bash
./venv/bin/python -u main.py --file urls.txt -o combined.csv
```

## Output expectations
- Export only STEM teachers.
- Keep only concrete email statuses: `found`, `verified`, `matched`.

## Notes
- Do not commit `.env`, `venv/`, or generated CSV/log files.
- Preserve current CLI behavior and Hack Club AI API usage.
