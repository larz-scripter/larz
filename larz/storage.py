"""
larz.storage — file uploads + a pluggable storage backend, zero dependencies.

Multipart parsing lands on `req.files` (see larz.core); this module is where the
bytes go. LocalStorage writes to a directory and (optionally) serves them:

    from larz.storage import LocalStorage
    store = LocalStorage("uploads", url_prefix="/files")
    store.mount(app)                         # serves /files/<name>

    @app.post("/upload")
    def upload(req):
        f = req.files.get("avatar")
        if not f: return ("no file", 400)
        name = store.save(f)                 # returns a safe stored name
        return {"url": store.url(name)}

`UploadedFile` (from larz.core) exposes .filename, .content_type, .data (bytes),
.size and .save(path). A storage backend just needs save()/open()/url()/delete().
"""

import os
import re
import time
import secrets

__all__ = ["LocalStorage", "safe_name"]


def safe_name(filename):
    """A filesystem-safe basename (no paths, no surprises)."""
    base = os.path.basename(filename or "").strip() or "file"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base.lstrip(".")[:120] or "file"


class LocalStorage:
    def __init__(self, directory="uploads", url_prefix=None, keep_name=False):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self.url_prefix = (url_prefix or "").rstrip("/")
        self.keep_name = keep_name

    def _unique(self, name):
        stem, ext = os.path.splitext(name)
        return "%s-%s%s" % (stem, secrets.token_hex(6), ext) if not self.keep_name \
            else name

    def save(self, upload, name=None):
        """Persist an UploadedFile (or raw bytes with an explicit name).
        Returns the stored filename."""
        data = getattr(upload, "data", upload)
        stored = safe_name(name or getattr(upload, "filename", None) or
                           ("upload-%d" % int(time.time())))
        stored = self._unique(stored)
        full = os.path.join(self.directory, stored)
        with open(full, "wb") as f:
            f.write(data if isinstance(data, (bytes, bytearray)) else bytes(data))
        return stored

    def open(self, name, mode="rb"):
        return open(os.path.join(self.directory, safe_name(name)), mode)

    def path(self, name):
        return os.path.join(self.directory, safe_name(name))

    def exists(self, name):
        return os.path.isfile(self.path(name))

    def delete(self, name):
        try:
            os.remove(self.path(name))
            return True
        except OSError:
            return False

    def url(self, name):
        return "%s/%s" % (self.url_prefix, safe_name(name)) if self.url_prefix else name

    def mount(self, app):
        """Serve stored files at url_prefix (uses the app's static handler)."""
        if self.url_prefix:
            app.static(self.url_prefix, self.directory)
        return self
