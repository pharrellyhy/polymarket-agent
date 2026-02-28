"""Data models for news items."""

from pydantic import BaseModel


class NewsItem(BaseModel):
    """A single news article or headline."""

    title: str
    url: str = ""
    published: str = ""
    summary: str = ""
