# PyFapello

A Python script for downloading images and videos from fapello.com galleries.

## Requirements

- Python 3.14 or newer
- uv or pip

## Installation

Clone the repository and enter the project directory:

```bash
git clone https://github.com/4kHunter/PyFapello.git
cd PyFapello
```

### Using uv

Install the dependencies:

```bash
uv sync
```

### Using pip

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Download a gallery using uv:

```bash
uv run python main.py -l https://fapello.com/profile-name/ -f downloads
```

Or, with your virtual environment activated:

```bash
python main.py -l https://fapello.com/profile-name/ -f downloads
```

To also record successfully downloaded URLs in SQLite, add `-s`:

```bash
uv run python main.py -l https://fapello.com/profile-name/ -f downloads -s
```

### Arguments

| Argument         | Required | Description                                   |
| ---------------- | -------- | --------------------------------------------- |
| `-l`, `--link`   | Yes      | Profile URL                                   |
| `-f`, `--folder` | Yes      | Download directory                            |
| `-s`, `--save`   | No       | Record successfully downloaded URLs in SQLite |

Relative download directories are resolved against the directory containing
`main.py`. Absolute paths are also supported.

## Features

- Downloads images and videos.
- Stores images in `Images/` and videos in `Videos/`.
- Downloads concurrently with up to 10 connections per host.
- Tries full-size images first and falls back to the original thumbnail on HTTP 404.
- Retries selected network errors and temporary HTTP failures.
- Continues downloading other files when an individual download fails.
- Displays download progress.
- Gives distinct URLs with colliding filenames unique names within a download run.

## Database

With `--save`, successfully downloaded URLs are recorded in `fapello.db`
in the current working directory.

The database stores the selected full-size or thumbnail URL. Failed downloads
are not recorded, and duplicate database entries are ignored.

The database does not currently filter future downloads. Running the script
again downloads the gallery again and may replace existing files.

## Development

Install development dependencies:

```bash
uv sync --dev
```

Run tests with statement and branch coverage:

```bash
uv run pytest
```

Run lint, formatting and type checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright --pythonpath .venv/bin/python
```

Tests use mocks and a local HTTP server without downloading from the live site.

After changing runtime dependencies, regenerate `requirements.txt`:

```bash
uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt
```
