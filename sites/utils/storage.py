"""Provide file upload path generation and storage cleanup utilities."""

import re
import logging
import os
import base64
import secrets
import threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from django.conf import settings
from django.db import transaction
from django.db.models import FileField
from sites.exceptions import UploadBusy
from django.utils.text import slugify
from sites.utils.constraints import MAX_STEM

logger = logging.getLogger(__name__)


_lock = threading.Lock()
_pool = None
_slots = None
_pool_pid = None


_TOKEN_SUFFIX = re.compile(r"\.[a-z2-7]{6}$")


FOLDERS = {
    "css": "css",
    "js": "js",
    "html": "html",
    **dict.fromkeys(["jpg", "jpeg", "png", "gif", "webp", "svg", "ico"], "images"),
    **dict.fromkeys(["mp4", "webm", "mov", "m4v", "ogv"], "videos"),
    **dict.fromkeys(["pdf", "doc", "docx"], "documents"),
}

def _key(owner):
    return owner.storage_key or str(owner.pk)

TOKEN_CHARS = 8


def make_storage_key(name, fallback):
    """`fallback` is used when the name gives an empty slug: "site", "theme" or "page"."""
    base = slugify(name or "").strip("-") or fallback
    return base

def short_token(chars=TOKEN_CHARS):
    """Random lowercase base32 text (a-z and 2-7): safe in URLs and file names."""
    raw = secrets.token_bytes((chars * 5 + 7) // 8)
    return base64.b32encode(raw).decode().lower().rstrip("=")[:chars]

def file_upload_path(instance, filename):
    """Generate the storage path for an uploaded file.

    Organizes files according to their model, owner, and file extension.
    Image and video assets receive a random token to avoid filename conflicts.

    Args:
        instance: Model instance associated with the uploaded file.
        filename: Original uploaded filename.

    Returns:
        str: Relative storage path for the uploaded file.

    Raises:
        ValueError: If the model type is not supported.
    """
    
    stem, ext = os.path.splitext(os.path.basename(filename))
    ext = ext.lower()
    folder = FOLDERS.get(ext.lstrip("."), "others")
    model = instance._meta.model_name

    if model in ("image", "video"):
        owner = (f"sites/{_key(instance.site)}" if instance.site_id else f"themes/{_key(instance.theme)}")

        stem = slugify(stem)[:MAX_STEM] or model
        return f"{owner}/{folder}/{stem}-{short_token(6)}{ext}"

    if model == "theme":
        owner = f"themes/{_key(instance)}"
    elif model == "site":
        owner = f"sites/{_key(instance)}"
    elif model == "page":
        owner = f"sites/{_key(instance.site)}/pages/{_key(instance)}"
    else:
        raise ValueError(f"file_upload_path: unsupported model '{model}'")

    stem = _TOKEN_SUFFIX.sub("", stem)[:MAX_STEM]
    return f"{owner}/{folder}/{stem}{ext}"


class StorageCleaner:
    """Provide utilities for deleting files from storage."""

    @staticmethod
    def delete_written_files(instance):
        """Delete committed files belonging to a new model instance.

        Args:
            instance: Model instance whose stored files should be deleted.
        """
        for field in instance._meta.concrete_fields:
            if not isinstance(field, FileField):
                continue

            file = getattr(instance, field.name)
            if file and getattr(file, "_committed", False):
                try:
                    file.storage.delete(file.name)
                except Exception:
                    logger.exception("Could not delete file %s", file.name)

    @staticmethod
    def delete_files(storage, name):
        """Delete a file from the specified storage backend.

        Args:
            storage: Storage backend responsible for the file.
            name: Storage-relative name of the file to delete.
        """
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Could not delete file %s", name)


class Ledger:
    """Track storage objects written by a single upload operation."""

    def __init__(self):
        """Initialize an empty ledger of storage objects."""
        self._entries = []
        self._lock = threading.Lock()

    def add(self, storage, name):
        """Record a storage object for subsequent cleanup.

        Args:
            storage: Storage backend containing the file.
            name: Storage-relative name of the file.
        """
        with self._lock:
            self._entries.append((storage, name))

    def delete_all(self):
        """Delete all recorded storage objects and clear the ledger."""
        with self._lock:
            entries = list(self._entries)
            self._entries.clear()

        for storage, name in entries:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not delete %s; left for the orphan sweep", name)



def _get_pool():
    """Get the upload thread pool, creating or recreating it when necessary.

    Returns:
        tuple: Thread pool executor and bounded semaphore controlling queue capacity.
    """
    global _pool, _slots, _pool_pid

    pid = os.getpid()
    if _pool is None or _pool_pid != pid:
        with _lock:
            if _pool is None or _pool_pid != pid:
                size = getattr(settings, "UPLOAD_POOL_SIZE", 0)
                queue = getattr(settings, "UPLOAD_QUEUE_SIZE", 32)
                _pool = ThreadPoolExecutor(
                    max_workers=size, thread_name_prefix="upload"
                )
                _slots = threading.BoundedSemaphore(size + queue)
                _pool_pid = pid

    return _pool, _slots


def _submit(fn, *args):
    """Submit a task to the upload pool while enforcing queue capacity.

    Args:
        fn: Callable to execute in a worker thread.
        *args: Positional arguments passed to the callable.

    Returns:
        Future: Future representing the submitted task.

    Raises:
        UploadBusy: If the upload queue remains full until the timeout.
    """
    pool, slots = _get_pool()
    timeout = getattr(settings, "UPLOAD_QUEUE_WAIT", 30)

    if not slots.acquire(timeout=timeout):
        raise UploadBusy()

    try:
        future = pool.submit(fn, *args)
    except BaseException:
        slots.release()
        raise

    future.add_done_callback(lambda _f: slots.release())
    return future



def _write(instance, field_name, ledger):
    """Write one uploaded file to storage without performing database operations.

    Args:
        instance: Model instance containing the uploaded file.
        field_name: Name of the model file field to write.
        ledger: Ledger tracking storage objects for cleanup.
    """
    field = instance._meta.get_field(field_name)
    upload = getattr(instance, field_name)
    storage = field.storage

    if hasattr(instance, "size"):
        instance.size = upload.size

    intended = field.generate_filename(instance, upload.name)
    ledger.add(storage, intended)

    actual = storage.save(intended, upload.file, max_length=field.max_length)
    if actual != intended:
        ledger.add(storage, actual)

    setattr(instance, field_name, actual)


def store_files(instances, field_name="file"):
    """Store uploaded files and return a ledger for failure cleanup.

    Args:
        instances: Model instances containing uploaded files.
        field_name: Name of the file field to store. Defaults to "file".
        workers: Number of workers to use. Defaults to the configured upload workers.

    Returns:
        Ledger: Ledger containing storage objects written by this operation.

    Raises:
        UploadBusy: If the upload queue is full.
        Exception: If any file fails to store.
    """
    ledger = Ledger()
    errors = []

    if getattr(settings, "UPLOAD_POOL_SIZE", 0) > 0 and len(instances) > 1:
        futures = []
        try:
            for instance in instances:
                futures.append(_submit(_write, instance, field_name, ledger))
        except Exception as exc:
            errors.append(exc)

        if not errors:
            wait(futures, return_when=FIRST_EXCEPTION)
        for future in futures:
            future.cancel()
        wait(futures)

        errors += [
            f.exception() for f in futures
            if not f.cancelled() and f.exception() is not None
        ]
    else:
        for instance in instances:
            try:
                _write(instance, field_name, ledger)
            except Exception as exc:
                errors.append(exc)
                break

    if errors:
        ledger.delete_all()
        raise errors[0]

    return ledger


def save_rows(instances, ledger):
    """Save uploaded file records in one transaction without storage operations.

    Args:
        instances: Model instances whose database records should be saved.
        ledger: Ledger tracking stored files for cleanup if saving fails.

    Raises:
        Exception: If a database operation fails.
    """
    try:
        with transaction.atomic():
            for instance in instances:
                instance.save()
    except Exception:
        ledger.delete_all()
        raise