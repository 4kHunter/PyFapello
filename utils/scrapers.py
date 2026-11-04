import asyncio
import logging
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from niquests.exceptions import ChunkedEncodingError, ConnectionError as NiquestsConnectionError, HTTPError, Timeout

from .http_client import BaseHttpClient, CloudflareChallengeError

logger = logging.getLogger(__name__)


class FappeloScraper(BaseHttpClient):
    BASE_URL = "https://fapello.com/ajax/model/{name}/"

    def __init__(
        self,
        link: str,
        timeout: float = 30,
        max_connections: int = 10,
    ) -> None:
        super().__init__(timeout=timeout, max_connections=max_connections)
        self.link = link

    async def fetch_urls(self) -> list[str]:
        image_urls: dict[str, None] = {}
        video_urls: dict[str, None] = {}
        visited_video_pages: set[str] = set()

        async for page_url, soup in self.iter_pages():
            image_urls.update(dict.fromkeys(self.get_image_urls(soup, page_url)))

            video_pages = self.get_video_pages(soup, page_url)
            new_video_pages = sorted(video_pages - visited_video_pages)

            results = await asyncio.gather(*(self.fetch_video_urls_safely(url) for url in new_video_pages))

            for urls in results:
                video_urls.update(dict.fromkeys(urls))

            visited_video_pages.update(new_video_pages)

        return list(dict.fromkeys([*image_urls, *video_urls]))

    async def iter_pages(self) -> AsyncIterator[tuple[str, BeautifulSoup]]:
        base_url = self.BASE_URL.format(name=self.get_model_name())
        page = 1
        seen_pages: set[tuple[tuple[str, str], ...]] = set()

        while True:
            url = f"{base_url}page-{page}/"
            html = await self.get_response(url)

            if not html.strip():
                return

            soup = self.get_soup(html)

            signature = tuple(
                (str(anchor["href"]), str(image["src"]))
                for anchor in soup.find_all("a", href=True)
                for image in anchor.find_all("img", src=True)
            )

            if not signature or signature in seen_pages:
                return

            seen_pages.add(signature)

            yield url, soup
            page += 1

    async def fetch_video_urls_safely(self, page_url: str) -> list[str]:
        try:
            return await self.fetch_video_urls(page_url)
        except (
            CloudflareChallengeError,
            HTTPError,
            ChunkedEncodingError,
            NiquestsConnectionError,
            Timeout,
            TimeoutError,
        ) as exc:
            logger.warning("Skipping video page %s: %s", page_url, exc)
            return []

    async def fetch_video_urls(self, page_url: str) -> list[str]:
        html = await self.get_response(page_url)
        soup = self.get_soup(html)

        return [urljoin(page_url, str(source["src"])) for source in soup.find_all("source", src=True)]

    def get_image_urls(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        urls: list[str] = []

        for anchor in soup.find_all("a", href=True):
            if anchor.select_one('img[src*="icon-play"]') is not None:
                continue

            image = anchor.find("img", src=True)
            if image is None:
                continue

            src = str(image["src"])
            urls.append(urljoin(base_url, src))

        return urls

    def get_video_pages(self, soup: BeautifulSoup, base_url: str) -> set[str]:
        return {
            urljoin(base_url, str(anchor["href"]))
            for anchor in soup.find_all("a", href=True)
            if anchor.select_one('img[src*="icon-play"]') is not None
        }

    def get_model_name(self) -> str:
        return urlparse(self.link).path.strip("/")

    def get_soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")
