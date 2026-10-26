import asyncio
import logging
import re
import sys
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "irctc-vacancy-mcp" / "src"

# Make modules inside src importable
sys.path.insert(0, str(SRC))


from irctc_api import (  # noqa: E402
    train_composition,
    train_schedule,
    all_coaches_async,
)
from vacancy_filter import filter_vacant  # noqa: E402

app = FastAPI(title="GapSeat", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    train_no: str = Field(min_length=5, max_length=5)
    travel_date: date
    boarding: str = Field(min_length=2, max_length=10)
    destination: str = Field(min_length=2, max_length=10)
    class_code: str = ""


def station_code(value):
    """Normalize station fields returned by the upstream service."""
    if isinstance(value, dict):
        value = (
            value.get("stationCode")
            or value.get("stnCode")
            or value.get("code")
            or value.get("station")
            or ""
        )
    return str(value or "").strip().upper()


def _seat_key(item):
    return (
        item.get("classCode", item.get("class_code")),
        item.get("coachName", item.get("coach")),
        str(item.get("berth", item.get("seat_number"))),
    )


def _deduplicate_vacancies(vacancies):
    unique = {}
    for item in vacancies:
        key = (
            item["classCode"],
            item["coachName"],
            str(item["berth"]),
            item["freeFrom"],
            item["freeTo"],
        )
        unique[key] = item
    return list(unique.values())


def _build_seat_change_itinerary(
    vacancies, station_list, boarded_from, travel_to
):
    start = station_list.index(boarded_from)
    end = station_list.index(travel_to)
    choices_by_hop = []

    for hop in range(start, end):
        choices = []
        for item in vacancies:
            free_start = station_list.index(item["freeFrom"])
            free_end = station_list.index(item["freeTo"])
            if free_start <= hop and free_end >= hop + 1:
                choices.append(item)
        if not choices:
            return []
        choices_by_hop.append(choices)

    # Keep the lowest-change path to each available seat at each hop.
    paths = {}
    for hop_number, choices in enumerate(choices_by_hop):
        next_paths = {}
        for item in choices:
            key = _seat_key(item)
            if hop_number == 0:
                next_paths[key] = (0, [item])
                continue

            for previous_key, (changes, path) in paths.items():
                candidate = (
                    changes + (previous_key != key),
                    path + [item],
                )
                current = next_paths.get(key)
                if current is None or candidate[0] < current[0]:
                    next_paths[key] = candidate
        paths = next_paths

    if not paths:
        return []

    _, selected_hops = min(paths.values(), key=lambda result: result[0])
    itinerary = []
    for hop_number, item in enumerate(selected_hops):
        hop_start = start + hop_number
        hop_end = hop_start + 1
        key = _seat_key(item)
        if itinerary and _seat_key(itinerary[-1]) == key:
            itinerary[-1]["to_station"] = station_list[hop_end]
            continue

        itinerary.append({
            "coach": item["coachName"],
            "class_code": item["classCode"],
            "seat_number": str(item["berth"]),
            "seat_label": (
                "Seat" if item["classCode"] in {"CC", "EC", "2S"}
                else "Berth"
            ),
            "seat_type": item["berthCode"] or "Unspecified",
            "from_station": station_list[hop_start],
            "to_station": station_list[hop_end],
        })
    return itinerary


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/search")
async def search_vacancy(payload: SearchRequest):
    train_no = payload.train_no.strip()
    boarding = payload.boarding.strip().upper()
    destination = payload.destination.strip().upper()
    jdate = payload.travel_date.isoformat()
    class_filter = payload.class_code.strip().upper() or None

    if not re.fullmatch(r"\d{5}", train_no):
        return {"ok": False, "error": "Enter a valid 5-digit train number."}

    if boarding == destination:
        return {
            "ok": False,
            "error": "Boarding and destination stations must be different.",
        }

    try:
        # Fetch chart composition and station route concurrently.
        composition, station_list = await asyncio.gather(
            asyncio.to_thread(train_composition, train_no, jdate, boarding),
            asyncio.to_thread(train_schedule, train_no),
        )

        if not isinstance(composition, dict) or composition.get("error"):
            raise RuntimeError(
                str(composition.get("error", "Could not load train composition."))
                if isinstance(composition, dict)
                else "Invalid train composition response."
            )

        coaches = composition.get("cdd") or []
        if not coaches:
            return {
                "ok": False,
                "error": (
                    "No coach information was returned. The chart may not be "
                    "prepared, the train/date may be incorrect, or IRCTC "
                    "may be temporarily unavailable."
                ),
            }

        station_list = [station_code(s) for s in station_list]
        station_list = [s for s in station_list if s]

        if not station_list:
            try:
                from train_schedule import _fetch_confirmtkt_schedule

                schedule = await asyncio.to_thread(
                    _fetch_confirmtkt_schedule, train_no
                )
                station_list = [
                    station_code(stop)
                    for stop in schedule
                    if station_code(stop)
                ]
            except Exception:
                logger.exception(
                    "Could not load fallback route for train %s", train_no
                )

        if not station_list:
            return {
                "ok": False,
                "error": (
                    "The train route could not be loaded. Please retry later; "
                    "vacancy results cannot be calculated safely without it."
                ),
            }

        # Validate that both stations occur in the returned route.
        if boarding not in station_list or destination not in station_list:
            return {
                "ok": False,
                "error": (
                    "Boarding or destination station was not found in the "
                    "train's route. Check the station codes and journey order."
                ),
            }

        if station_list.index(boarding) >= station_list.index(destination):
            return {
                "ok": False,
                "error": "The destination must come after the boarding station.",
            }

        remote = station_code(composition.get("remote"))
        source = station_code(composition.get("from"))
        train_start_date = (
            composition.get("trainStartDate") or jdate
        )

        # Restrict coach requests to the selected class, if provided.
        if class_filter:
            coaches = [
                coach for coach in coaches
                if str(coach.get("classCode") or "").upper() == class_filter
            ]

        coach_summary = [
            {
                "coach": str(coach.get("coachName") or "Coach not listed"),
                "class_code": str(coach.get("classCode") or "").upper(),
                "vacant_berths": coach.get("vacantBerths", 0),
            }
            for coach in coaches
        ]

        if not coaches:
            return {
                "ok": True,
                "train_name": composition.get("trainName", train_no),
                "train_no": train_no,
                "travel_date": jdate,
                "boarding": boarding,
                "destination": destination,
                "coach_summary": coach_summary,
                "results": [],
                "message": "No coaches were returned for the selected class.",
            }

        # Reuse the existing scraper; it limits concurrent requests.
        coach_data = await all_coaches_async(
            train_no=train_no,
            jdate=jdate,
            boarding=boarding,
            remote=remote,
            source=source,
            train_start_date=str(train_start_date),
            coaches=coaches,
            concurrency=5,
            chart_mode="current",
        )

        direct_vacancies = _deduplicate_vacancies(filter_vacant(
            all_coach_data=coach_data,
            boarded_from=boarding,
            travel_to=destination,
            class_filter=class_filter,
            station_list=station_list,
        ))
        segment_vacancies = _deduplicate_vacancies(filter_vacant(
            all_coach_data=coach_data,
            boarded_from=boarding,
            travel_to=destination,
            class_filter=class_filter,
            station_list=station_list,
            include_partial_windows=True,
        ))

        # Only return fields required by the web interface.
        results = [
            {
                "coach": item["coachName"],
                "class_code": item["classCode"],
                "seat_number": str(item["berth"]),
                "seat_label": (
                    "Seat" if item["classCode"] in {"CC", "EC", "2S"}
                    else "Berth"
                ),
                "seat_type": item["berthCode"] or "Unspecified",
                "free_from": item["freeFrom"],
                "free_to": item["freeTo"],
            }
            for item in direct_vacancies
        ]
        itinerary = []
        if not results:
            itinerary = _build_seat_change_itinerary(
                segment_vacancies, station_list, boarding, destination
            )

        return {
            "ok": True,
            "train_name": composition.get("trainName", train_no),
            "train_no": train_no,
            "travel_date": jdate,
            "boarding": boarding,
            "destination": destination,
            "coach_summary": coach_summary,
            "results": results,
            "itinerary": itinerary,
            "message": (
                f"{len(results)} seat(s) or berth(s) are chart-listed for "
                "the full journey."
                if results
                else (
                    f"No single seat covers the full journey. A possible "
                    f"seat-change route with {len(itinerary)} segment(s) is "
                    "shown below."
                    if itinerary
                    else "No continuous seat or berth option was found in the chart data."
                )
            ),
        }

    except PermissionError:
        return {
            "ok": False,
            "error": (
                "IRCTC denied the request (401/403). Its security system may "
                "be blocking this connection. Try again later from your own "
                "network; do not repeatedly retry."
            ),
        }
    except Exception as exc:
        # Log the technical error on the server, not in the public response.
        logger.exception("Vacancy search failed for train %s", train_no)
        return {
            "ok": False,
            "error": (
                "Could not retrieve live chart data. IRCTC may be unavailable, "
                "blocking the connection, or returning an unexpected response. "
                "Please try again later."
            ),
        }