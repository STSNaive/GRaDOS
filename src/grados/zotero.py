"""Zotero integration: save papers to Zotero library."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class ZoteroSaveResult:
    success: bool
    item_key: str = ""
    message: str = ""


async def save_to_zotero(
    doi: str,
    title: str,
    library_id: str,
    library_type: str,
    api_key: str,
    authors: list[str] | None = None,
    abstract: str = "",
    journal: str = "",
    year: str = "",
    url: str = "",
    tags: list[str] | None = None,
    collection_key: str = "",
) -> ZoteroSaveResult:
    """Save a paper to Zotero via the Web API."""
    if not all([library_id, api_key]):
        return ZoteroSaveResult(success=False, message="Zotero library_id and API key required")

    creators = []
    for author in (authors or []):
        parts = author.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
        else:
            creators.append({"creatorType": "author", "lastName": author})

    item = {
        "itemType": "journalArticle",
        "title": title,
        "DOI": doi,
        "creators": creators,
        "abstractNote": abstract,
        "publicationTitle": journal,
        "date": year,
        "url": url,
        "tags": [{"tag": t} for t in (tags or [])],
    }
    if collection_key:
        item["collections"] = [collection_key]

    endpoint = f"https://api.zotero.org/{library_type}s/{library_id}/items"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                endpoint,
                json=[item],
                headers={
                    "Zotero-API-Key": api_key,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            data = resp.json()

            if "successful" in data and "0" in data["successful"]:
                return ZoteroSaveResult(
                    success=True,
                    item_key=data["successful"]["0"].get("key", ""),
                    message="Saved to Zotero",
                )
            if "failed" in data and "0" in data["failed"]:
                return ZoteroSaveResult(
                    success=False,
                    message=data["failed"]["0"].get("message", "Unknown error"),
                )
            return ZoteroSaveResult(success=False, message=f"Unexpected response: {data}")

        except Exception as e:
            return ZoteroSaveResult(success=False, message=str(e))
