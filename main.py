import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlparse

from utils.database import Database
from utils.downloader import Downloader
from utils.scrapers import FappeloScraper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download galleries of images/videos from supported sites",
    )
    parser.add_argument("-l", "--link", required=True, help="Profile link")
    parser.add_argument("-f", "--folder", required=True, help="Download folder")
    parser.add_argument(
        "-s",
        "--save",
        action="store_true",
        help="Record successfully downloaded URLs in the database",
    )

    return parser.parse_args()


def resolve_folder(folder: str) -> Path:
    path = Path(folder).expanduser()

    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    return path


def save_links(urls: list[str]) -> None:
    with Database() as database:
        database.insert_many(urls)


async def main() -> None:
    args = parse_args()

    scrapers = {
        "fapello.com": FappeloScraper,
        # "fapello.ru": FappeloRuScraper,
    }

    parsed_url = urlparse(args.link)
    scraper_class = scrapers.get(parsed_url.hostname or "")

    if parsed_url.scheme not in {"http", "https"} or scraper_class is None or not parsed_url.path.strip("/"):
        raise ValueError(f"Invalid profile link: {args.link}")

    async with scraper_class(args.link) as scraper:
        urls = await scraper.fetch_urls()

    if not urls:
        print("No media found.")
        return

    folder = await asyncio.to_thread(resolve_folder, args.folder)

    async with Downloader(urls, folder) as downloader:
        downloaded_urls = await downloader.download_all()

    if args.save and downloaded_urls:
        await asyncio.to_thread(save_links, downloaded_urls)


if __name__ == "__main__":
    asyncio.run(main())
