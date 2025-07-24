import os
import logging
from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import RedirectResponse
import httpx
from icalendar import Calendar, Event
from cachetools import TTLCache, cached
from opencc import OpenCC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ical-filter-sync")

app = FastAPI(title="iCal Filter Service Sync")

ORIGINAL_ICAL_URL = os.getenv("ORIGINAL_ICAL_URL", "https://www.hkex.com.hk/News/HKEX-Calendar/Subscribe-Calendar?sc_lang=zh-CN")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

cache: TTLCache[str, bytes] = TTLCache(maxsize=10, ttl=CACHE_TTL_SECONDS)

@cached(cache)
def fetch_ical(url: str) -> bytes:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            _ = resp.raise_for_status()
            return resp.content
    except httpx.RequestError as e:
        logger.error(f"HTTP request failed: {e}")
        raise
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e}")
        raise

def event_matches_keywords(event: Event, keywords: list[str]) -> bool:
    summary = str(event.get('SUMMARY', '')).lower() 
    description = str(event.get('DESCRIPTION', '')).lower() 
    return any(keyword.lower() in summary or keyword.lower() in description for keyword in keywords)

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/custom.ics", response_class=Response, responses={502: {"description": "Bad Gateway"}})
def filtered_ics(keywords: str = Query(default=None, description="Comma separated keywords to filter events"),debug:bool=Query(default=False, description="Enable debug mode")):
    filter_keywords = ["新上市"]

    if keywords:
        # 简体转换成繁体
        oc = OpenCC('s2t')
        filter_keywords = [oc.convert(k.strip()) for k in keywords.split(",") if k.strip()]    

        if debug:
            logger.info("Debug mode is on!")
        logger.info(f"Filtering events with keywords: {filter_keywords}")

    try:
        ical_data: bytes = fetch_ical(ORIGINAL_ICAL_URL)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch original iCal: {e}")

    try:
        cal:Calendar = Calendar.from_ical(ical_data)
    except Exception as e:
        logger.error(f"Failed to parse iCal data: {e}")
        raise HTTPException(status_code=502, detail="Failed to parse iCal data")

    X_WR_CALNAME = os.getenv("X_WR_CALNAME", "港股IPO日历")
    new_cal = Calendar()
    new_cal.add('METHOD','PUBLISH')
    new_cal.add('X-WR-CALNAME',X_WR_CALNAME)
    new_cal.add('X-WR-TIMEZONE','Asia/Hong_Kong')

    for event in cal.events:
        if event_matches_keywords(event, filter_keywords):
            new_cal.add_component(event)


    filtered_ical_bytes = new_cal.to_ical()
    if debug:
        return Response(content=filtered_ical_bytes, media_type="text/plain")
    return Response(content=filtered_ical_bytes, media_type="text/calendar")

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")

