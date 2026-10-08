"""Thin GCP access layer used by the CLI (infra/) and the policy handlers.

REST (google-auth AuthorizedSession) everywhere an API exists; `gcloud.py` is only used for
Cloud Build / Cloud Run deploys and as a last resort. See docs/ARCHITECTURE.md for the verified calls.
"""
from .rest import GcpError, Rest, rest  # noqa: F401
