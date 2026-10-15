"""Use Werkzeug's upload stream hook for the durable resume intake route.

Only this route is redirected. The normal Flask multipart limits and parser
remain authoritative; no second upload parser or global tempfile override.
"""

import os
from tempfile import SpooledTemporaryFile

from flask import Request

from .config import Config


class CareerUploadRequest(Request):
    def _get_file_stream(self, total_content_length, content_type,
                         filename=None, content_length=None):
        if self.path != "/api/profile/student/upload":
            return super()._get_file_stream(total_content_length, content_type,
                                            filename, content_length)
        # Werkzeug's default stream spills at 500 KiB into the system temp
        # directory. Keep the same spool threshold, but place this route's
        # spill on the same volume as its durable upload files.
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        return SpooledTemporaryFile(max_size=1024 * 500, mode="w+b",
                                    dir=Config.UPLOAD_FOLDER)
