"""First-run setup webapp: configure .env + TLS certs, then hand off to admin.

The package is split into small pieces:

* :mod:`lsm.setup.env_file`  -- read/merge/write ``.env`` preserving comments.
* :mod:`lsm.setup.schema`    -- the editable variable set, cert + path helpers.
* :mod:`lsm.setup.app`       -- the FastAPI JSON API + static SPA (port 8989).
"""

from __future__ import annotations
