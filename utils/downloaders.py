import asyncio
from pathlib import Path

import aiofiles
import aiohttp
from tqdm import tqdm

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://fapello.com/",
}
TIMEOUT = aiohttp.ClientTimeout(total=120, sock_connect=10)
CONCURRENCY = 6
MAX_RETRIES = 6


def media_seperation(path: Path) -> None:

    image_files = [
        filename
        for filename in path.iterdir()
        if filename.suffix in FILE_FORMATS["Images"]
    ]
    video_files = [
        video_file
        for video_file in path.iterdir()
        if video_file.suffix in FILE_FORMATS["Videos"]
    ]

    images_folder = Path(path / "Images")
    images_folder.mkdir(exist_ok=True)
    videos_folder = Path(path / "Videos")
    videos_folder.mkdir(exist_ok=True)

    for image in image_files:
        image.rename(images_folder / image.name)

    for video in video_files:
        video.rename(videos_folder / video.name)


class Downloader:
    def __init__(self, urls: list[str], folder: Path) -> None:
        self.urls = urls
        self.folder = folder

    def _filename(self, url: str) -> str:
        return url.split("/")[-1]

    def _existing_names(self) -> set[str]:
        names: set[str] = set()
        for directory in (
            self.folder,
            self.folder / "Images",
            self.folder / "Videos",
        ):
            if directory.is_dir():
                names.update(path.name for path in directory.iterdir() if path.is_file())
        return names

    async def download_file(
        self, session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore
    ) -> bytes | None:
        async with sem:
            for attempt in range(MAX_RETRIES):
                async with session.get(url) as response:
                    status = response.status
                    retry_after = response.headers.get("Retry-After", "")
                    if status == 200:
                        total = int(response.headers.get("Content-Length", 0))
                        downloaded = bytearray()
                        with tqdm(
                            total=total,
                            unit_scale=True,
                            unit="B",
                            leave=False,
                            desc=url,
                            disable=True,
                        ) as progress:
                            async for chunk, _ in response.content.iter_chunks():
                                progress.update(len(chunk))
                                downloaded.extend(chunk)
                        return downloaded
                    await response.read()

                if status in {429, 503} and attempt < MAX_RETRIES - 1:
                    wait = int(retry_after) if retry_after.isdigit() else 2 ** attempt
                    wait = min(max(wait, 1), 60)
                    print(
                        f"HTTP {status} {url} retry {attempt + 1}/{MAX_RETRIES} in {wait}s",
                        flush=True,
                    )
                    await asyncio.sleep(wait)
                    continue

                print(f"Skip {url}: HTTP {status}", flush=True)
                return None
            return None

    async def store_file(self, data: bytes, filename: str) -> None:
        async with aiofiles.open(self.folder / filename, mode="wb") as f:
            await f.write(data)

    async def download_and_store(
        self, session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore
    ) -> str | None:
        data = await self.download_file(session, url, sem)
        if not data:
            return None
        await self.store_file(data, self._filename(url))
        return url

    async def download_all(self) -> list[str]:
        self.folder.mkdir(parents=True, exist_ok=True)
        existing = self._existing_names()
        pending = [url for url in self.urls if self._filename(url) not in existing]
        skipped = len(self.urls) - len(pending)
        if skipped:
            print(f"Skipping {skipped} files already on disk.", flush=True)
        if not pending:
            print("Nothing to download.", flush=True)
            return []

        print(f"Downloading {len(pending)} files ({CONCURRENCY} at a time).", flush=True)
        saved: list[str] = []
        sem = asyncio.Semaphore(CONCURRENCY)
        async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
            coros = [
                self.download_and_store(session, link, sem) for link in pending
            ]
            for func in tqdm(
                asyncio.as_completed(coros), total=len(coros), desc="Processing"
            ):
                result = await func
                if result:
                    saved.append(result)
        print(f"Saved {len(saved)}/{len(pending)} files.", flush=True)
        return saved
