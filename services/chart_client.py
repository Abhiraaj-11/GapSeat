class ChartUnavailable(RuntimeError):
    """The train chart could not be retrieved or parsed."""


class ChartClient:
    def __init__(self):
        pass

    async def fetch_chart(
        self,
        train_number: str,
        journey_date: str,
    ) -> dict:
        """
        Connect this method to the existing irctc-vacancy-mcp client.

        Expected return value:
            A normalized chart structure containing:
            - ordered stations
            - coach/class information
            - berth-level occupancy intervals

        Do not return an empty chart when a request fails.
        Do not invent vacant berths.
        """
        raise ChartUnavailable(
            "Live chart integration is not connected yet. "
            "Connect the existing MCP chart client first."
        )