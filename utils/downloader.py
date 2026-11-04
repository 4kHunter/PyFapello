import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiofiles.threadpool import wrap
from niquests.exceptions import ChunkedEncodingError, ConnectionError as NiquestsConnectionError, HTTPError, Timeout
from tqdm import tqdm

from .http_client import BaseHttpClient

FILE_FORMATS = {
    "Images": {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".jpe",
        ".svg",
        ".tif",
        ".tiff",
        ".jif",
    },
    "Videos": {
        ".mpeg",
        ".avchd",
        ".webm",
        ".mpv",
        ".swf",
        ".avi",
        ".m4p",
        ".wmv",
        ".mp2",
        ".m4v",
        ".qt",
        ".mpe",
        ".mp4",
        ".flv",
        ".mov",
        ".mpg",
        ".ogg",
    },
}


def retry[**P, T](
    attempts: int = 3,
    delay: float = 1,
    exceptions: tuple[type[Exception], ...] = (OSError,),
) -> Callable[
    [Callable[P, Coroutine[Any, Any, T]]],
    Callable[P, Coroutine[Any, Any, T]],
]:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    if delay < 0:
        raise ValueError("delay must be non-negative")

    def decorator(
        func: Callable[P, Coroutine[Any, Any, T]],
    ) -> Callable[P, Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            for _ in range(attempts - 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions:
                    await asyncio.sleep(delay)

            return await func(*args, **kwargs)

        return wrapper

    return decorator


class Downloader(BaseHttpClient):
    def __init__(
        self,
        urls: list[str],
        folder: Path,
        timeout: float = 30,
        max_connections: int = 10,
    ) -> None:
        super().__init__(timeout=timeout, max_connections=max_connections)
        self.urls = list(dict.fromkeys(urls))
        self.folder = folder
        self._destinations: dict[str, Path] = {}
        self._downloads: dict[str, asyncio.Task[None]] = {}

    def get_destination(self, url: str) -> Path:
        filename = Path(urlparse(url).path).name

        if filename in {"", ".", ".."}:
            raise ValueError(f"Missing filename in URL: {url}")

        suffix = Path(filename).suffix.lower()

        for category, extensions in FILE_FORMATS.items():
            if suffix in extensions:
                return self.folder / category / filename

        return self.folder / filename

    @retry(
        attempts=3,
        delay=4,
        exceptions=(
            ChunkedEncodingError,
            NiquestsConnectionError,
            Timeout,
            asyncio.TimeoutError,
        ),
    )
    async def download_file(self, url: str, destination: Path) -> None:
        if self._session is None:
            raise RuntimeError("Use the downloader inside 'async with'")

        await asyncio.to_thread(
            destination.parent.mkdir,
            parents=True,
            exist_ok=True,
        )
        temporary = destination.with_name(f"{destination.name}.part")

        try:
            async with await self._session.get(
                url,
                timeout=self.timeout,
                stream=True,
            ) as response:
                if response.headers.get("cf-mitigated") == "challenge":
                    raise RuntimeError(f"Cloudflare challenge: {url}")

                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type:
                    raise RuntimeError(f"Expected media, received HTML: {url}")

                # Opened synchronously: aiofiles.open leaks the descriptor if cancelled while opening.
                raw_file = temporary.open("wb")
                file = wrap(raw_file, loop=asyncio.get_running_loop())

                try:
                    async for chunk in await response.iter_content(
                        chunk_size=256 * 1024,
                    ):
                        await file.write(chunk)
                finally:
                    await file.close()

            await asyncio.to_thread(temporary.replace, destination)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    def get_candidate_urls(self, original_url: str) -> list[str]:
        parsed = urlparse(original_url)
        path = Path(parsed.path)

        if "_300px" in path.name and path.suffix.lower() in FILE_FORMATS["Images"]:
            full_size_path = str(path.with_name(path.name.replace("_300px", "")))
            full_size_url = parsed._replace(path=full_size_path).geturl()
            return [full_size_url, original_url]

        return [original_url]

    def plan_destinations(self) -> None:
        reserved: set[Path] = set()
        destinations: dict[str, Path] = {}

        for url in self.urls:
            for candidate in self.get_candidate_urls(url):
                if candidate in destinations:
                    continue

                try:
                    base = self.get_destination(candidate)
                except ValueError:
                    continue

                destination = self.reserve_destination(base, reserved)
                destinations[candidate] = destination

        self._destinations = destinations

    @staticmethod
    def reserve_destination(base: Path, reserved: set[Path]) -> Path:
        destination = base
        counter = 1

        while destination in reserved or destination.with_name(f"{destination.name}.part") in reserved:
            destination = base.with_name(f"{base.stem}_{counter}{base.suffix}")
            counter += 1

        reserved.update({destination, destination.with_name(f"{destination.name}.part")})
        return destination

    def download_candidate(self, url: str) -> asyncio.Task[None]:
        task = self._downloads.get(url)

        if task is None:
            task = asyncio.create_task(self._download_to_destination(url))
            self._downloads[url] = task

        return task

    async def _download_to_destination(self, url: str) -> None:
        destination = self._destinations[url] if url in self._destinations else self.get_destination(url)
        await self.download_file(url, destination)

    async def download_media(self, original_url: str) -> str:
        *fallbacks, final_url = self.get_candidate_urls(original_url)

        for url in fallbacks:
            try:
                await asyncio.shield(self.download_candidate(url))
            except HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
            else:
                return url

        await asyncio.shield(self.download_candidate(final_url))
        return final_url

    async def download_all(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("Use the downloader inside 'async with'")

        self.plan_destinations()
        self._downloads = {}

        tasks = [asyncio.create_task(self.download_media(url)) for url in self.urls]
        downloaded_urls: list[str] = []

        try:
            with tqdm(total=len(tasks), desc="Downloading", unit="file") as progress:
                for task in asyncio.as_completed(tasks):
                    try:
                        downloaded_url = await task
                    except Exception as exc:
                        tqdm.write(f"Download failed: {exc}")
                    else:
                        downloaded_urls.append(downloaded_url)
                    finally:
                        progress.update(1)
        finally:
            pending = [*tasks, *self._downloads.values()]

            for task in pending:
                task.cancel()

            await asyncio.gather(*pending, return_exceptions=True)

        return list(dict.fromkeys(downloaded_urls))
