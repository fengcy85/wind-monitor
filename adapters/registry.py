"""Explicit per-source adapter loader and public-page parser policy.

Every source has its own module in adapters/sites. No hidden API, CAPTCHA bypass,
logins or claims of verified coverage. Shared transport/date libraries are reused.
"""
from __future__ import annotations
import importlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

SITE_ID = re.compile(r"^(cn|intl)_[a-f0-9]{12}$")

@dataclass(frozen=True)
class SiteAdapter:
    config: dict

    @property
    def id(self): return self.config["source_id"]

    @property
    def strategy(self): return self.config["strategy"]

    @property
    def list_selectors(self): return self.config["list_selectors"]

    @property
    def date_selectors(self): return self.config["date_selectors"]

    @property
    def body_selectors(self): return self.config["body_selectors"]

    def entry_urls(self):
        """Uses only configured official entry URLs, never guesses private endpoints."""
        return list(dict.fromkeys(self.config["entry_urls"]))

    def validate_source(self, source):
        if self.id != source["id"]: raise ValueError("adapter ID mismatch")
        if self.config["official_url"] != source["url"]: raise ValueError("adapter URL mismatch")

    def is_site_specific(self):
        return bool(self.config["list_selectors"] and self.config["body_selectors"] and self.config["official_url"])


def load_adapter(source):
    source_id = source["id"]
    if not SITE_ID.fullmatch(source_id): raise ValueError("invalid source adapter ID")
    obj = importlib.import_module("adapters.sites." + source_id).build()
    obj.validate_source(source)
    return obj
