"""Packaged, offline-readable troubleshooting knowledge."""
from importlib.resources import files
import json


def list_articles():
    root = files("match_mobile_diagnostics").joinpath("knowledge")
    entries = json.loads(root.joinpath("index.json").read_text())
    return [dict(entry, body=root.joinpath(entry["id"] + ".md").read_text()) for entry in entries]


def get_article(article_id):
    for article in list_articles():
        if article["id"] == article_id:
            return article
    raise KeyError(article_id)
