import argparse
import asyncio
from pathlib import Path
from urllib.parse import urlparse

from utils.downloaders import Downloader, media_seperation
from utils.scrapers import FappeloScraper

__version__ = "1.0.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simple Script written in Python for downloading galleries of images/videos from fapello",
        epilog="Enjoy!!!",
    )
    parser.add_argument("-l", "--link", help="fapello link", required=True)
    parser.add_argument("-f", "--folder", required=True, help="Folder name")
    parser.add_argument(
        "-s", "--save", action="store_true", help="Save the links to the database"
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    netlocs = {"fapello.com": FappeloScraper}

    parse_url = urlparse(args.link)
    if parse_url.netloc not in netlocs:
        raise ValueError(f"Invalid link: {args.link}")

    scraper = netlocs[parse_url.netloc](args.link)

    if args.save:
        urls = await scraper.save_to_database()
    else:
        urls = await scraper.fetch_urls()

    folder_path = Path(__file__).resolve().parent
    downloader = Downloader(urls, folder_path / Path(args.folder))
    await downloader.download_all()
    media_seperation(downloader.folder)


if __name__ == "__main__":
    asyncio.run(main())
