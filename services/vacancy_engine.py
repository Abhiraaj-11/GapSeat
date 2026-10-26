class ChartFormatError(RuntimeError):
    pass


def find_vacancies(
    chart: dict,
    from_station: str,
    to_station: str,
    travel_class: str,
) -> dict:
    """
    Expected normalized chart structure:

    {
        "stations": ["NDLS", "CNB", "PRYJ"],
        "coaches": [
            {
                "coach_no": "S4",
                "class": "SL",
                "berths": [
                    {
                        "number": 32,
                        "type": "LB",
                        "segments": [
                            {
                                "from_station": "NDLS",
                                "to_station": "CNB",
                                "occupied": False
                            }
                        ]
                    }
                ]
            }
        ]
    }

    The scraper/parser must produce this structure from real data.
    """

    stations = chart.get("stations")
    coaches = chart.get("coaches")

    if not isinstance(stations, list) or not isinstance(coaches, list):
        raise ChartFormatError("The chart has an unsupported data format.")

    station_index = {
        str(code).strip().upper(): i
        for i, code in enumerate(stations)
    }

    if from_station not in station_index:
        raise ValueError(f"Station {from_station} was not found in the train route.")

    if to_station not in station_index:
        raise ValueError(f"Station {to_station} was not found in the train route.")

    start = station_index[from_station]
    end = station_index[to_station]

    if start >= end:
        raise ValueError("Destination must occur after boarding on this train.")

    matches = []

    for coach in coaches:
        if coach.get("class", "").upper() != travel_class.upper():
            continue

        for berth in coach.get("berths", []):
            segments = berth.get("segments", [])

            # Each interval is identified by its position in the train route.
            # Missing interval data means we cannot verify this berth.
            occupancy_by_interval = {}

            for segment in segments:
                segment_from = str(
                    segment.get("from_station", "")
                ).strip().upper()
                segment_to = str(
                    segment.get("to_station", "")
                ).strip().upper()

                if (
                    segment_from not in station_index
                    or segment_to not in station_index
                ):
                    continue

                a = station_index[segment_from]
                b = station_index[segment_to]

                if b != a + 1:
                    continue

                occupied = segment.get("occupied")
                if isinstance(occupied, bool):
                    occupancy_by_interval[a] = occupied

            # Every consecutive interval must be explicitly verified vacant.
            required_intervals = range(start, end)
            if not all(
                occupancy_by_interval.get(i) is False
                for i in required_intervals
            ):
                continue

            matches.append({
                "coach_no": coach.get("coach_no", "Unknown"),
                "berth_number": berth.get("number", "Unknown"),
                "berth_type": berth.get("type", "Unknown"),
                "from_station": from_station,
                "to_station": to_station,
            })

    return {
        "train_number": chart.get("train_number", ""),
        "train_name": chart.get("train_name", ""),
        "retrieved_at": chart.get("retrieved_at"),
        "from_station": from_station,
        "to_station": to_station,
        "travel_class": travel_class,
        "vacancies": matches,
        "count": len(matches),
    }