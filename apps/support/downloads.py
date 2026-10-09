"""Store official installers for repeatable Telegram delivery."""

import os
import tempfile
from urllib.parse import urlparse, unquote

import requests
from django.core.files import File

MAX_TELEGRAM_UPLOAD = 49 * 1024 * 1024


class InstallerTooLarge(Exception):
    pass


def prepare_installer(item):
    if item.telegram_file_id:
        return item
    if item.file:
        if item.file.size > MAX_TELEGRAM_UPLOAD:
            raise InstallerTooLarge()
        if item.file.storage.exists(item.file.name):
            return item
    if not item.installer_url or item.platform in {"iphone", "macos"}:
        return item
    with requests.get(item.installer_url, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        size = int(response.headers.get("Content-Length", 0))
        if size > MAX_TELEGRAM_UPLOAD:
            raise InstallerTooLarge()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            raise ValueError("Installer URL returned an HTML page")
        name = os.path.basename(unquote(urlparse(item.installer_url).path))
        with tempfile.TemporaryFile() as stream:
            written = 0
            for chunk in response.iter_content(1024 * 1024):
                written += len(chunk)
                if written > MAX_TELEGRAM_UPLOAD:
                    raise InstallerTooLarge()
                stream.write(chunk)
            if not written:
                raise ValueError("Installer is empty")
            stream.seek(0)
            item.file.save(name, File(stream), save=False)
        item.telegram_file_id = ""
        item.save(update_fields=["file", "telegram_file_id", "updated_at"])
    return item
