import asyncio
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://fapello.com/",
}


def _fetch_sync(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", "replace")
            if status != 200:
                raise RuntimeError(f"HTTP {status} from {url} ({body[:200]!r})")
            return body
    except urllib.error.HTTPError as exc:
        snippet = exc.read()[:200].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(f"HTTP {exc.code} from {url} ({snippet!r})") from exc


class FappeloScraper:
    BASE_URL: str = "https://fapello.com/ajax/model/{name}/"

    def __init__(self, link: str) -> None:
        self.link = link

    def get_model_name(self) -> str:
        return urlparse(self.link).path.strip("/")

    def get_soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    async def get_response(self, url: str) -> str:
        return await asyncio.to_thread(_fetch_sync, url)

    async def fetch_urls(self) -> list[str]:
        video_urls: list[str] = []
        image_urls: list[str] = []
        page = 1

        while True:
            model_name = self.get_model_name()
            ajax_url = f"{self.BASE_URL.format(name=model_name)}page-{page}/"
            print(f"Fetching page {page}: {ajax_url}", flush=True)
            response = await self.get_response(ajax_url)

            if not response.strip():
                print(f"Empty page {page}, done scraping.", flush=True)
                break

            soup = self.get_soup(response)

            image_anchors = {
                img["src"].replace("_300px", "")
                for img in soup.findAll("img")
                if img.get("src") and "icon-play" not in img["src"]
            }

            icon_anchors = {
                anchors["href"]
                for anchors in soup.findAll("a", href=True)
                for img in anchors.findAll("img")
                if img.get("src") and "icon-play" in img["src"]
            }

            if not image_anchors and not icon_anchors:
                print(
                    f"Page {page} had HTML but no media (likely a block page). Stopping.",
                    flush=True,
                )
                break

            print(
                f"Page {page}: {len(image_anchors)} images, {len(icon_anchors)} video posts",
                flush=True,
            )
            image_urls.extend(list(image_anchors))

            if icon_anchors:
                response_videos = await asyncio.gather(
                    *[self.get_response(url) for url in icon_anchors]
                )
                video_soups = [self.get_soup(response) for response in response_videos]

                video_anchors = {
                    video["src"]
                    for video_soup in video_soups
                    for video in video_soup.findAll("source")
                    if video.get("src")
                }

                video_urls.extend(list(video_anchors))

            page += 1

        print(
            f"Found {len(image_urls)} images and {len(video_urls)} videos.",
            flush=True,
        )
        return image_urls + video_urls
